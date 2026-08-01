"""Glassdoor APP 评论采集器 — 纯协议调用，存储至 MongoDB"""
import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import curl_cffi.requests as requests
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "glassdoor"
COLLECTION_REVIEWS = "app_reviews"
COLLECTION_EMPLOYERS = "app_employers"

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

DELAY_BETWEEN_PAGES = (0.3, 0.8)  # 经压力测试: APP 无 429 限流
DELAY_BETWEEN_EMPLOYERS = (1.5, 3)  # 安全间隔
PAGE_SIZE = 20
MAX_RETRIES = 3

# 已知公司种子（从首页推荐获取）
SEED_EMPLOYERS = [
    1138,     # Apple Inc.
    1596815,  # DeepMind Technologies Limited
    235798,   # (from geb events)
    321780,   # (from geb events)
    7790,     # (from geb events)
    252593,   # (from geb events)
    18604,    # (from geb events)
    40371,    # (from geb events)
]

# 公司搜索关键词（2-gram 覆盖 + 行业词）
DISCOVERY_TERMS = [
    # 英语最高频 2-gram
    "in", "er", "an", "on", "at", "es", "en", "or", "al", "ti",
    "te", "ic", "ar", "st", "re", "le", "ra", "li", "io", "nt",
    "ed", "it", "ve", "co", "de", "ri", "ro", "ne", "ma", "ta",
    "si", "el", "la", "ch", "me", "di", "un", "no", "pe", "ac",
    # 行业关键词
    "Software", "Consulting", "Bank", "Insurance", "Hospital",
    "Health", "Tech", "Media", "Finance", "Marketing", "Retail",
    "Energy", "Construction", "Real Estate", "Education", "Law",
    "Food", "Transport", "Pharma", "Hotel", "Design", "Security",
]
DISCOVERY_PER_PAGE = 100
DISCOVERY_MAX_PAGES = 5  # 每个关键词最多 5 页（500 家公司）

# GraphQL 请求体模板（从 APP 抓包提取）
SEARCH_COMPANIES_QUERY = (
    "query SearchCompanies($employerSearchInput: EmployerSearchInput) { "
    "  employerSearchRG(employerSearchInput: $employerSearchInput) { "
    "    __typename ...CompaniesSearchResultFragment "
    "  } "
    "}  "
    "fragment EmployerResultFragment on UgcSearchV3EmployerResult { "
    "  employer { id shortName squareLogoUrl(size: REGULAR) "
    "    counts { salaryCount reviewCount globalJobCount { jobCount } } "
    "  } "
    "  employerRatings { overallRating } "
    "}  "
    "fragment CompaniesSearchResultFragment on UgcSearchV3EmployerResultWrapper { "
    "  employerResults { __typename ...EmployerResultFragment } "
    "  numOfPagesAvailable pageNumber "
    "}"
)

REVIEWS_QUERY = (
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!, "
    "$sort: ReviewsSortOrderEnum, $jobTitle: JobTitleIdent, $language: String, "
    "$applyDefaultCriteria: Boolean, $bestProfileId: Int, "
    "$employmentStatuses: [EmploymentStatusEnum], $gocId: GOCIdent, "
    "$location: LocationIdent, $onlyCurrentEmployees: Boolean) { "
    "  employer(id: $employerId) { primaryIndustryId name shortName } "
    "  reviewLocationsRG(employer: { id: $employerId } ) "
    "    { __typename ...EmployerReviewLocationsFragment } "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId }  "
    "    employmentStatuses: $employmentStatuses goc: $gocId "
    "    location: $location sort: $sort "
    "    page: { num: $page size: $pageSize }  "
    "    applyDefaultCriteria: $applyDefaultCriteria "
    "    jobTitle: $jobTitle onlyCurrentEmployees: $onlyCurrentEmployees "
    "    worldwideFilter: true dynamicProfileId: $bestProfileId "
    "    useRowProfileTldForRatings: false language: $language "
    "  }) { filteredReviewsCount numberOfPages "
    "    reviews { __typename ...EmployerReviewListFragment } "
    "    queryJobTitle { mgocId } "
    "  } "
    "}  "
    "fragment EmployerReviewLocationsFragment on ReviewLocationsRGResponse { "
    "  locations { id name type children { id name type "
    "    children { id name type children { id name type "
    "    children { id name type } } } } } }  "
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

log = logging.getLogger("glassdoor")


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
class GlassdoorCollector:
    """Glassdoor APP 评论采集器"""

    def __init__(self) -> None:
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.reviews = self.db[COLLECTION_REVIEWS]
        self.employers = self.db[COLLECTION_EMPLOYERS]

        # 确保索引
        self.reviews.create_index([("reviewId", ASCENDING)], unique=True, background=True)
        self.reviews.create_index([("employerId", ASCENDING)], background=True)
        self.employers.create_index([("employerId", ASCENDING)], unique=True, background=True)

        self.session = requests.Session()
        self._cf_bm = CF_BM
        self._gd_id = GD_ID

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _headers(self, operation: str) -> dict:
        return {
            "x-gd-id": self._gd_id,
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
            "cookie": f"gdId={self._gd_id}; __cf_bm={self._cf_bm}",
        }

    def _post(self, body: dict, operation: str = "EmployerReviewsData") -> requests.Response:
        url = "https://api.glassdoor.com/mobile-graph"
        params = {"locale": "zh_CN_#Hans"}
        resp = requests.post(
            url,
            params=params,
            headers=self._headers(operation),
            json=body,
            impersonate="chrome110",
            timeout=60,
        )
        return resp

    def _retry_post(self, body: dict, operation: str = "EmployerReviewsData") -> dict:
        """带重试的 POST，返回解析后的 JSON"""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._post(body, operation)
                if resp.status_code == 200 and len(resp.content) > 100:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    log.warning("Rate limited (429), waiting %ds ...", wait)
                    time.sleep(wait)
                    continue
                log.warning("HTTP %d: %s", resp.status_code, resp.text[:200])
            except Exception as exc:
                log.warning("Request failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Failed after {MAX_RETRIES} retries")

    # ------------------------------------------------------------------
    # 核心采集
    # ------------------------------------------------------------------
    def _collect_employer_reviews(self, employer_id: int, total_pages: int,
                                   employer_name: str = "") -> int:
        """采集单个公司的所有评论，返回新增数量"""
        new_count = 0
        for page in range(1, total_pages + 1):
            # 断点检查：跳过已有数据的页
            check = self.reviews.find_one(
                {"employerId": employer_id, "page": page},
                {"_id": 1},
            )
            if check:
                log.debug("  page %d already collected, skip", page)
                continue

            body = {
                "operationName": "EmployerReviewsData",
                "variables": {
                    "employerId": employer_id,
                    "page": page,
                    "pageSize": PAGE_SIZE,
                    "sort": "RELEVANCE",
                    "language": "eng",
                    "applyDefaultCriteria": True,
                    "employmentStatuses": ["REGULAR", "PART_TIME"],
                    "location": {},
                    "onlyCurrentEmployees": False,
                },
                "query": REVIEWS_QUERY,
                "extensions": {
                    "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
                },
            }

            data = self._retry_post(body)
            er = data.get("data", {}).get("employerReviews", {})
            reviews = er.get("reviews", [])

            if not reviews:
                log.debug("  page %d empty, done", page)
                break

            docs = []
            for r in reviews:
                doc = self._transform_review(r, employer_id, employer_name, page)
                docs.append(doc)

            if docs:
                try:
                    self.reviews.insert_many(docs, ordered=False)
                    new_count += len(docs)
                except Exception:
                    # fallback to single insert for duplicates
                    for doc in docs:
                        try:
                            self.reviews.insert_one(doc)
                            new_count += 1
                        except DuplicateKeyError:
                            pass

            log.info("  page %d/%d: %d reviews (new: %d)",
                     page, total_pages, len(reviews), new_count)

            # rate limit
            delay = random.uniform(*DELAY_BETWEEN_PAGES)
            time.sleep(delay)

            # 动态更新 total_pages（可能后台变化）
            actual_pages = er.get("numberOfPages", total_pages)
            if actual_pages > total_pages:
                total_pages = actual_pages
                log.info("  total_pages updated to %d", total_pages)

        return new_count

    @staticmethod
    def _transform_review(r: dict, employer_id: int, employer_name: str,
                           page: int) -> dict[str, Any]:
        """将 API 响应转换为 MongoDB 文档"""
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
            "captured_at": datetime.now(timezone.utc),
        }

    # ------------------------------------------------------------------
    # 公司列表 / 发现
    # ------------------------------------------------------------------
    def discover_companies(self, terms: list[str] | None = None,
                            max_pages: int = DISCOVERY_MAX_PAGES) -> int:
        """通过关键词搜索发现公司，返回新增公司数量"""
        if terms is None:
            terms = DISCOVERY_TERMS

        new_count = 0
        for term in terms:
            log.info("Discover: '%s'", term)
            total_results = 0
            for page in range(1, max_pages + 1):
                body = {
                    "operationName": "SearchCompanies",
                    "variables": {
                        "employerSearchInput": {
                            "employerName": term,
                            "numPerPage": DISCOVERY_PER_PAGE,
                            "pageRequested": page,
                        }
                    },
                    "query": SEARCH_COMPANIES_QUERY,
                    "extensions": {
                        "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
                    },
                }
                try:
                    data = self._retry_post(body, "SearchCompanies")
                except RuntimeError:
                    log.info("  page %d: failed, done with '%s'", page, term)
                    break

                sr = data.get("data", {}).get("employerSearchRG", {})
                results = sr.get("employerResults", [])
                available_pages = sr.get("numOfPagesAvailable", 0)

                if not results:
                    break

                for r in results:
                    emp = r.get("employer", {})
                    eid = emp.get("id")
                    if not eid:
                        continue
                    if self.employers.find_one({"employerId": eid}, {"_id": 1}):
                        continue  # 已存在

                    ratings = r.get("employerRatings", {}) or {}
                    counts = emp.get("counts", {}) or {}
                    global_jobs = counts.get("globalJobCount", {}) or {}

                    doc = {
                        "employerId": eid,
                        "shortName": emp.get("shortName", ""),
                        "name": emp.get("shortName", ""),
                        "logoUrl": emp.get("squareLogoUrl"),
                        "overallRating": ratings.get("overallRating"),
                        "reviewCount": counts.get("reviewCount", 0),
                        "salaryCount": counts.get("salaryCount", 0),
                        "jobCount": global_jobs.get("jobCount", 0),
                        "discoveredVia": term,
                        "discoveredAt": datetime.now(timezone.utc),
                    }
                    self.employers.update_one(
                        {"employerId": eid},
                        {"$setOnInsert": doc},
                        upsert=True,
                    )
                    new_count += 1
                    total_results += 1

                log.info("  page %d/%d: %d results (total: %d, new: %d)",
                         page, min(available_pages, max_pages),
                         len(results), total_results, new_count)

                if page >= available_pages:
                    break
                time.sleep(random.uniform(1.5, 3))

            time.sleep(random.uniform(2, 4))

        log.info("Discovery done: %d new employers", new_count)
        return new_count

    def get_employer_ids(self, min_reviews: int = 1) -> list[int]:
        """获取待采集的公司 ID 列表（可设定最小评论数阈值）"""
        pipeline = [
            {"$match": {"reviewCount": {"$gte": min_reviews}}},
            {"$sort": {"reviewCount": -1}},
            {"$project": {"employerId": 1, "name": 1, "reviewCount": 1}},
        ]
        return [doc["employerId"] for doc in self.employers.aggregate(pipeline)]

    def seed_employers(self) -> None:
        """写入种子公司记录，并尝试获取名称"""
        for eid in SEED_EMPLOYERS:
            if self.employers.find_one({"employerId": eid}):
                continue
            # 尝试用评论接口第一页获取名称
            name = ""
            try:
                body = {
                    "operationName": "EmployerReviewsData",
                    "variables": {
                        "employerId": eid,
                        "page": 1, "pageSize": 1,
                        "sort": "RELEVANCE", "language": "eng",
                        "applyDefaultCriteria": True,
                        "employmentStatuses": ["REGULAR", "PART_TIME"],
                        "location": {}, "onlyCurrentEmployees": False,
                    },
                    "query": REVIEWS_QUERY,
                    "extensions": {
                        "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
                    },
                }
                data = self._retry_post(body)
                emp = data.get("data", {}).get("employer", {})
                er = data.get("data", {}).get("employerReviews", {})
                name = emp.get("name", "")
                log.info("Seed employer %d: %s (%d reviews)",
                         eid, name, er.get("filteredReviewsCount", 0))
            except Exception as exc:
                log.warning("Failed to get name for %d: %s", eid, exc)

            self.employers.update_one(
                {"employerId": eid},
                {"$set": {"employerId": eid, "name": name,
                          "seeded_at": datetime.now(timezone.utc)}},
                upsert=True,
            )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def collect_all(self, employer_ids: list[int] | None = None,
                     max_employers: int = 0,
                     max_pages_per_employer: int = 0) -> dict:
        """采集所有指定公司（或全部已发现公司）的评论

        Args:
            employer_ids: 指定公司列表，None=从 DB 获取所有
            max_employers: 最多采集公司数，0=全部
            max_pages_per_employer: 每公司最多页数，0=全部
        """
        if employer_ids is None:
            employer_ids = self.get_employer_ids(min_reviews=1)
            if not employer_ids:
                employer_ids = SEED_EMPLOYERS

        if max_employers > 0:
            employer_ids = employer_ids[:max_employers]

        stats = {"total_new": 0, "companies": 0}
        for idx, eid in enumerate(employer_ids, 1):
            log.info("[%d/%d] Employer %d ...", idx, len(employer_ids), eid)

            try:
                body = {
                    "operationName": "EmployerReviewsData",
                    "variables": {
                        "employerId": eid,
                        "page": 1, "pageSize": PAGE_SIZE,
                        "sort": "RELEVANCE", "language": "eng",
                        "applyDefaultCriteria": True,
                        "employmentStatuses": ["REGULAR", "PART_TIME"],
                        "location": {}, "onlyCurrentEmployees": False,
                    },
                    "query": REVIEWS_QUERY,
                    "extensions": {
                        "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
                    },
                }
                data = self._retry_post(body)
                emp = data.get("data", {}).get("employer", {})
                er = data.get("data", {}).get("employerReviews", {})
                name = emp.get("name", "")
                total_pages = er.get("numberOfPages", 0)
                total_reviews = er.get("filteredReviewsCount", 0)

                log.info("  %s: %d reviews, %d pages", name, total_reviews, total_pages)

                # 更新公司记录
                self.employers.update_one(
                    {"employerId": eid},
                    {"$set": {"employerId": eid, "name": name,
                              "totalReviews": total_reviews,
                              "totalPages": total_pages}},
                    upsert=True,
                )

                if total_pages == 0:
                    log.info("  No reviews, skip")
                    continue

                if max_pages_per_employer > 0:
                    total_pages = min(total_pages, max_pages_per_employer)

                new_count = self._collect_employer_reviews(eid, total_pages, name)
                stats["total_new"] += new_count
                stats["companies"] += 1
                log.info("  Done: %d new reviews", new_count)

            except Exception as exc:
                log.error("  Failed for employer %d: %s", eid, exc)

            delay = random.uniform(*DELAY_BETWEEN_EMPLOYERS)
            time.sleep(delay)

        log.info("All done: %d new reviews across %d companies",
                 stats["total_new"], stats["companies"])
        return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    collector = GlassdoorCollector()

    # Step 1: 发现公司
    log.info("=== Phase 1: Discover companies ===")
    # 先试用少量关键词快速测试
    test_terms = ["Software", "Consulting", "Tech", "Health", "Finance"]
    new_employers = collector.discover_companies(terms=test_terms, max_pages=3)
    log.info("Discovered %d new employers", new_employers)

    total = collector.employers.count_documents({})
    with_reviews = collector.employers.count_documents({"reviewCount": {"$gt": 0}})
    log.info("Total employers in DB: %d (%d with reviews)", total, with_reviews)

    # Step 2: 采集评论（按评论数从高到低）
    employer_ids = collector.get_employer_ids(min_reviews=10)
    log.info("=== Phase 2: Collect reviews for %d employers ===", len(employer_ids))

    stats = collector.collect_all(
        employer_ids=employer_ids,
        max_employers=50,        # 先限制 50 家公司
        max_pages_per_employer=10,  # 每公司最多 10 页
    )
    print(f"\nDone. {stats['total_new']} new reviews across {stats['companies']} companies.")


if __name__ == "__main__":
    main()
