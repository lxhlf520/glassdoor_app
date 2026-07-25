"""Glassdoor 全量公司发现 — 使用 40+ 关键词遍历 SearchCompanies"""
import json
import logging
import random
import time
from datetime import datetime, timezone

import curl_cffi.requests as requests
from pymongo import MongoClient, ASCENDING

# ---------------------------------------------------------------------------
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "glassdoor"
COLLECTION_EMPLOYERS = "app_employers"

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

# 经压力测试: 0 delay 也无 429，安全延迟 0.3s
DELAY_BETWEEN_PAGES = 0.3
DELAY_BETWEEN_TERMS = 1.0
NUM_PER_PAGE = 100

# Phase 1: 短 2-gram 快速扫描（每词 2 页，覆盖广）
BIGRAM_TERMS = [
    "in", "er", "an", "on", "at", "es", "en", "or", "al", "ti",
    "te", "ic", "ar", "st", "re", "le", "ra", "li", "io", "nt",
    "ed", "it", "ve", "co", "de", "ri", "ro", "ne", "ma", "ta",
    "si", "el", "la", "ch", "me", "di", "un", "no", "pe", "ac",
    "ou", "se", "ca", "us", "ce", "il", "be", "pa", "mi", "to",
    "ni", "is", "po", "vi", "ci", "he", "fo", "sc", "pr", "mo",
]
BIGRAM_MAX_PAGES = 2  # 短词仅浅扫

# Phase 2: 行业/长关键词深挖
DEEP_TERMS = [
    "Software", "Consulting", "Bank", "Insurance", "Hospital",
    "Health", "Tech", "Media", "Finance", "Marketing", "Retail",
    "Energy", "Construction", "Education", "Law",
    "Food", "Transport", "Pharma", "Hotel", "Design", "Security",
    "University", "Capital", "Group", "Partners", "Global",
    "Network", "Service", "Solution", "Digital", "Medical",
    "Investment", "Manufacturing", "Agency", "Property", "Care",
    "Research", "Data", "Cloud", "Systems", "Innovation",
]

# 每关键词最大页数 (0 = 不限)
MAX_PAGES_PER_TERM = 0  # 全部
# 安全上限：单关键词最多 500 页，避免部分太大词跑太久
MAX_PAGES_CAP = 500

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

log = logging.getLogger("discovery")


class CompanyDiscoverer:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.employers = self.db[COLLECTION_EMPLOYERS]
        self.employers.create_index(
            [("employerId", ASCENDING)], unique=True, background=True
        )
        # 断点续传：记录已完成的关键词 (term+phase 唯一)
        self.progress = self.db["app_discovery_progress"]
        self.progress.create_index(
            [("key", ASCENDING)], unique=True, background=True
        )
        self._gd_id = GD_ID
        self._cf_bm = CF_BM
        self._session = requests.Session()

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

    def _post(self, body: dict, operation: str) -> tuple[int, dict]:
        headers = self._headers(operation)
        for attempt in range(3):
            try:
                resp = requests.post(
                    "https://api.glassdoor.com/mobile-graph",
                    params={"locale": "zh_CN_#Hans"},
                    headers=headers, json=body,
                    impersonate="chrome110", timeout=30,
                )
                if resp.status_code == 200 and len(resp.content) > 50:
                    return 200, resp.json()
                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    log.warning("429, waiting %ds ...", wait)
                    time.sleep(wait)
                    continue
                log.warning("HTTP %d: %s", resp.status_code, resp.text[:100])
            except Exception as e:
                log.warning("Req failed: %s", e)
            time.sleep(3)
        return 0, {}

    def _term_done(self, phase: str, term: str) -> bool:
        key = f"{phase}:{term}"
        return self.progress.find_one({"key": key}, {"_id": 1}) is not None

    def _mark_done(self, phase: str, term: str, new_count: int):
        key = f"{phase}:{term}"
        self.progress.update_one(
            {"key": key},
            {"$set": {"key": key, "phase": phase, "term": term,
                      "newCount": new_count,
                      "doneAt": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def run(self, terms: list[str] | None = None,
            max_pages: int = MAX_PAGES_PER_TERM,
            page_cap: int = MAX_PAGES_CAP,
            phase: str = "default"):
        """遍历所有关键词，分页搜索，写入 MongoDB（支持断点跳过）"""
        if terms is None:
            terms = BIGRAM_TERMS + DEEP_TERMS

        total_new = 0
        start_time = time.time()
        skipped = 0
        for idx, term in enumerate(terms, 1):
            if self._term_done(phase, term):
                skipped += 1
                continue
            log.info("[%d/%d] '%s' (skipped %d done)", idx, len(terms), term, skipped)
            term_total = 0
            term_new = 0
            crashed = False
            try:
                for page in range(1, page_cap + 1):
                    body = {
                        "operationName": "SearchCompanies",
                        "variables": {
                            "employerSearchInput": {
                                "employerName": term,
                                "numPerPage": NUM_PER_PAGE,
                                "pageRequested": page,
                            }
                        },
                        "query": SEARCH_COMPANIES_QUERY,
                        "extensions": {
                            "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
                        },
                    }
                    status, data = self._post(body, "SearchCompanies")
                    if status != 200:
                        log.info("  page %d failed, breaking", page)
                        break

                    # None 防御：GraphQL 部分错误时 data/employerSearchRG 可能为 null
                    if not isinstance(data, dict):
                        log.warning("  page %d: non-dict response, breaking", page)
                        break
                    sr = (data.get("data") or {}).get("employerSearchRG") or {}
                    results = sr.get("employerResults") or []
                    available_pages = min(sr.get("numOfPagesAvailable") or 0,
                                          page_cap)

                    if not results:
                        errs = data.get("errors")
                        if errs:
                            log.warning("  page %d GraphQL errors: %s",
                                        page, json.dumps(errs)[:200])
                        break

                    page_new = 0
                    for r in results:
                        emp = r.get("employer") or {}
                        eid = emp.get("id")
                        if not eid:
                            continue
                        if self.employers.find_one({"employerId": eid}, {"_id": 1}):
                            continue

                        ratings = r.get("employerRatings") or {}
                        counts = emp.get("counts") or {}
                        global_jobs = counts.get("globalJobCount") or {}

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
                        res = self.employers.update_one(
                            {"employerId": eid},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )
                        if res.upserted_id is not None:
                            page_new += 1

                    total_new += page_new
                    term_new += page_new
                    term_total += len(results)
                    page_note = len(results)

                    log.info("  p%d/%d: %d results, +%d new | term:+%d total:+%d",
                             page, available_pages, page_note, page_new,
                             term_total, total_new)

                    if page >= available_pages:
                        break
                    if max_pages > 0 and page >= max_pages:
                        break
                    time.sleep(DELAY_BETWEEN_PAGES)
            except Exception:
                crashed = True
                log.exception("  term '%s' crashed, skip to next (NOT marked done)",
                              term)

            elapsed = time.time() - start_time
            log.info("  Done '%s': +%d new, elapsed %.0fs", term, term_new, elapsed)
            if not crashed:
                self._mark_done(phase, term, term_new)
            time.sleep(DELAY_BETWEEN_TERMS)

        elapsed = time.time() - start_time
        log.info("=== DISCOVERY COMPLETE ===")
        log.info("Total new employers: %d", total_new)
        log.info("DB total: %d", self.employers.count_documents({}))
        log.info("Elapsed: %.0fs (%.1fmin)", elapsed, elapsed / 60)
        return total_new


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    d = CompanyDiscoverer()
    before = d.employers.count_documents({})
    log.info("=== Glassdoor Full Company Discovery ===")
    log.info("DB has %d employers before run", before)

    # Phase 1: 短 2-gram 浅扫 (2 pages each)
    log.info("\n=== Phase 1: Bigram shallow scan (%d terms, %d pages each) ===",
             len(BIGRAM_TERMS), BIGRAM_MAX_PAGES)
    n1 = d.run(terms=BIGRAM_TERMS, max_pages=BIGRAM_MAX_PAGES,
               page_cap=BIGRAM_MAX_PAGES, phase="bigram2")
    after1 = d.employers.count_documents({})
    log.info("Phase 1 done: +%d new → DB total: %d", n1, after1)

    # Phase 2: 长关键词深挖 (no page limit)
    log.info("\n=== Phase 2: Deep keyword scan (%d terms, unlimited pages) ===",
             len(DEEP_TERMS))
    n2 = d.run(terms=DEEP_TERMS, max_pages=0, page_cap=200, phase="deep")
    after2 = d.employers.count_documents({})
    log.info("Phase 2 done: +%d new → DB total: %d", n2, after2)

    total_new = n1 + n2
    log.info("\n=== ALL DONE ===")
    log.info("Total new: %d, DB: %d → %d", total_new, before, after2)


if __name__ == "__main__":
    main()
