"""Glassdoor 并行评论采集器 — 纯协议调用，存储至 PostgreSQL

提速策略（实测）：
- pageSize=100（服务端上限），请求数降为 1/5
- 8 线程并发，实测 4.5 req/s 无 429
- 断点续跑：review_progress 按公司记录 done_pages

基础设施（节点/指纹/限速）已迁移至 infra.py。
"""
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import execute_values

from .infra import (
    fp_rotator, rate_limiter, rotator, fetch_graphql,
    invalidate_session,
)

from .config import (
    PARALLEL_FLUSH_SIZE as FLUSH_SIZE,
    PARALLEL_MAX_PAGE_RETRIES as MAX_PAGE_RETRIES,
    PARALLEL_MAX_PAGES_PER_EMPLOYER as MAX_PAGES_PER_EMPLOYER,
    PARALLEL_PAGE_SIZE as PAGE_SIZE,
    PARALLEL_QUEUE_SIZE as QUEUE_SIZE,
    PARALLEL_STATS_INTERVAL as STATS_INTERVAL,
    PARALLEL_WORKERS as WORKERS,
)
from .db import get_conn, init_all_tables, put_conn

REVIEWS_QUERY = (
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!, $sort: ReviewsSortOrderEnum, $language: String, $applyDefaultCriteria: Boolean, $employmentStatuses: [EmploymentStatusEnum], $location: LocationIdent, $onlyCurrentEmployees: Boolean) { "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId } "
    "    employmentStatuses: $employmentStatuses "
    "    location: $location "
    "    sort: $sort "
    "    page: { num: $page size: $pageSize } "
    "    applyDefaultCriteria: $applyDefaultCriteria "
    "    onlyCurrentEmployees: $onlyCurrentEmployees "
    "    worldwideFilter: true "
    "    useRowProfileTldForRatings: false "
    "    language: $language "
    "  }) { filteredReviewsCount numberOfPages "
    "    reviews { "
    "      reviewId featured reviewDateTime summary isCurrentJob "
    "      lengthOfEmployment location { id name type } "
    "      ratingOverall ratingRecommendToFriend ratingCeo "
    "      ratingBusinessOutlook ratingCareerOpportunities "
    "      ratingCompensationAndBenefits ratingCultureAndValues "
    "      ratingDiversityAndInclusion ratingSeniorLeadership "
    "      ratingWorkLifeBalance pros cons advice countHelpful "
    "      hasEmployerResponse employer { id shortName squareLogoUrl } "
    "      employerResponses { responseDateTime response } "
    "      jobTitle { text } "
    "    } "
    "  } "
    "}"
)

log = logging.getLogger("parallel")

# 全局统计
_stats_lock = threading.Lock()
_stats = {"req_ok": 0, "req_fail": 0, "reviews": 0, "pages": 0,
          "employers_done": 0, "start": time.time()}
_error_streak = {"n": 0, "lock": threading.Lock()}

# 全局失败追踪 · 冷却机制：连续失败超阈值时暂停所有请求，让隧道代理恢复
_consecutive_fails = 0
_cooldown_until = 0.0
_fail_lock = threading.Lock()


def _note_error():
    """记录一次失败。触发冷却：全局连续 ≥3 或本 worker 连续 ≥5。"""
    global _consecutive_fails
    with _error_streak["lock"]:
        _error_streak["n"] += 1
        ws = _error_streak["n"]
    with _fail_lock:
        _consecutive_fails += 1
        return _consecutive_fails >= 3 or ws >= 5


def _note_ok():
    """记录一次成功，重置新旧两个计数器。"""
    global _consecutive_fails
    with _error_streak["lock"]:
        _error_streak["n"] = 0
    with _fail_lock:
        _consecutive_fails = max(0, _consecutive_fails - 1)


def _adaptive_sleep():
    with _error_streak["lock"]:
        n = _error_streak["n"]
    if n >= 20:
        time.sleep(30)
    elif n >= 10:
        time.sleep(10)
    elif n >= 5:
        time.sleep(3)


def _trigger_cooldown():
    """触发全局冷却。时长随连续失败次数递增：15s → 30s → 60s → ... → 300s。"""
    global _cooldown_until, _consecutive_fails
    with _fail_lock:
        with _error_streak["lock"]:
            ws = _error_streak["n"]
        levels = [15, 30, 60, 120, 300]
        idx = min(max(_consecutive_fails, ws) - 3, len(levels) - 1)
        idx = max(0, idx)
        duration = levels[idx]
        _cooldown_until = max(_cooldown_until, time.time() + duration)
    log.warning("Proxy degraded (global=%d worker=%d fails), cooldown %.0fs",
                _consecutive_fails, ws, duration)
    # 销毁所有 session，冷却后重建 TCP 连接
    invalidate_session(fp_rotator.current)


# ---------------------------------------------------------------------------
# dict → PG tuple 映射
# ---------------------------------------------------------------------------
REVIEW_COLUMNS = (
    "review_id", "employer_id", "employer_name", "page", "featured",
    "review_date_time", "summary", "is_current_job", "length_of_employment",
    "location_id", "location_name",
    "rating_overall", "rating_recommend", "rating_ceo",
    "rating_business_outlook", "rating_career_opp", "rating_comp_benefits",
    "rating_culture_values", "rating_diversity", "rating_senior_leadership",
    "rating_work_life_balance",
    "pros", "cons", "advice", "count_helpful", "has_employer_response",
    "employer_responses", "job_title", "collected_at",
)


def _rating_to_float(val):
    """Convert Glassdoor enum rating strings (POSITIVE/NEGATIVE/APPROVE) to float 0-1."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).upper()
    if s in ("POSITIVE", "APPROVE"):
        return 1.0
    if s in ("NEGATIVE", "DISAPPROVE"):
        return 0.0
    if s == "NO_OPINION":
        return -1.0
    return None


def _review_doc_to_row(doc: dict) -> tuple:
    return (
        doc["reviewId"],
        doc["employerId"],
        doc.get("employerName", ""),
        doc.get("page", 0),
        doc.get("featured", False),
        doc.get("reviewDateTime"),
        doc.get("summary"),
        doc.get("isCurrentJob"),
        doc.get("lengthOfEmployment"),
        doc.get("locationId"),
        doc.get("locationName"),
        doc.get("ratingOverall"),
        _rating_to_float(doc.get("ratingRecommendToFriend")),
        _rating_to_float(doc.get("ratingCeo")),
        _rating_to_float(doc.get("ratingBusinessOutlook")),
        doc.get("ratingCareerOpportunities"),
        doc.get("ratingCompensationAndBenefits"),
        doc.get("ratingCultureAndValues"),
        doc.get("ratingDiversityAndInclusion"),
        doc.get("ratingSeniorLeadership"),
        doc.get("ratingWorkLifeBalance"),
        doc.get("pros"),
        doc.get("cons"),
        doc.get("advice"),
        doc.get("countHelpful", 0),
        doc.get("hasEmployerResponse", False),
        json.dumps(doc.get("employerResponses") or []),
        doc.get("jobTitle"),
        doc.get("collectedAt"),
    )


def fetch_page(employer_id: int, page: int) -> tuple[int, dict]:
    body = {
        "operationName": "EmployerReviewsData",
        "variables": {
            "employerId": employer_id, "page": page, "pageSize": PAGE_SIZE,
            "sort": "RELEVANCE",
            "language": "eng",
            "applyDefaultCriteria": True,
            "employmentStatuses": ["REGULAR", "PART_TIME"],
            "location": {},
            "onlyCurrentEmployees": False,
        },
        "query": REVIEWS_QUERY,
    }
    return fetch_graphql("EmployerReviewsData", body, timeout=60)


def transform_review(r: dict, employer_id: int, employer_name: str,
                     page: int) -> dict[str, Any]:
    loc = r.get("location") or {}
    jt = r.get("jobTitle") or {}
    emp = r.get("employer") or {}
    return {
        "reviewId": r.get("reviewId"),
        "employerId": employer_id,
        "employerName": employer_name or emp.get("shortName", ""),
        "page": page,
        "featured": r.get("featured", False),
        "reviewDateTime": r.get("reviewDateTime"),
        "summary": r.get("summary"),
        "isCurrentJob": r.get("isCurrentJob"),
        "lengthOfEmployment": r.get("lengthOfEmployment"),
        "locationId": loc.get("id"),
        "locationName": loc.get("name"),
        "ratingOverall": r.get("ratingOverall"),
        "ratingRecommendToFriend": r.get("ratingRecommendToFriend"),
        "ratingCeo": r.get("ratingCeo"),
        "ratingBusinessOutlook": r.get("ratingBusinessOutlook"),
        "ratingCareerOpportunities": r.get("ratingCareerOpportunities"),
        "ratingCompensationAndBenefits": r.get("ratingCompensationAndBenefits"),
        "ratingCultureAndValues": r.get("ratingCultureAndValues"),
        "ratingDiversityAndInclusion": r.get("ratingDiversityAndInclusion"),
        "ratingSeniorLeadership": r.get("ratingSeniorLeadership"),
        "ratingWorkLifeBalance": r.get("ratingWorkLifeBalance"),
        "pros": r.get("pros"),
        "cons": r.get("cons"),
        "advice": r.get("advice"),
        "countHelpful": r.get("countHelpful"),
        "hasEmployerResponse": r.get("hasEmployerResponse", False),
        "employerResponses": [
            {"date": er.get("responseDateTime"), "text": er.get("response")}
            for er in (r.get("employerResponses") or [])
        ],
        "jobTitle": jt.get("text"),
        "collectedAt": datetime.now(timezone.utc),
    }


class ParallelCollector:
    def __init__(self):
        init_all_tables()
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop_flag = threading.Event()

    # ------------------------------------------------------------------
    # Progress helpers (PG)
    # ------------------------------------------------------------------
    @staticmethod
    def _mark_page_done(eid: int, page: int, n_new: int):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_progress (employer_id, done_pages, collected, updated_at)
                       VALUES (%s, %s::int[], %s, %s)
                       ON CONFLICT (employer_id) DO UPDATE SET
                           done_pages = review_progress.done_pages || %s::int,
                           collected = review_progress.collected + %s,
                           updated_at = %s
                       WHERE NOT (%s::int = ANY(review_progress.done_pages))""",
                    (eid, [page], n_new, datetime.now(timezone.utc),
                     page, n_new, datetime.now(timezone.utc), page))
                conn.commit()
        finally:
            put_conn(conn)

    @staticmethod
    def _mark_page_failed(eid: int, page: int):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_progress (employer_id, failed_pages, updated_at)
                       VALUES (%s, %s::int[], %s)
                       ON CONFLICT (employer_id) DO UPDATE SET
                           failed_pages = review_progress.failed_pages || %s::int,
                           updated_at = %s
                       WHERE NOT (%s::int = ANY(review_progress.failed_pages))""",
                    (eid, [page], datetime.now(timezone.utc),
                     page, datetime.now(timezone.utc), page))
                conn.commit()
        finally:
            put_conn(conn)

    @staticmethod
    def _mark_employer_done(eid: int, status: str = "done"):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_progress (employer_id, status, done_at, updated_at)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (employer_id) DO UPDATE SET
                           status = EXCLUDED.status,
                           done_at = EXCLUDED.done_at,
                           updated_at = EXCLUDED.updated_at""",
                    (eid, status, datetime.now(timezone.utc),
                     datetime.now(timezone.utc)))
                conn.commit()
        finally:
            put_conn(conn)
        with _stats_lock:
            _stats["employers_done"] += 1

    @staticmethod
    def _upsert_progress(eid: int, ename: str, total_pages: int):
        """page=1 时初始化进度"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_progress
                           (employer_id, employer_name, total_pages, status,
                            done_pages, failed_pages, collected, started_at, updated_at)
                       VALUES (%s, %s, %s, 'in_progress', '{}'::int[], '{}'::int[], 0, %s, %s)
                       ON CONFLICT (employer_id) DO UPDATE SET
                           employer_name = EXCLUDED.employer_name,
                           total_pages = EXCLUDED.total_pages,
                           status = 'in_progress',
                           started_at = EXCLUDED.started_at,
                           updated_at = EXCLUDED.updated_at""",
                    (eid, ename, total_pages,
                     datetime.now(timezone.utc), datetime.now(timezone.utc)))
                conn.commit()
        finally:
            put_conn(conn)

    def worker(self, wid: int):
        buf: list[dict] = []

        def flush():
            if not buf:
                return
            docs = [d for d in buf if d.get("reviewId")]
            if not docs:
                buf.clear()
                return
            rows = [_review_doc_to_row(d) for d in docs]
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    execute_values(cur,
                        f"""INSERT INTO reviews ({', '.join(REVIEW_COLUMNS)})
                            VALUES %s
                            ON CONFLICT (review_id) DO NOTHING""",
                        rows)
                    inserted = cur.rowcount
                    conn.commit()
                    with _stats_lock:
                        _stats["reviews"] += inserted
            except Exception as e:
                log.warning("[w%d] flush error: %s", wid, str(e)[:100])
            finally:
                buf.clear()
                put_conn(conn)

        while not self.stop_flag.is_set():
            try:
                task = self.q.get(timeout=5)
            except queue.Empty:
                flush()
                if self.stop_flag.is_set():
                    return
                continue
            eid, ename, page, retries = task
            # 全局冷却等待
            with _fail_lock:
                wait = _cooldown_until - time.time()
            if wait > 0:
                log.info("[w%d] Cooldown: waiting %.0fs...", wid, wait)
                time.sleep(wait)
            _adaptive_sleep()
            rate_limiter.acquire()
            rate_limiter.maybe_ramp_up()

            status, data = fetch_page(eid, page)
            if status == -1:
                log.warning("[w%d] employer permanent fail eid=%s p%d", wid, eid, page)
                self._mark_page_failed(eid, page)
                self.q.task_done()
                continue
            if status != 200:
                if _note_error():
                    _trigger_cooldown()
                if retries < MAX_PAGE_RETRIES:
                    self.q.put((eid, ename, page, retries + 1))
                else:
                    log.warning("[w%d] page giveup eid=%s p%d", wid, eid, page)
                    self._mark_page_failed(eid, page)
                with _stats_lock:
                    _stats["req_fail"] += 1
                self.q.task_done()
                continue
            _note_ok()
            with _stats_lock:
                _stats["req_ok"] += 1
                _stats["pages"] += 1

            # 200 但响应包含 errors（如 "Server error"），按失败重试处理
            if data.get("errors"):
                err_msg = data["errors"][0].get("message", "unknown") if data["errors"] else "unknown"
                log.warning("[w%d] API error eid=%s p%d: %s", wid, eid, page, err_msg)
                if _note_error():
                    _trigger_cooldown()
                if retries < MAX_PAGE_RETRIES:
                    self.q.put((eid, ename, page, retries + 1))
                else:
                    log.warning("[w%d] page giveup (API error) eid=%s p%d", wid, eid, page)
                    self._mark_page_failed(eid, page)
                with _stats_lock:
                    _stats["req_fail"] += 1
                    _stats["req_ok"] -= 1
                    _stats["pages"] -= 1
                self.q.task_done()
                continue

            er = (data.get("data") or {}).get("employerReviews") or {}
            reviews = er.get("reviews") or []
            number_of_pages = min(er.get("numberOfPages") or 0,
                                  MAX_PAGES_PER_EMPLOYER)

            if page == 1:
                self._upsert_progress(eid, ename, number_of_pages)
                if number_of_pages == 0 or not reviews:
                    self._mark_employer_done(eid, "done_empty")
                    self.q.task_done()
                    continue
                for p in range(2, number_of_pages + 1):
                    self.q.put((eid, ename, p, 0))

            n_new = 0
            for r in reviews:
                doc = transform_review(r, eid, ename, page)
                if doc.get("reviewId"):
                    buf.append(doc)
                    n_new += 1
            if len(buf) >= FLUSH_SIZE:
                flush()

            self._mark_page_done(eid, page, n_new)

            # 检查是否已完成全部页
            if n_new == 0 and number_of_pages > 0:
                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """SELECT done_pages, failed_pages, total_pages
                               FROM review_progress WHERE employer_id = %s""",
                            (eid,))
                        row = cur.fetchone()
                finally:
                    put_conn(conn)
                if row:
                    done_set = set(row[0] or [])
                    fail_set = set(row[1] or [])
                    tp = row[2] or 0
                    if tp > 0 and len(done_set | fail_set) >= tp:
                        st = "done" if not fail_set else "done_with_errors"
                        self._mark_employer_done(eid, st)
            self.q.task_done()
        flush()

    # ------------------------------------------------------------------
    # Seeder: 从 PG 读取雇主列表 + 断点续传
    # ------------------------------------------------------------------
    def seeder(self):
        # 获取 in_progress 的雇主
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT employer_id, done_pages, failed_pages, total_pages
                       FROM review_progress WHERE status = 'in_progress'""")
                in_prog = {row[0]: {
                    "done_pages": row[1] or [], "failed_pages": row[2] or [],
                    "total_pages": row[3] or 0} for row in cur.fetchall()}

                cur.execute(
                    """SELECT employer_id FROM review_progress
                       WHERE status IN ('done', 'done_empty', 'done_with_errors')""")
                done_ids = {row[0] for row in cur.fetchall()}
        finally:
            put_conn(conn)

        log.info("seeder: %d in_progress, %d done", len(in_prog), len(done_ids))

        # 续传 in_progress
        for eid, p in in_prog.items():
            tp = p["total_pages"]
            have = set(p["done_pages"]) | set(p["failed_pages"])
            missing = [pg for pg in range(1, tp + 1) if pg not in have]
            if not missing:
                self._mark_employer_done(eid)
                continue

            conn2 = get_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM employers WHERE employer_id = %s", (eid,))
                    row = cur.fetchone()
                    ename = row[0] if row else ""
            finally:
                put_conn(conn2)

            for pg in missing:
                self.q.put((eid, ename, pg, 0))
            log.info("seeder: resume eid=%s, %d missing pages", eid, len(missing))

        # 新雇主：reviewCount > 0 且未完成
        log.info("seeder: loading employer list ...")
        if done_ids:
            done_list = list(done_ids)
            # 分批查询避免 IN 列表过大
            conn3 = get_conn()
            try:
                with conn3.cursor() as cur:
                    cur.execute(
                        """SELECT employer_id, name FROM employers
                           WHERE review_count > 0 AND employer_id != ALL(%s::int[])
                           ORDER BY review_count DESC""",
                        (done_list,))
                    emp_list = [(row[0], row[1] or "") for row in cur.fetchall()]
            finally:
                put_conn(conn3)
        else:
            conn4 = get_conn()
            try:
                with conn4.cursor() as cur:
                    cur.execute(
                        """SELECT employer_id, name FROM employers
                           WHERE review_count > 0
                           ORDER BY review_count DESC""")
                    emp_list = [(row[0], row[1] or "") for row in cur.fetchall()]
            finally:
                put_conn(conn4)

        log.info("seeder: %d employers to queue", len(emp_list))
        n_seed = 0
        for eid, ename in emp_list:
            if eid in in_prog:
                continue
            self.q.put((eid, ename, 1, 0))
            n_seed += 1
            if n_seed % 5000 == 0:
                log.info("seeder: %d employers queued ...", n_seed)
        log.info("seeder: done, %d new employers queued", n_seed)

    def stats_loop(self):
        last = dict(_stats)
        while not self.stop_flag.is_set():
            time.sleep(STATS_INTERVAL)
            with _stats_lock:
                cur = dict(_stats)
            d_req = cur["req_ok"] - last["req_ok"]
            d_rev = cur["reviews"] - last["reviews"]
            elapsed = time.time() - cur["start"]
            log.info(
                "STATS req=%d fail=%d pages=%d reviews=%d emp_done=%d | "
                "%.1f req/s %.0f rev/s | q=%d | limit=%.2f | fp=%s | node=%s#%d ban=%d | "
                "elapsed %.1fh",
                cur["req_ok"], cur["req_fail"], cur["pages"], cur["reviews"],
                cur["employers_done"],
                d_req / STATS_INTERVAL, d_rev / STATS_INTERVAL,
                self.q.qsize(), rate_limiter.rate,
                fp_rotator.current, rotator.current, rotator.req_count,
                len(rotator.banned_nodes),
                elapsed / 3600)
            last = cur

    def run(self):
        threads = []
        for w in range(WORKERS):
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
        log.info("=== ALL DONE === req=%d fail=%d reviews=%d employers=%d",
                 cur["req_ok"], cur["req_fail"], cur["reviews"],
                 cur["employers_done"])

        # 汇总查询：重试以应对池连接闲置超时被 PG 断开
        for attempt in range(3):
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM reviews")
                    total = cur.fetchone()[0]
                log.info("DB reviews total: %d", total)
                break
            except Exception:
                time.sleep(0.5)
            finally:
                put_conn(conn)
        else:
            log.info("DB reviews total: (query failed, data intact)")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    c = ParallelCollector()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM employers WHERE review_count > 0")
            n_emp = cur.fetchone()[0]
    finally:
        put_conn(conn)

    log.info("employers with reviews: %d", n_emp)
    log.info("workers=%d pageSize=%d rotator=%s nodes=%d current=%s",
             WORKERS, PAGE_SIZE, rotator.enabled, len(rotator.nodes),
             rotator.current)
    c.run()


if __name__ == "__main__":
    main()
