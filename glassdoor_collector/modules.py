"""Glassdoor 模块采集器：Pay & Benefits / Interviews / Jobs（公司岗位）

复用 infra.py 的反限流与 GraphQL 基础设施。

Jobs（公司招聘岗位）：
- 使用 JobsSearchAndroid 查询，pageTypeEnum=SERP + searchParams.filterParams
  [{filterKey: employerId, values: <eid>}]，可纯净返回单公司岗位。
- 分页用 pageNumber 递增（cursor 方式服务端报错）。SERP 相关性排序存在跨页
  重复，故用 (listing_id, employer_id) 唯一索引去重，并在连续多页无新增时提前结束。

用法示例：
    uv run glassdoor-modules --modules benefits,interviews,jobs --workers 4
    uv run glassdoor-modules --modules jobs --max-employers 10 --workers 1
"""
import argparse
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .infra import (
    fetch_graphql, fp_rotator, rate_limiter, rotator,
)

from .config import (
    MODULES_FLUSH_SIZE as FLUSH_SIZE,
    MODULES_MAX_PAGE_RETRIES as MAX_PAGE_RETRIES,
    MODULES_MAX_PAGES_PER_EMPLOYER as MAX_PAGES_PER_EMPLOYER,
    MODULES_STATS_INTERVAL as STATS_INTERVAL,
    MODULES_WORKERS as WORKERS,
)
from .db import get_conn, init_all_tables, put_conn
from psycopg2.extras import execute_values

log = logging.getLogger("modules")

# ---------------------------------------------------------------------------
# 全局统计
# ---------------------------------------------------------------------------
_stats_lock = threading.Lock()
_stats = {"req_ok": 0, "req_fail": 0, "docs": 0,
          "employers_done": 0, "start": time.time()}


# ---------------------------------------------------------------------------
# GraphQL 查询模板（从 APK 提取）
# ---------------------------------------------------------------------------
BENEFITS_QUERY = (
    "query EmployerBenefits($employerId: Int!, $countryId: Int!, $benefitsReviewsPageNumber: Int!, "
    "$benefitsReviewsPageSize: Int!, $employmentStatus: EmploymentStatusEnum) { "
    "benefitsOverviewForCountry(benefitsInput: { employerId: $employerId countryId: $countryId employmentStatus: $employmentStatus } ) { "
    "employerBenefitSummary { comment } overallBenefitRating totalBenefitReviews "
    "benefitsCategoryToStatisticAggregates { benefitCategory { id name } "
    "benefitStatisticAggregateList { benefit { id name } benefitRatingDenominator benefitRatingNumerator verified } } } "
    "countriesForEmployerBenefits(employerId: $employerId) { id name } "
    "employmentStatusEnumsForBenefitReviews(employerId: $employerId, countryId: $countryId) "
    "overviewBenefitReviews(benefitsInput: { employmentStatus: $employmentStatus countryId: $countryId employerId: $employerId "
    "page: { size: $benefitsReviewsPageSize num: $benefitsReviewsPageNumber } } ) { "
    "__typename ...EmployerBenefitsReviewsFragment } } "
    "fragment EmployerBenefitsReviewsFragment on BenefitReview { id rating createDate currentJob userEnteredJobTitle "
    "city { name state { name } metro { name } country { name } } benefitComments { helpfulVotes comment id } }"
)

INTERVIEWS_QUERY = (
    "query EmployerInterviewsList($employerId: Int!, $difficulties: [InterviewDifficultyLevelEnum], $gocId: GOCIdent, "
    "$location: LocationIdent, $jobTitle: JobTitleIdent, $outcomes: [InterviewOutcomeEnum], $page: Int!, $pageSize: Int!, "
    "$sort: InterviewsSortOrderEnum!) { "
    "employerInterviewsList: employerInterviewsIG(employerInterviewsInput: { employer: { id: $employerId } "
    "difficulties: $difficulties goc: $gocId jobTitle: $jobTitle location: $location outcomes: $outcomes "
    "page: { num: $page size: $pageSize } sort: $sort } ) { "
    "interviews { __typename ...EmployerInterviewFragment } filteredInterviewCount totalNumberOfPages queryJobTitle { mgocId } "
    "employer { primaryIndustryId } } } "
    "fragment EmployerInterviewFragment on InterviewIG { advice countHelpful difficulty id experience employer { name squareLogoUrl } "
    "employerResponses { response responseDateTime } featured jobTitle { text } negotiationDescription outcome processDescription "
    "reviewDateTime source userQuestions { question answerCount } }"
)

JOBS_QUERY = (
    "query JobsSearchAndroid($adSlotName: String, $pageTypeEnum: PageTypeEnum, $searchParams: SearchParams, "
    "$onlyCurrentGlassdoorAwards: Boolean! = true, $blcAwardsLimit: Int! = 30, $bptwAwardsLimit: Int! = 30) { "
    "jobListings(contextHolder: { adSlotName: $adSlotName pageTypeEnum: $pageTypeEnum searchParams: $searchParams } ) { "
    "jobListings { jobview { __typename ...JobViewFragment } } "
    "searchResultsMetadata { searchCriteria { keyword location { id name } } "
    "jobAlert { jobAlertId emailFrequencyEnumId } } "
    "paginationCursors { __typename ...PaginationCursorFragment } companyFilterOptions { id shortName } "
    "filterOptions totalJobsCount } } "
    "fragment GlassdoorAwards on Employer { bestLedCompanies(limit: $blcAwardsLimit, onlyCurrent: $onlyCurrentGlassdoorAwards) { "
    "id isCurrent name timePeriod rank } bestPlacesToWork(limit: $bptwAwardsLimit, onlyCurrent: $onlyCurrentGlassdoorAwards) { "
    "id isCurrent listType name timePeriod rank } } "
    "fragment JobViewFragment on JobView { job { listingId jobTitleText } header { adOrderId ageInDays applied appliedSource "
    "easyApply expired goc locId locationName locationType normalizedJobTitle employerNameFromSearch employer { __typename name "
    "squareLogoUrl id ...GlassdoorAwards } payPeriod payPeriodAdjustedPay { p90 p50 p10 } occupations { key } rating salarySource "
    "savedJobId isSponsoredJob payCurrency jobViewUrl jobCountryId jobResultTrackingKey } overview { primaryIndustry { industryId "
    "industryName sectorId sectorName } } gaTrackerData { requiresTracking trackingUrl } } "
    "fragment PaginationCursorFragment on PaginationCursor { cursor pageNumber }"
)

# ---------------------------------------------------------------------------
# 数据转换（保持原 dict 格式，flush 时映射到 PG 列）
# ---------------------------------------------------------------------------

def transform_benefit_review(r: dict, employer_id: int, employer_name: str,
                             country_id: int) -> dict[str, Any]:
    city = r.get("city") or {}
    state = city.get("state") or {}
    metro = city.get("metro") or {}
    country = city.get("country") or {}
    comments = r.get("benefitComments") or []
    return {
        "benefitReviewId": r.get("id"),
        "employerId": employer_id,
        "employerName": employer_name,
        "countryId": country_id,
        "type": "review",
        "rating": r.get("rating"),
        "createDate": r.get("createDate"),
        "currentJob": r.get("currentJob"),
        "userEnteredJobTitle": r.get("userEnteredJobTitle"),
        "cityName": city.get("name"),
        "stateName": state.get("name"),
        "metroName": metro.get("name"),
        "countryName": country.get("name"),
        "benefitComments": [
            {"id": c.get("id"), "helpfulVotes": c.get("helpfulVotes"),
             "comment": c.get("comment")}
            for c in comments
        ],
        "collectedAt": datetime.now(timezone.utc),
    }


def transform_interview(r: dict, employer_id: int, employer_name: str,
                        page: int) -> dict[str, Any]:
    emp = r.get("employer") or {}
    jt = r.get("jobTitle") or {}
    return {
        "interviewId": r.get("id"),
        "employerId": employer_id,
        "employerName": employer_name,
        "page": page,
        "advice": r.get("advice"),
        "countHelpful": r.get("countHelpful"),
        "difficulty": r.get("difficulty"),
        "experience": r.get("experience"),
        "employerNameDetail": emp.get("name"),
        "employerSquareLogoUrl": emp.get("squareLogoUrl"),
        "employerResponses": [
            {"response": er.get("response"),
             "responseDateTime": er.get("responseDateTime")}
            for er in (r.get("employerResponses") or [])
        ],
        "featured": r.get("featured", False),
        "jobTitle": jt.get("text"),
        "negotiationDescription": r.get("negotiationDescription"),
        "outcome": r.get("outcome"),
        "processDescription": r.get("processDescription"),
        "reviewDateTime": r.get("reviewDateTime"),
        "source": r.get("source"),
        "userQuestions": [
            {"question": q.get("question"), "answerCount": q.get("answerCount")}
            for q in (r.get("userQuestions") or [])
        ],
        "collectedAt": datetime.now(timezone.utc),
    }


def transform_job(jv: dict, employer_id: int, employer_name: str,
                  page: int) -> dict[str, Any]:
    job = jv.get("job") or {}
    header = jv.get("header") or {}
    overview = jv.get("overview") or {}
    emp = header.get("employer") or {}
    industry = overview.get("primaryIndustry") or {}
    adj = header.get("payPeriodAdjustedPay") or {}
    return {
        "listingId": job.get("listingId"),
        "employerId": employer_id,
        "employerName": employer_name,
        "page": page,
        "jobTitleText": job.get("jobTitleText"),
        "normalizedJobTitle": header.get("normalizedJobTitle"),
        "locationName": header.get("locationName"),
        "locationType": header.get("locationType"),
        "locId": header.get("locId"),
        "jobCountryId": header.get("jobCountryId"),
        "ageInDays": header.get("ageInDays"),
        "applied": header.get("applied"),
        "easyApply": header.get("easyApply"),
        "expired": header.get("expired"),
        "isSponsoredJob": header.get("isSponsoredJob"),
        "payPeriod": header.get("payPeriod"),
        "payCurrency": header.get("payCurrency"),
        "payP10": adj.get("p10"),
        "payP50": adj.get("p50"),
        "payP90": adj.get("p90"),
        "rating": header.get("rating"),
        "salarySource": header.get("salarySource"),
        "goc": header.get("goc"),
        "jobViewUrl": header.get("jobViewUrl"),
        "employerNameFromSearch": header.get("employerNameFromSearch"),
        "employerLogoUrl": emp.get("squareLogoUrl"),
        "primaryIndustryId": industry.get("industryId"),
        "primaryIndustryName": industry.get("industryName"),
        "sectorId": industry.get("sectorId"),
        "sectorName": industry.get("sectorName"),
        "collectedAt": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# 通用请求封装
# ---------------------------------------------------------------------------
def fetch_module_page(operation: str, body: dict, employer_id: int,
                      retries: int = MAX_PAGE_RETRIES) -> tuple[int, dict]:
    """返回 (status, data)。status 含义与 fetch_graphql 一致。"""
    for attempt in range(retries + 1):
        rate_limiter.acquire()
        rate_limiter.maybe_ramp_up()
        status, data = fetch_graphql(operation, body, timeout=60)
        if status == 200:
            with _stats_lock:
                _stats["req_ok"] += 1
            return 200, data
        with _stats_lock:
            _stats["req_fail"] += 1
        if status == -1:
            log.warning("permanent fail op=%s eid=%s", operation, employer_id)
            return -1, {}
        log.warning("retry op=%s eid=%s attempt=%d status=%s",
                    operation, employer_id, attempt, status)
        if attempt < retries:
            time.sleep(min(2 ** attempt, 30))
    return 0, {}


# ---------------------------------------------------------------------------
# dict → PG row 映射函数
# ---------------------------------------------------------------------------
def _benefit_doc_to_row(doc: dict) -> tuple:
    return (
        doc["benefitReviewId"],
        doc["employerId"],
        doc.get("employerName", ""),
        doc.get("countryId"),
        doc.get("type", "review"),
        doc.get("rating"),
        doc.get("createDate"),
        doc.get("currentJob"),
        doc.get("userEnteredJobTitle"),
        doc.get("cityName"),
        doc.get("stateName"),
        doc.get("metroName"),
        doc.get("countryName"),
        json.dumps(doc.get("benefitComments") or []),
        doc.get("comment"),
        doc.get("overallBenefitRating"),
        doc.get("totalBenefitReviews", 0),
        json.dumps(doc.get("benefitsCategoryToStatisticAggregates") or []),
        doc.get("collectedAt"),
    )


def _interview_doc_to_row(doc: dict) -> tuple:
    return (
        doc["interviewId"],
        doc["employerId"],
        doc.get("employerName", ""),
        doc.get("page", 0),
        doc.get("advice"),
        doc.get("countHelpful", 0),
        doc.get("difficulty"),
        doc.get("experience"),
        doc.get("employerNameDetail"),
        doc.get("employerSquareLogoUrl"),
        json.dumps(doc.get("employerResponses") or []),
        doc.get("featured", False),
        doc.get("jobTitle"),
        doc.get("negotiationDescription"),
        doc.get("outcome"),
        doc.get("processDescription"),
        doc.get("reviewDateTime"),
        doc.get("source"),
        json.dumps(doc.get("userQuestions") or []),
        doc.get("collectedAt"),
    )


def _job_doc_to_row(doc: dict) -> tuple:
    return (
        doc["listingId"],
        doc["employerId"],
        doc.get("employerName", ""),
        doc.get("page", 0),
        doc.get("jobTitleText"),
        doc.get("normalizedJobTitle"),
        doc.get("locationName"),
        doc.get("locationType"),
        doc.get("locId"),
        doc.get("jobCountryId"),
        doc.get("ageInDays"),
        doc.get("applied"),
        doc.get("easyApply"),
        doc.get("expired"),
        doc.get("isSponsoredJob"),
        doc.get("payPeriod"),
        doc.get("payCurrency"),
        doc.get("payP10"),
        doc.get("payP50"),
        doc.get("payP90"),
        doc.get("rating"),
        doc.get("salarySource"),
        doc.get("goc"),
        doc.get("jobViewUrl"),
        doc.get("employerNameFromSearch"),
        doc.get("employerLogoUrl"),
        doc.get("primaryIndustryId"),
        doc.get("primaryIndustryName"),
        doc.get("sectorId"),
        doc.get("sectorName"),
        doc.get("collectedAt"),
    )


# ---------------------------------------------------------------------------
# 模块采集器基类
# ---------------------------------------------------------------------------
BENEFIT_COLUMNS = (
    "benefit_review_id", "employer_id", "employer_name", "country_id", "type",
    "rating", "create_date", "current_job", "user_entered_job_title",
    "city_name", "state_name", "metro_name", "country_name",
    "benefit_comments", "comment", "overall_benefit_rating",
    "total_benefit_reviews", "benefits_category_aggregates", "collected_at",
)

INTERVIEW_COLUMNS = (
    "interview_id", "employer_id", "employer_name", "page",
    "advice", "count_helpful", "difficulty", "experience",
    "employer_name_detail", "employer_logo_url", "employer_responses",
    "featured", "job_title", "negotiation_desc", "outcome",
    "process_description", "review_date_time", "source",
    "user_questions", "collected_at",
)

JOB_COLUMNS = (
    "listing_id", "employer_id", "employer_name", "page",
    "job_title_text", "normalized_job_title", "location_name", "location_type",
    "loc_id", "job_country_id", "age_in_days", "applied", "easy_apply",
    "expired", "is_sponsored_job", "pay_period", "pay_currency",
    "pay_p10", "pay_p50", "pay_p90", "rating", "salary_source",
    "goc", "job_view_url", "employer_name_from_search", "employer_logo_url",
    "primary_industry_id", "primary_industry_name", "sector_id", "sector_name",
    "collected_at",
)


class BaseModuleCollector:
    name: str = ""
    out_table: str = ""
    progress_table: str = ""
    columns: tuple = ()
    doc_to_row: Callable = lambda doc: ()
    page_size: int = 20
    operation: str = ""
    id_field_name: str = ""

    def __init__(self, max_employers: int | None = None,
                 workers: int = WORKERS, only_with_reviews: bool = True):
        init_all_tables()
        self.max_employers = max_employers
        self.workers = workers
        self.only_with_reviews = only_with_reviews
        self.q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()

    def build_task(self, employer_id: int, employer_name: str,
                   resume_doc: dict | None) -> dict:
        raise NotImplementedError

    def collect_employer(self, task: dict, add_doc: Callable[[dict], None]) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Progress helpers
    # ------------------------------------------------------------------
    def _init_progress(self, employer_id: int, employer_name: str,
                       total_pages: int | None = None, ctx: dict | None = None):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {self.progress_table}
                            (employer_id, employer_name, status, next_page,
                             collected, failed_pages, total_pages, ctx, started_at, updated_at)
                        VALUES (%s, %s, 'in_progress', 1, 0, '{{}}'::int[],
                                %s, %s, %s, %s)
                        ON CONFLICT (employer_id) DO UPDATE SET
                            employer_name = EXCLUDED.employer_name,
                            status = 'in_progress',
                            started_at = EXCLUDED.started_at,
                            updated_at = EXCLUDED.updated_at""",
                    (employer_id, employer_name, total_pages,
                     json.dumps(ctx or {}),
                     datetime.now(timezone.utc), datetime.now(timezone.utc)))
                conn.commit()
        finally:
            put_conn(conn)

    def _mark_page(self, employer_id: int, page: int, n: int):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {self.progress_table}
                            (employer_id, next_page, collected, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (employer_id) DO UPDATE SET
                            next_page = EXCLUDED.next_page,
                            collected = {self.progress_table}.collected + EXCLUDED.collected,
                            updated_at = EXCLUDED.updated_at""",
                    (employer_id, page + 1, n, datetime.now(timezone.utc)))
                conn.commit()
        finally:
            put_conn(conn)

    def _mark_done(self, employer_id: int, status: str):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {self.progress_table}
                            (employer_id, status, done_at, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (employer_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            done_at = EXCLUDED.done_at,
                            updated_at = EXCLUDED.updated_at""",
                    (employer_id, status, datetime.now(timezone.utc),
                     datetime.now(timezone.utc)))
                conn.commit()
        finally:
            put_conn(conn)
        with _stats_lock:
            _stats["employers_done"] += 1

    def _mark_error(self, employer_id: int, page: int):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {self.progress_table}
                            (employer_id, failed_pages, status, updated_at)
                        VALUES (%s, %s::int[], 'error', %s)
                        ON CONFLICT (employer_id) DO UPDATE SET
                            failed_pages = {self.progress_table}.failed_pages || %s::int,
                            status = 'error',
                            updated_at = %s
                        WHERE NOT (%s::int = ANY({self.progress_table}.failed_pages))""",
                    (employer_id, [page], datetime.now(timezone.utc),
                     page, datetime.now(timezone.utc), page))
                conn.commit()
        finally:
            put_conn(conn)

    # ------------------------------------------------------------------
    # Seeder: 从 PG 读取雇主列表 + 断点续传
    # ------------------------------------------------------------------
    def seeder(self):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                done_statuses = "('done', 'done_empty', 'done_with_errors')"
                cur.execute(
                    f"SELECT employer_id FROM {self.progress_table} "
                    f"WHERE status IN {done_statuses}")
                done_ids = {row[0] for row in cur.fetchall()}

                cur.execute(
                    f"SELECT employer_id, next_page, ctx FROM {self.progress_table} "
                    f"WHERE status = 'in_progress'")
                in_progress = {
                    row[0]: {"nextPage": row[1], "ctx": row[2] or {}}
                    for row in cur.fetchall()
                }
        finally:
            put_conn(conn)

        log.info("[%s] seeder: %d done, %d in_progress",
                 self.name, len(done_ids), len(in_progress))

        # 续传 in_progress
        for eid, p in in_progress.items():
            conn2 = get_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM employers WHERE employer_id = %s", (eid,))
                    row = cur.fetchone()
                    ename = row[0] if row else ""
            finally:
                put_conn(conn2)
            self.q.put(self.build_task(eid, ename, p))

        # 新雇主
        if done_ids:
            done_list = list(done_ids)
            conn3 = get_conn()
            try:
                with conn3.cursor() as cur:
                    if self.only_with_reviews:
                        cur.execute(
                            """SELECT employer_id, name FROM employers
                               WHERE review_count > 0 AND employer_id != ALL(%s::int[])
                               ORDER BY review_count DESC""",
                            (done_list,))
                    else:
                        cur.execute(
                            """SELECT employer_id, name FROM employers
                               WHERE employer_id != ALL(%s::int[])
                               ORDER BY employer_id""",
                            (done_list,))
                    emp_list = [(row[0], row[1] or "") for row in cur.fetchall()]
            finally:
                put_conn(conn3)
        else:
            conn4 = get_conn()
            try:
                with conn4.cursor() as cur:
                    if self.only_with_reviews:
                        cur.execute(
                            """SELECT employer_id, name FROM employers
                               WHERE review_count > 0
                               ORDER BY review_count DESC""")
                    else:
                        cur.execute(
                            "SELECT employer_id, name FROM employers ORDER BY employer_id")
                    emp_list = [(row[0], row[1] or "") for row in cur.fetchall()]
            finally:
                put_conn(conn4)

        n = 0
        for eid, ename in emp_list:
            if eid in in_progress:
                continue
            self.q.put(self.build_task(eid, ename, None))
            n += 1
            if self.max_employers and n >= self.max_employers:
                break
            if n % 5000 == 0:
                log.info("[%s] seeder: %d queued ...", self.name, n)
        log.info("[%s] seeder: done, %d new employers queued", self.name, n)

    def worker(self, wid: int):
        buf: list[dict] = []

        def flush():
            if not buf:
                return
            docs = [d for d in buf if d.get(self.id_field_name)]
            if not docs:
                buf.clear()
                return
            rows = [self.doc_to_row(d) for d in docs]
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    execute_values(cur,
                        f"""INSERT INTO {self.out_table} ({', '.join(self.columns)})
                            VALUES %s
                            ON CONFLICT DO NOTHING""",
                        rows)
                    inserted = cur.rowcount
                    conn.commit()
                    with _stats_lock:
                        _stats["docs"] += inserted
            except Exception as e:
                log.warning("[w%d] flush error: %s", wid, str(e)[:100])
            finally:
                buf.clear()
                put_conn(conn)

        def add_doc(doc: dict):
            if not doc or not doc.get(self.id_field_name):
                return
            buf.append(doc)
            if len(buf) >= FLUSH_SIZE:
                flush()

        while not self.stop_flag.is_set():
            try:
                task = self.q.get(timeout=5)
            except queue.Empty:
                flush()
                if self.stop_flag.is_set():
                    return
                continue
            try:
                status = self.collect_employer(task, add_doc)
            except Exception as e:
                log.warning("[w%d] employer error eid=%s: %s",
                            wid, task.get("employerId"), str(e)[:120])
                status = "error"
            flush()
            self.q.task_done()
            if status == "error":
                self._mark_error(task["employerId"], task.get("startPage", 1))

    def stats_loop(self):
        last = dict(_stats)
        while not self.stop_flag.is_set():
            time.sleep(STATS_INTERVAL)
            with _stats_lock:
                cur = dict(_stats)
            d_req = cur["req_ok"] - last["req_ok"]
            d_docs = cur["docs"] - last["docs"]
            elapsed = time.time() - cur["start"]
            log.info(
                "[%s] STATS req=%d fail=%d docs=%d emp_done=%d | "
                "%.1f req/s %.0f docs/s | q=%d | limit=%.2f | fp=%s | "
                "node=%s#%d ban=%d | elapsed %.1fh",
                self.name, cur["req_ok"], cur["req_fail"], cur["docs"],
                cur["employers_done"],
                d_req / STATS_INTERVAL, d_docs / STATS_INTERVAL,
                self.q.qsize(), rate_limiter.rate,
                fp_rotator.current, rotator.current, rotator.req_count,
                len(rotator.banned_nodes),
                elapsed / 3600)
            last = cur

    def run(self):
        threads = []
        for w in range(self.workers):
            t = threading.Thread(target=self.worker, args=(w,), daemon=True)
            t.start()
            threads.append(t)
        st = threading.Thread(target=self.stats_loop, daemon=True)
        st.start()

        self.seeder()
        self.q.join()
        self.stop_flag.set()
        for t in threads:
            t.join(timeout=10)

        with _stats_lock:
            cur = dict(_stats)
        log.info("=== %s DONE === req=%d fail=%d docs=%d employers=%d",
                 self.name.upper(), cur["req_ok"], cur["req_fail"],
                 cur["docs"], cur["employers_done"])


# ---------------------------------------------------------------------------
# Benefits 采集器
# ---------------------------------------------------------------------------
class BenefitsCollector(BaseModuleCollector):
    name = "benefits"
    out_table = "benefits"
    progress_table = "benefits_progress"
    columns = BENEFIT_COLUMNS
    doc_to_row = staticmethod(_benefit_doc_to_row)
    page_size = 20
    operation = "EmployerBenefits"
    id_field_name = "benefitReviewId"

    def build_task(self, employer_id: int, employer_name: str,
                   resume_doc: dict | None) -> dict:
        task = {
            "employerId": employer_id,
            "employerName": employer_name,
            "module": self.name,
        }
        if resume_doc:
            task["startPage"] = resume_doc.get("nextPage", 1)
            task["ctx"] = resume_doc.get("ctx", {})
        else:
            task["startPage"] = 1
            task["ctx"] = {}
        return task

    def _make_body(self, employer_id: int, country_id: int,
                   page: int) -> dict:
        return {
            "operationName": self.operation,
            "variables": {
                "employerId": employer_id,
                "countryId": country_id,
                "benefitsReviewsPageNumber": page,
                "benefitsReviewsPageSize": self.page_size,
                "employmentStatus": "REGULAR",
            },
            "query": BENEFITS_QUERY,
        }

    def _probe_country(self, employer_id: int) -> tuple[int, dict] | None:
        """先尝试美国（countryId=1），无数据则遍历公司支持的其他国家。"""
        body = self._make_body(employer_id, 1, 1)
        status, data = fetch_module_page(self.operation, body, employer_id)
        if status != 200:
            return None
        overview = ((data.get("data") or {}).get(
            "benefitsOverviewForCountry") or {})
        reviews = (data.get("data") or {}).get("overviewBenefitReviews") or []
        if (overview.get("totalBenefitReviews") or 0) > 0 or reviews:
            return 1, data

        countries = ((data.get("data") or {}).get(
            "countriesForEmployerBenefits") or [])
        for c in countries:
            cid = c.get("id")
            if not cid or cid == 1:
                continue
            body = self._make_body(employer_id, cid, 1)
            status, data = fetch_module_page(
                self.operation, body, employer_id)
            if status != 200:
                continue
            overview = ((data.get("data") or {}).get(
                "benefitsOverviewForCountry") or {})
            reviews = (data.get("data") or {}).get(
                "overviewBenefitReviews") or []
            if (overview.get("totalBenefitReviews") or 0) > 0 or reviews:
                return cid, data
        return None

    def _overview_doc(self, overview: dict, employer_id: int,
                      employer_name: str, country_id: int) -> dict:
        return {
            "benefitReviewId": "__overview__",
            "employerId": employer_id,
            "employerName": employer_name,
            "countryId": country_id,
            "type": "overview",
            "comment": (overview.get("employerBenefitSummary") or {}).get("comment"),
            "overallBenefitRating": overview.get("overallBenefitRating"),
            "totalBenefitReviews": overview.get("totalBenefitReviews"),
            "benefitsCategoryToStatisticAggregates": overview.get(
                "benefitsCategoryToStatisticAggregates") or [],
            "collectedAt": datetime.now(timezone.utc),
        }

    def collect_employer(self, task: dict,
                         add_doc: Callable[[dict], None]) -> str:
        eid = task["employerId"]
        ename = task["employerName"]
        start_page = task.get("startPage", 1)
        ctx = task.get("ctx", {})
        country_id = ctx.get("countryId")
        first_data: dict | None = None

        if start_page == 1 and country_id is None:
            probe = self._probe_country(eid)
            if probe is None:
                self._mark_done(eid, "done_empty")
                return "done_empty"
            country_id, first_data = probe
            self._init_progress(eid, ename, total_pages=None,
                                ctx={"countryId": country_id})
        else:
            country_id = country_id or 1
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT 1 FROM {self.progress_table} WHERE employer_id = %s",
                        (eid,))
                    if cur.fetchone() is None:
                        self._init_progress(eid, ename, total_pages=None,
                                            ctx={"countryId": country_id})
            finally:
                put_conn(conn)

        page = start_page
        while page <= MAX_PAGES_PER_EMPLOYER:
            if first_data is not None:
                data = first_data
                first_data = None
            else:
                body = self._make_body(eid, country_id, page)
                status, data = fetch_module_page(
                    self.operation, body, eid)
                if status != 200:
                    self._mark_error(eid, page)
                    return "error"

            overview = ((data.get("data") or {}).get(
                "benefitsOverviewForCountry") or {})
            reviews = (data.get("data") or {}).get(
                "overviewBenefitReviews") or []
            if not isinstance(reviews, list):
                reviews = []

            if page == 1:
                add_doc(self._overview_doc(overview, eid, ename, country_id))
                if not reviews and not overview.get("totalBenefitReviews"):
                    self._mark_done(eid, "done_empty")
                    return "done_empty"

            for r in reviews:
                doc = transform_benefit_review(r, eid, ename, country_id)
                if doc.get("benefitReviewId"):
                    add_doc(doc)

            self._mark_page(eid, page, len(reviews))
            if len(reviews) < self.page_size:
                break
            page += 1

        self._mark_done(eid, "done")
        return "done"


# ---------------------------------------------------------------------------
# Interviews 采集器
# ---------------------------------------------------------------------------
class InterviewsCollector(BaseModuleCollector):
    name = "interviews"
    out_table = "interviews"
    progress_table = "interviews_progress"
    columns = INTERVIEW_COLUMNS
    doc_to_row = staticmethod(_interview_doc_to_row)
    page_size = 50
    operation = "EmployerInterviewsList"
    id_field_name = "interviewId"

    def build_task(self, employer_id: int, employer_name: str,
                   resume_doc: dict | None) -> dict:
        task = {
            "employerId": employer_id,
            "employerName": employer_name,
            "module": self.name,
        }
        if resume_doc:
            task["startPage"] = resume_doc.get("nextPage", 1)
        else:
            task["startPage"] = 1
        return task

    def collect_employer(self, task: dict,
                         add_doc: Callable[[dict], None]) -> str:
        eid = task["employerId"]
        ename = task["employerName"]
        page = task.get("startPage", 1)
        total_pages: int | None = None

        while page <= MAX_PAGES_PER_EMPLOYER:
            body = {
                "operationName": self.operation,
                "variables": {
                    "employerId": eid,
                    "page": page,
                    "pageSize": self.page_size,
                    "sort": "RELEVANCE",
                },
                "query": INTERVIEWS_QUERY,
            }
            status, data = fetch_module_page(self.operation, body, eid)
            if status != 200:
                self._mark_error(eid, page)
                return "error"

            il = ((data.get("data") or {}).get("employerInterviewsList") or {})

            if page == 1:
                total_pages = min(il.get("totalNumberOfPages") or 0,
                                  MAX_PAGES_PER_EMPLOYER)
                if total_pages == 0 or not il.get("interviews"):
                    self._mark_done(eid, "done_empty")
                    return "done_empty"
                self._init_progress(eid, ename, total_pages=total_pages)

            interviews = il.get("interviews") or []
            for r in interviews:
                doc = transform_interview(r, eid, ename, page)
                if doc.get("interviewId"):
                    add_doc(doc)

            self._mark_page(eid, page, len(interviews))
            if page >= total_pages or not interviews:
                break
            page += 1

        self._mark_done(eid, "done")
        return "done"


# ---------------------------------------------------------------------------
# Jobs 采集器（公司招聘岗位）
# ---------------------------------------------------------------------------
class JobsCollector(BaseModuleCollector):
    name = "jobs"
    out_table = "jobs"
    progress_table = "jobs_progress"
    columns = JOB_COLUMNS
    doc_to_row = staticmethod(_job_doc_to_row)
    page_size = 30
    operation = "JobsSearchAndroid"
    id_field_name = "listingId"
    max_no_new_pages = 8

    def build_task(self, employer_id: int, employer_name: str,
                   resume_doc: dict | None) -> dict:
        task = {
            "employerId": employer_id,
            "employerName": employer_name,
            "module": self.name,
        }
        task["startPage"] = resume_doc.get("nextPage", 1) if resume_doc else 1
        return task

    def _make_body(self, employer_id: int, page: int) -> dict:
        return {
            "operationName": self.operation,
            "variables": {
                "pageTypeEnum": "SERP",
                "searchParams": {
                    "filterParams": [
                        {"filterKey": "employerId",
                         "values": str(employer_id)}
                    ],
                    "pageNumber": page,
                },
            },
            "query": JOBS_QUERY,
        }

    def collect_employer(self, task: dict,
                         add_doc: Callable[[dict], None]) -> str:
        eid = task["employerId"]
        ename = task["employerName"]
        page = task.get("startPage", 1)
        total_pages: int | None = None
        seen: set = set()
        no_new = 0

        while page <= MAX_PAGES_PER_EMPLOYER:
            body = self._make_body(eid, page)
            status, data = fetch_module_page(self.operation, body, eid)
            if status != 200:
                self._mark_error(eid, page)
                return "error"

            jl = ((data.get("data") or {}).get("jobListings") or {})
            items = jl.get("jobListings") or []

            if total_pages is None:
                total = jl.get("totalJobsCount") or 0
                total_pages = min((total + self.page_size - 1) // self.page_size,
                                  MAX_PAGES_PER_EMPLOYER)
                if page == 1 and not items:
                    self._mark_done(eid, "done_empty")
                    return "done_empty"
                if not self._progress_exists(eid):
                    self._init_progress(eid, ename, total_pages=total_pages)

            new_this = 0
            for it in items:
                jv = it.get("jobview") or {}
                doc = transform_job(jv, eid, ename, page)
                lid = doc.get("listingId")
                if lid and lid not in seen:
                    seen.add(lid)
                    new_this += 1
                    add_doc(doc)

            self._mark_page(eid, page, new_this)

            if not items:
                break
            if new_this == 0:
                no_new += 1
                if no_new >= self.max_no_new_pages:
                    break
            else:
                no_new = 0
            if total_pages and page >= total_pages:
                break
            page += 1

        self._mark_done(eid, "done")
        return "done"

    def _progress_exists(self, employer_id: int) -> bool:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT 1 FROM {self.progress_table} WHERE employer_id = %s",
                    (employer_id,))
                return cur.fetchone() is not None
        finally:
            put_conn(conn)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Glassdoor Benefits / Interviews module collector")
    parser.add_argument(
        "--modules", default="all",
        help="采集模块，逗号分隔：benefits,interviews,jobs,all")
    parser.add_argument(
        "--max-employers", type=int, default=None,
        help="每个模块最多采集的公司数（用于测试）")
    parser.add_argument(
        "--workers", type=int, default=WORKERS,
        help="每个模块的并发 worker 数")
    parser.add_argument(
        "--all-employers", action="store_true",
        help="默认只采集 reviewCount>0 的公司；加上此参数则采集 employers 全部")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    modules = [m.strip() for m in args.modules.lower().split(",")]
    if "all" in modules:
        modules = ["benefits", "interviews", "jobs"]

    for name in modules:
        if name == "benefits":
            c = BenefitsCollector(
                max_employers=args.max_employers,
                workers=args.workers,
                only_with_reviews=not args.all_employers)
        elif name == "interviews":
            c = InterviewsCollector(
                max_employers=args.max_employers,
                workers=args.workers,
                only_with_reviews=not args.all_employers)
        elif name == "jobs":
            c = JobsCollector(
                max_employers=args.max_employers,
                workers=args.workers,
                only_with_reviews=not args.all_employers)
        else:
            log.warning("Unknown module: %s", name)
            continue

        # 统计公司数
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if c.only_with_reviews:
                    cur.execute(
                        "SELECT COUNT(*) FROM employers WHERE review_count > 0")
                else:
                    cur.execute("SELECT COUNT(*) FROM employers")
                n_emp = cur.fetchone()[0]
        finally:
            put_conn(conn)

        log.info("Starting %s collection for %d employers (workers=%d)",
                 c.name, n_emp, c.workers)
        c.run()


if __name__ == "__main__":
    main()
