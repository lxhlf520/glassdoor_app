"""Glassdoor 模块采集器：Pay & Benefits / Interviews / Jobs（公司岗位）

复用 collector_infra.py 的反限流与 GraphQL 基础设施。

Jobs（公司招聘岗位）：
- 使用 JobsSearchAndroid 查询，pageTypeEnum=SERP + searchParams.filterParams
  [{filterKey: employerId, values: <eid>}]，可纯净返回单公司岗位。
- 分页用 pageNumber 递增（cursor 方式服务端报错）。SERP 相关性排序存在跨页
  重复，故用 (listingId, employerId) 唯一索引去重，并在连续多页无新增时提前结束。

Salary 模块当前状态：
- SearchAggregatedSalaryEstimates / SearchSalaryEstimates / GetSalaryReport
  返回 "Server error"，暂未实现，保留查询模板方便后续接入。

用法示例：
    uv run python module_collector.py --modules benefits,interviews,jobs --workers 4
    uv run python module_collector.py --modules jobs --max-employers 10 --workers 1
"""
import argparse
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from pymongo import ASCENDING, InsertOne
from pymongo.errors import BulkWriteError

from collector_infra import (
    DB_NAME, fetch_graphql, fp_rotator, mongo_client, rate_limiter, rotator,
)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
COLLECTION_EMPLOYERS = "app_employers"

WORKERS = 4
FLUSH_SIZE = 200
STATS_INTERVAL = 30
MAX_PAGES_PER_EMPLOYER = 3000
MAX_PAGE_RETRIES = 5

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

# Jobs（公司岗位）查询：pageTypeEnum=SERP + filterParams(employerId) 已验证可用
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

SALARY_AGGREGATED_QUERY_TEMPLATE = (
    "query SearchAggregatedSalaryEstimates($cityId: Int, $countryId: Int, $metroId: Int, $stateId: Int, "
    "$employerId: Int, $employerName: String, $goc: GOCIdent, $jobTitle: String!, $jobTitleId: Int, "
    "$pageNumber: Int!, $pageSize: Int!, $payPeriod: PayPeriodEnum, $sort: SalariesSortOrder, $yearsOfExperience: YearsOfExperienceEnum) { "
    "aggregatedSalaryEstimates(aggregatedSalaryEstimatesInput: { employer: { id: $employerId name: $employerName } goc: $goc "
    "jobTitle: { id: $jobTitleId text: $jobTitle } location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } "
    "page: { num: $pageNumber size: $pageSize } sort: $sort viewAsPayPeriodId: $payPeriod yearsOfExperience: $yearsOfExperience } ) { "
    "numPages results { basePayStatistics { mean } currency { code id } employer { counts { globalJobCount { jobCount } } id name "
    "shortName squareLogoUrl ratings { overallRating } } jobTitle { id text gocId mgocId } payPeriod totalAdditionalPayStatistics { mean } "
    "totalPayStatistics { __typename ...PayStatistics } } resultCount queryLocation { id name type } } } "
    "fragment PayStatistics on StatisticsResult { percentiles { ident value } }"
)


# ---------------------------------------------------------------------------
# 数据转换
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
# 模块采集器基类
# ---------------------------------------------------------------------------
class BaseModuleCollector:
    name: str = ""
    out_collection: str = ""
    progress_collection: str = ""
    page_size: int = 20
    operation: str = ""
    id_field_name: str = ""

    def __init__(self, max_employers: int | None = None,
                 workers: int = WORKERS, only_with_reviews: bool = True):
        self.max_employers = max_employers
        self.workers = workers
        self.only_with_reviews = only_with_reviews
        self.client = mongo_client()
        self.db = self.client[DB_NAME]
        self.employers = self.db[COLLECTION_EMPLOYERS]
        self.out = self.db[self.out_collection]
        self.progress = self.db[self.progress_collection]
        self.q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self._setup_indexes()

    def _setup_indexes(self):
        self.out.create_index(
            [(self.id_field_name, ASCENDING), ("employerId", ASCENDING)],
            unique=True, background=True)
        self.out.create_index([("employerId", ASCENDING)], background=True)
        self.progress.create_index(
            [("employerId", ASCENDING)], unique=True, background=True)

    def build_task(self, employer_id: int, employer_name: str,
                   resume_doc: dict | None) -> dict:
        raise NotImplementedError

    def collect_employer(self, task: dict, add_doc: Callable[[dict], None]) -> str:
        raise NotImplementedError

    def _init_progress(self, employer_id: int, employer_name: str,
                       total_pages: int | None = None, ctx: dict | None = None):
        update: dict[str, Any] = {
            "$set": {
                "employerName": employer_name,
                "status": "in_progress",
                "nextPage": 1,
                "startedAt": datetime.now(timezone.utc),
                "updatedAt": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"collected": 0, "failedPages": []},
        }
        if total_pages is not None:
            update["$set"]["totalPages"] = total_pages
        if ctx:
            update["$set"]["ctx"] = ctx
        self.progress.update_one(
            {"employerId": employer_id}, update, upsert=True)

    def _mark_page(self, employer_id: int, page: int, n: int):
        self.progress.update_one(
            {"employerId": employer_id},
            {"$set": {"nextPage": page + 1,
                      "updatedAt": datetime.now(timezone.utc)},
             "$inc": {"collected": n}},
            upsert=True,
        )

    def _mark_done(self, employer_id: int, status: str):
        self.progress.update_one(
            {"employerId": employer_id},
            {"$set": {"status": status,
                      "doneAt": datetime.now(timezone.utc),
                      "updatedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )
        with _stats_lock:
            _stats["employers_done"] += 1

    def _mark_error(self, employer_id: int, page: int):
        self.progress.update_one(
            {"employerId": employer_id},
            {"$addToSet": {"failedPages": page},
             "$set": {"status": "error",
                      "updatedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def seeder(self):
        done_filter = {"status": {"$in": ["done", "done_empty",
                                           "done_with_errors"]}}
        done_ids = set(self.progress.distinct("employerId", done_filter))
        in_progress = {
            p["employerId"]: p
            for p in self.progress.find(
                {"status": "in_progress"},
                {"employerId": 1, "nextPage": 1, "ctx": 1})
        }
        log.info("[%s] seeder: %d done, %d in_progress",
                 self.name, len(done_ids), len(in_progress))

        for eid, p in in_progress.items():
            ename = (self.employers.find_one(
                {"employerId": eid}, {"shortName": 1}) or {}).get(
                    "shortName", "")
            self.q.put(self.build_task(eid, ename, p))

        query: dict[str, Any] = {"employerId": {"$nin": list(done_ids)}}
        if self.only_with_reviews:
            query["reviewCount"] = {"$gt": 0}
        cursor = self.employers.find(
            query, {"employerId": 1, "shortName": 1}).sort("reviewCount", -1)
        n = 0
        for emp in cursor:
            eid = emp["employerId"]
            if eid in in_progress:
                continue
            self.q.put(self.build_task(eid, emp.get("shortName", ""), None))
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
            try:
                ops = [InsertOne(d) for d in buf if d.get(self.id_field_name)]
                if ops:
                    self.out.bulk_write(ops, ordered=False)
                    with _stats_lock:
                        _stats["docs"] += len(ops)
            except BulkWriteError as bwe:
                inserted = bwe.details.get("nInserted", 0)
                with _stats_lock:
                    _stats["docs"] += inserted
            except Exception as e:
                log.warning("[w%d] flush error: %s", wid, str(e)[:100])
            finally:
                buf.clear()

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
        log.info("DB %s total: %d", self.out_collection,
                 self.out.count_documents({}))


# ---------------------------------------------------------------------------
# Benefits 采集器
# ---------------------------------------------------------------------------
class BenefitsCollector(BaseModuleCollector):
    name = "benefits"
    out_collection = "app_benefits"
    progress_collection = "app_benefits_progress"
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
        """先尝试美国（countryId=1），无数据则遍历公司支持的其他国家。
        返回 (选中的 countryId, 第一页响应 data)。"""
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
            "comment": (overview.get("employerBenefitSummary") or {}).get(
                "comment"),
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
            if not self.progress.find_one({"employerId": eid}):
                self._init_progress(eid, ename, total_pages=None,
                                    ctx={"countryId": country_id})

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
                add_doc(self._overview_doc(
                    overview, eid, ename, country_id))
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
    out_collection = "app_interviews"
    progress_collection = "app_interviews_progress"
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

            il = ((data.get("data") or {}).get(
                "employerInterviewsList") or {})

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
    out_collection = "app_jobs"
    progress_collection = "app_jobs_progress"
    page_size = 30  # 服务端固定每页 30 条
    operation = "JobsSearchAndroid"
    id_field_name = "listingId"
    # 注：SERP+employerId 只暴露相关性排序的子集（每公司约百条，即 App
    # 展示的“热门岗位”），totalJobsCount 虽大但无法通过翻页全量枚举。
    # 同一池会跨页重复，故靠唯一索引去重 + 连续无新增提前结束。
    max_no_new_pages = 8  # 连续多少页无新增就提前结束

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
                if not self.progress.find_one({"employerId": eid}):
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
        help="默认只采集 reviewCount>0 的公司；加上此参数则采集 app_employers 全部")
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

        n_emp = c.employers.count_documents(
            {"reviewCount": {"$gt": 0}} if c.only_with_reviews else {})
        log.info("Starting %s collection for %d employers (workers=%d)",
                 c.name, n_emp, c.workers)
        c.run()


if __name__ == "__main__":
    main()
