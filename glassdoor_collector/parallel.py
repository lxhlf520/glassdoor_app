"""Glassdoor 并行评论采集器

提速策略（实测）：
- pageSize=100（服务端上限），请求数降为 1/5
- 8 线程并发，实测 4.5 req/s 无 429
- 断点续跑：app_review_progress 按公司记录 donePages

基础设施（节点/指纹/限速）已迁移至 collector_infra.py。
"""
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, ASCENDING, InsertOne
from pymongo.errors import BulkWriteError

from .infra import (
    DB_NAME, fp_rotator, rate_limiter, rotator, fetch_graphql,
    mongo_client,
)

# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
COLLECTION_REVIEWS = "app_reviews"
COLLECTION_EMPLOYERS = "app_employers"
COLLECTION_PROGRESS = "app_review_progress"

WORKERS = 8
PAGE_SIZE = 100
MAX_PAGE_RETRIES = 5
MAX_PAGES_PER_EMPLOYER = 3000
QUEUE_SIZE = 0
FLUSH_SIZE = 500
STATS_INTERVAL = 30

REVIEWS_QUERY = (
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!, "
    "$sort: ReviewsSortOrderEnum, $jobTitle: JobTitleIdent, $language: String, "
    "$applyDefaultCriteria: Boolean, $bestProfileId: Int, "
    "$employmentStatuses: [EmploymentStatusEnum], $gocId: GOCIdent, "
    "$location: LocationIdent, $onlyCurrentEmployees: Boolean) { "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId } "
    "    employmentStatuses: $employmentStatuses goc: $gocId "
    "    location: $location sort: $sort "
    "    page: { num: $page size: $pageSize } "
    "    applyDefaultCriteria: $applyDefaultCriteria "
    "    jobTitle: $jobTitle onlyCurrentEmployees: $onlyCurrentEmployees "
    "    worldwideFilter: true dynamicProfileId: $bestProfileId "
    "    useRowProfileTldForRatings: false language: $language "
    "  }) { filteredReviewsCount numberOfPages "
    "    reviews { __typename ...EmployerReviewListFragment } } "
    "}  "
    "fragment EmployerReviewListFragment on EmployerReviewRG { "
    "  reviewId featured reviewDateTime summary isCurrentJob "
    "  lengthOfEmployment location { id name type } "
    "  ratingOverall ratingRecommendToFriend ratingCeo "
    "  ratingBusinessOutlook ratingCareerOpportunities "
    "  ratingCompensationAndBenefits ratingCultureAndValues "
    "  ratingDiversityAndInclusion ratingSeniorLeadership "
    "  ratingWorkLifeBalance pros cons advice countHelpful "
    "  hasEmployerResponse employer { id shortName squareLogoUrl } "
    "  employerResponses { responseDateTime response } "
    "  jobTitle { text } "
    "}"
)

log = logging.getLogger("parallel")

# 全局统计
_stats_lock = threading.Lock()
_stats = {"req_ok": 0, "req_fail": 0, "reviews": 0, "pages": 0,
          "employers_done": 0, "start": time.time()}
_error_streak = {"n": 0, "lock": threading.Lock()}


def _note_error():
    with _error_streak["lock"]:
        _error_streak["n"] += 1


def _note_ok():
    with _error_streak["lock"]:
        _error_streak["n"] = 0


def _adaptive_sleep():
    with _error_streak["lock"]:
        n = _error_streak["n"]
    if n >= 20:
        time.sleep(30)
    elif n >= 10:
        time.sleep(10)
    elif n >= 5:
        time.sleep(3)


def fetch_page(employer_id: int, page: int) -> tuple[int, dict]:
    body = {
        "operationName": "EmployerReviewsData",
        "variables": {
            "employerId": employer_id, "page": page, "pageSize": PAGE_SIZE,
            "sort": "RELEVANCE", "language": "eng",
            "applyDefaultCriteria": True,
            "employmentStatuses": ["REGULAR", "PART_TIME"],
            "location": {}, "onlyCurrentEmployees": False,
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
        self.client = mongo_client()
        self.db = self.client[DB_NAME]
        self.reviews = self.db[COLLECTION_REVIEWS]
        self.employers = self.db[COLLECTION_EMPLOYERS]
        self.progress = self.db[COLLECTION_PROGRESS]
        self.reviews.create_index([("reviewId", ASCENDING)], unique=True, background=True)
        self.reviews.create_index([("employerId", ASCENDING)], background=True)
        self.progress.create_index([("employerId", ASCENDING)], unique=True, background=True)
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop_flag = threading.Event()

    def _mark_page_done(self, eid: int, page: int, n_new: int):
        self.progress.update_one(
            {"employerId": eid},
            {"$addToSet": {"donePages": page},
             "$inc": {"collected": n_new},
             "$set": {"updatedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def _mark_employer_done(self, eid: int, status: str = "done"):
        self.progress.update_one(
            {"employerId": eid},
            {"$set": {"status": status, "doneAt": datetime.now(timezone.utc),
                      "updatedAt": datetime.now(timezone.utc)}},
            upsert=True,
        )
        with _stats_lock:
            _stats["employers_done"] += 1

    def worker(self, wid: int):
        buf: list[dict] = []

        def flush():
            if not buf:
                return
            try:
                ops = [InsertOne(d) for d in buf if d.get("reviewId")]
                if ops:
                    self.reviews.bulk_write(ops, ordered=False)
                    with _stats_lock:
                        _stats["reviews"] += len(ops)
            except BulkWriteError as bwe:
                inserted = bwe.details.get("nInserted", 0)
                with _stats_lock:
                    _stats["reviews"] += inserted
            except Exception as e:
                log.warning("[w%d] flush error: %s", wid, str(e)[:100])
            finally:
                buf.clear()

        while not self.stop_flag.is_set():
            try:
                task = self.q.get(timeout=5)
            except queue.Empty:
                flush()
                if self.stop_flag.is_set():
                    return
                continue
            eid, ename, page, retries = task
            _adaptive_sleep()
            rate_limiter.acquire()
            rate_limiter.maybe_ramp_up()

            status, data = fetch_page(eid, page)
            if status == -1:
                log.warning("[w%d] employer permanent fail eid=%s p%d", wid, eid, page)
                self.progress.update_one(
                    {"employerId": eid},
                    {"$addToSet": {"failedPages": page}}, upsert=True)
                self.q.task_done()
                continue
            if status != 200:
                _note_error()
                if retries < MAX_PAGE_RETRIES:
                    self.q.put((eid, ename, page, retries + 1))
                else:
                    log.warning("[w%d] page giveup eid=%s p%d", wid, eid, page)
                    self.progress.update_one(
                        {"employerId": eid},
                        {"$addToSet": {"failedPages": page}}, upsert=True)
                with _stats_lock:
                    _stats["req_fail"] += 1
                self.q.task_done()
                continue
            _note_ok()
            with _stats_lock:
                _stats["req_ok"] += 1
                _stats["pages"] += 1

            er = (data.get("data") or {}).get("employerReviews") or {}
            reviews = er.get("reviews") or []
            number_of_pages = min(er.get("numberOfPages") or 0,
                                  MAX_PAGES_PER_EMPLOYER)

            if page == 1:
                self.progress.update_one(
                    {"employerId": eid},
                    {"$set": {"employerName": ename, "totalPages": number_of_pages,
                              "status": "in_progress",
                              "startedAt": datetime.now(timezone.utc),
                              "updatedAt": datetime.now(timezone.utc)},
                     "$setOnInsert": {"donePages": [], "failedPages": [],
                                      "collected": 0}},
                    upsert=True,
                )
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

            prog = self.progress.find_one({"employerId": eid},
                                          {"donePages": 1, "totalPages": 1,
                                           "failedPages": 1})
            if prog:
                tp = prog.get("totalPages") or 0
                done_set = set(prog.get("donePages") or [])
                fail_set = set(prog.get("failedPages") or [])
                if tp > 0 and len(done_set | fail_set) >= tp:
                    st = "done" if not fail_set else "done_with_errors"
                    self._mark_employer_done(eid, st)
            self.q.task_done()
        flush()

    def seeder(self):
        in_prog = {p["employerId"]: p for p in self.progress.find(
            {"status": "in_progress"},
            {"employerId": 1, "donePages": 1, "totalPages": 1, "failedPages": 1})}
        done_ids = set(self.progress.distinct(
            "employerId", {"status": {"$in": ["done", "done_empty",
                                              "done_with_errors"]}}))
        log.info("seeder: %d in_progress, %d done", len(in_prog), len(done_ids))

        for eid, p in in_prog.items():
            tp = p.get("totalPages") or 0
            have = set(p.get("donePages") or []) | set(p.get("failedPages") or [])
            missing = [pg for pg in range(1, tp + 1) if pg not in have]
            if not missing:
                self._mark_employer_done(eid)
                continue
            ename = (self.employers.find_one({"employerId": eid},
                                             {"shortName": 1}) or {}).get("shortName", "")
            for pg in missing:
                self.q.put((eid, ename, pg, 0))
            log.info("seeder: resume eid=%s, %d missing pages", eid, len(missing))

        log.info("seeder: loading employer list ...")
        emp_list = list(self.employers.find(
            {"reviewCount": {"$gt": 0}, "employerId": {"$nin": list(done_ids)}},
            {"employerId": 1, "shortName": 1, "reviewCount": 1},
        ).sort("reviewCount", -1))
        log.info("seeder: %d employers to queue", len(emp_list))
        n_seed = 0
        for emp in emp_list:
            eid = emp["employerId"]
            if eid in in_prog:
                continue
            self.q.put((eid, emp.get("shortName", ""), 1, 0))
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
        log.info("DB reviews total: %d", self.reviews.count_documents({}))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    c = ParallelCollector()
    n_emp = c.employers.count_documents({"reviewCount": {"$gt": 0}})
    log.info("employers with reviews: %d", n_emp)
    log.info("workers=%d pageSize=%d rotator=%s nodes=%d current=%s",
             WORKERS, PAGE_SIZE, rotator.enabled, len(rotator.nodes),
             rotator.current)
    c.run()


if __name__ == "__main__":
    main()
