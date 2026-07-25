"""Glassdoor 并行评论采集器

提速策略（实测）：
- pageSize=100（服务端上限），请求数降为 1/5
- 8 线程并发，实测 4.5 req/s 无 429
- 断点续跑：app_review_progress 按公司记录 donePages
"""
import json
import logging
import os
import queue
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import curl_cffi.requests as curl_requests
from pymongo import MongoClient, ASCENDING, InsertOne
from pymongo.errors import BulkWriteError

from clash_api import ClashAPI

# ---------------------------------------------------------------------------
# 支持环境变量覆盖，方便部署到不同机器
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "glassdoor"
COLLECTION_REVIEWS = "app_reviews"
COLLECTION_EMPLOYERS = "app_employers"
COLLECTION_PROGRESS = "app_review_progress"

WORKERS = 8
PAGE_SIZE = 100
PROXY_URL = os.environ.get("CLASH_MIXED", "http://127.0.0.1:7890")
PROXIES = {"http": PROXY_URL, "https": PROXY_URL}
MAX_PAGE_RETRIES = 5
MAX_PAGES_PER_EMPLOYER = 3000  # 安全上限 (Amazon 2103 页)
# 两层轮换（指纹实证，_fp_test.py / _fresh_asn_test.py）：
# - 指纹级配额：同 TLS 指纹 ~500-600 请求/窗口（与 IP、gdId 无关），停火 3-5 分钟恢复
# - IP 级配额：干净节点 ~600+ 请求/窗口，共享 IP 可被预烧
# => 指纹轮换 200 req/指纹 + 节点轮换 250 req/IP，双重保底
ROTATE_AFTER = 250
BAN_COOLDOWN = 15 * 60      # 429/403 后节点冷却 15 分钟（实测封禁仅 2-3 分钟）

# TLS 指纹池（_fp_test.py 验证全部通杀 api.glassdoor.com）
FP_POOL = ["chrome110", "chrome120", "chrome124", "chrome131", "chrome133a",
           "safari15_5", "safari17_0", "safari18_0", "edge101", "firefox133",
           "chrome131_android", "chrome99_android"]
FP_ROTATE_AFTER = 200  # 低于 ~500-600 指纹配额墙

ALIVE_NODES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_alive_nodes.json")
# 无界队列：有界队列会导致 worker 扩展分页任务时与 seeder 互相死锁
# 峰值 ~40 万轻量元组 (eid, name, page, retries)，内存 <100MB 可接受
QUEUE_SIZE = 0
FLUSH_SIZE = 500               # 每线程批量写入阈值
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
_tls = threading.local()

# 全局统计
_stats_lock = threading.Lock()
_stats = {"req_ok": 0, "req_fail": 0, "reviews": 0, "pages": 0,
          "employers_done": 0, "start": time.time()}
# 自适应降速
_error_streak = {"n": 0, "lock": threading.Lock()}


# ---------------------------------------------------------------------------
# 全局令牌桶限速器：实测持续 ~7 req/s 触发 429，初始 3.0 req/s 自适应调节
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, rate: float):
        self._rate = rate          # 当前许可速率 (req/s)
        self._min_rate = 0.5
        self._max_rate = 3.0       # 封顶 3 req/s：实测干净 IP 上稳定，更高易触限
        self._tokens = rate        # 桶内令牌
        self._capacity = max(rate * 2, 4)   # 允许小 burst
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._last_429 = 0.0
        self._last_adjust = time.monotonic()

    @property
    def rate(self) -> float:
        return self._rate

    def acquire(self):
        """阻塞直到拿到一个令牌"""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(min(wait, 1.0))

    def on_429(self):
        """遇 429：速率降 30%，10s 内不重复降"""
        with self._lock:
            now = time.monotonic()
            if now - self._last_429 < 10:
                return
            self._last_429 = now
            old = self._rate
            self._rate = max(self._min_rate, self._rate * 0.7)
            self._capacity = max(self._rate * 2, 2)
            self._tokens = min(self._tokens, self._capacity)
            self._last_adjust = now
        log.warning("429: rate %.2f -> %.2f req/s", old, self._rate)

    def maybe_ramp_up(self):
        """连续 5 分钟无 429：速率 +15%（上限 max_rate）"""
        with self._lock:
            now = time.monotonic()
            if now - self._last_429 < 300 or now - self._last_adjust < 300:
                return
            old = self._rate
            self._rate = min(self._max_rate, self._rate * 1.15)
            self._capacity = max(self._rate * 2, 4)
            self._last_adjust = now
            if self._rate != old:
                log.info("ramp up: rate %.2f -> %.2f req/s", old, self._rate)


rate_limiter = RateLimiter(rate=3.0)


# ---------------------------------------------------------------------------
# FlClash 节点轮换：每 IP 有 429 配额，按请求数主动换节点 + 429/403 被动换
# ---------------------------------------------------------------------------
class NodeRotator:
    def __init__(self):
        self.api = ClashAPI()
        self.lock = threading.Lock()
        self.nodes = self._load_nodes()
        self.idx = -1
        self.current = self.api.current() if self.api.alive() else None
        if self.current in self.nodes:
            self.idx = self.nodes.index(self.current)
        self.req_count = 0
        self.banned_nodes = {}   # node -> unban ts
        self.banned_ips = {}     # egress ip -> unban ts
        self.last_rotate = 0.0
        self.pending_429 = 0     # 当前节点连续 429 计数（<3 不换，防误判）
        self.enabled = bool(self.nodes) and self.api.alive()

    def _load_nodes(self) -> list:
        nodes = []
        try:
            with open(ALIVE_NODES_FILE, encoding="utf-8") as f:
                nodes = json.load(f)
        except Exception:
            pass
        if not nodes:
            try:
                nodes = ClashAPI().nodes()
            except Exception:
                nodes = []
        # 洗牌打散地区/ASN：顺序遍历会连续命中同子网段（CF 按段限流）
        random.shuffle(nodes)
        return nodes

    def on_request(self):
        """每次请求前调用：计数 + 达阈值主动轮换"""
        with self.lock:
            self.req_count += 1
            if self.enabled and self.req_count >= ROTATE_AFTER:
                self._rotate_locked("proactive")

    def on_429(self):
        with self.lock:
            if time.time() - self.last_rotate < 10:
                return
            self.pending_429 += 1
            if self.pending_429 < 3:
                log.warning("429 on %s (%d/3), not rotating yet",
                            self.current, self.pending_429)
                return
            self.pending_429 = 0
        self._ban_and_rotate("429")

    def on_403(self):
        self._ban_and_rotate("403")

    def on_ok(self):
        with self.lock:
            self.pending_429 = 0

    def _ban_and_rotate(self, reason: str):
        with self.lock:
            now = time.time()
            # 10s 内已有其他线程处理过本次封禁，直接返回
            if now - self.last_rotate < 10:
                return
            if self.current:
                unban = now + BAN_COOLDOWN
                self.banned_nodes[self.current] = unban
                # 尽力标记出口 IP：同 IP 节点共享配额
                try:
                    eg = self.api.egress_ip(timeout=4)
                    if eg and eg[0]:
                        self.banned_ips[eg[0]] = unban
                except Exception:
                    pass
            self._rotate_locked(reason)

    def _rotate_locked(self, reason: str):
        now = time.time()
        n = len(self.nodes)
        for _ in range(n):
            self.idx = (self.idx + 1) % n
            node = self.nodes[self.idx]
            if now < self.banned_nodes.get(node, 0):
                continue
            eg = self.api.switch_and_wait(node, settle=1.0)
            if not eg:
                self.banned_nodes[node] = now + 300   # 节点不通，5 分钟后再试
                continue
            if now < self.banned_ips.get(eg[0], 0):
                # 同 IP 节点共享配额/封禁
                self.banned_nodes[node] = self.banned_ips[eg[0]]
                continue
            log.warning("rotate[%s]: req=%d %s -> %s (egress %s %s)",
                        reason, self.req_count, self.current, node, eg[0], eg[1])
            self.current = node
            self.req_count = 0
            self.last_rotate = time.time()
            return
        # 全部冷却：睡到最早解封
        wake = min(list(self.banned_nodes.values()) + [time.time() + 120])
        wait = max(30, wake - time.time() + 5)
        log.warning("all nodes cooling, sleep %.0fs", wait)
        time.sleep(wait)
        # 解封后重置计数，下一轮请求自然继续；若仍是旧节点被封会再触发轮换
        self.req_count = 0


rotator = NodeRotator()


# ---------------------------------------------------------------------------
# 指纹轮换器：每 FP_ROTATE_AFTER 请求切到下一个 TLS 指纹
# ---------------------------------------------------------------------------
class FPRotator:
    def __init__(self, pool, after):
        self.pool = list(pool)
        random.shuffle(self.pool)
        self.after = after
        self.lock = threading.Lock()
        self.idx = -1
        self.count = 0
        self.current = None

    def take(self) -> str:
        """返回当前指纹并计数；满 after 自动切到下一个。"""
        with self.lock:
            if self.count == 0 or self.count >= self.after:
                self.idx = (self.idx + 1) % len(self.pool)
                self.current = self.pool[self.idx]
                self.count = 0
                if self.idx == 0:           # 一轮结束重新洗牌
                    random.shuffle(self.pool)
            self.count += 1
            return self.current


fp_rotator = FPRotator(FP_POOL, FP_ROTATE_AFTER)


def get_session(fp: str):
    if not hasattr(_tls, "sessions"):
        _tls.sessions = {}
    if fp not in _tls.sessions:
        _tls.sessions[fp] = curl_requests.Session(impersonate=fp)
        _tls.sessions[fp].proxies = PROXIES
    return _tls.sessions[fp]


def _headers(operation: str) -> dict:
    # 每请求随机设备 ID、不带 cookie：规避 CF 按固定身份(~500 req)的 429 配额
    return {
        "x-gd-id": str(uuid.uuid4()),
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": operation,
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0"
        ),
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
    }


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
    """返回 (status, data)。status: 200 ok / 0 网络或 HTTP 错误"""
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
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
    }
    try:
        rotator.on_request()
        fp = fp_rotator.take()
        resp = get_session(fp).post(
            "https://api.glassdoor.com/mobile-graph",
            params={"locale": "zh_CN_#Hans"},
            headers=_headers("EmployerReviewsData"), json=body,
            timeout=60,
        )
        if resp.status_code == 200:
            rotator.on_ok()
            return 200, resp.json()
        if resp.status_code == 429:
            # 429 = 该出口 IP/子网配额耗尽（CF error 1015），由轮换解决，
            # 不降全局速率（实测干净 IP 上 3 req/s 长期稳定）
            log.warning("429 received! body: %s", resp.text[:200])
            rotator.on_429()
            time.sleep(2)
            return 0, {}
        if resp.status_code == 403:
            # JSON body = 雇主级封锁（区域限制/已删除），不轮换、不重试
            # HTML body  = CF 质询页面，轮换节点
            body_prefix = resp.text[:1]
            if body_prefix in ("{", "[") or "json" in resp.headers.get("content-type", ""):
                log.info("403 employer-blocked eid=%s p%d (JSON)", employer_id, page)
                return -1, {}   # -1 = 永久失败，worker 不重试
            log.warning("403 CF challenge on eid=%s p%d, rotating node", employer_id, page)
            rotator.on_403()
            time.sleep(5)
            return 0, {}
        log.warning("HTTP %d on eid=%s p%d", resp.status_code, employer_id, page)
        return 0, {}
    except Exception as e:
        log.warning("req error eid=%s p%d: %s", employer_id, page, str(e)[:100])
        return 0, {}


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
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.reviews = self.db[COLLECTION_REVIEWS]
        self.employers = self.db[COLLECTION_EMPLOYERS]
        self.progress = self.db[COLLECTION_PROGRESS]
        self.reviews.create_index([("reviewId", ASCENDING)], unique=True, background=True)
        self.reviews.create_index([("employerId", ASCENDING)], background=True)
        self.progress.create_index([("employerId", ASCENDING)], unique=True, background=True)
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)  # 0 = 无界
        self.stop_flag = threading.Event()

    # ---------------- progress helpers ----------------
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

    # ---------------- worker ----------------
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
                # 重复 reviewId 正常忽略
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
                # 雇主级永久失败（如 403 JSON 雇主封锁），不重试直接记录
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

            # page 1: 展开剩余页任务 + 初始化 progress
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

            # 完成判定（仅非 page1 路径需要；page1 在展开后由后续页驱动）
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

    # ---------------- seeder ----------------
    def seeder(self):
        """按 reviewCount 降序播种。断点续跑：
        - done/done_empty: 跳过
        - in_progress: 补投缺失页
        - 无记录: 投 page 1
        """
        in_prog = {p["employerId"]: p for p in self.progress.find(
            {"status": "in_progress"},
            {"employerId": 1, "donePages": 1, "totalPages": 1, "failedPages": 1})}
        done_ids = set(self.progress.distinct(
            "employerId", {"status": {"$in": ["done", "done_empty",
                                              "done_with_errors"]}}))
        log.info("seeder: %d in_progress, %d done", len(in_prog), len(done_ids))

        # 1) 先补投 in_progress 缺失页
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

        # 2) 新公司播种（先全量载入内存，避免队列阻塞导致游标超时）
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

    # ---------------- stats ----------------
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
        self.q.join()  # 等所有任务完成
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
