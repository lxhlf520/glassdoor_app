"""Glassdoor APP 评论采集器（legacy 单线程版）— 纯协议调用，存储至 PostgreSQL"""
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import curl_cffi.requests as requests

from .config import (
    CF_BM,
    COLLECTOR_DELAY_EMPLOYERS as DELAY_BETWEEN_EMPLOYERS,
    COLLECTOR_DELAY_PAGES as DELAY_BETWEEN_PAGES,
    COLLECTOR_DISCOVERY_MAX_PAGES as DISCOVERY_MAX_PAGES,
    COLLECTOR_DISCOVERY_PER_PAGE as DISCOVERY_PER_PAGE,
    COLLECTOR_DISCOVERY_TERMS as DISCOVERY_TERMS,
    COLLECTOR_MAX_RETRIES as MAX_RETRIES,
    COLLECTOR_PAGE_SIZE as PAGE_SIZE,
    GD_ID,
    SEED_EMPLOYERS,
)
from .db import get_conn, init_all_tables, put_conn

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
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!) { "
    "  employer(id: $employerId) { primaryIndustryId name shortName } "
    "  reviewLocationsRG(employer: { id: $employerId } ) "
    "    { locations { id name type children { id name type "
    "    children { id name type children { id name type "
    "    children { id name type } } } } } }  "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId }  "
    "    page: { num: $page size: $pageSize }  "
    "    worldwideFilter: true "
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
    "    queryJobTitle { mgocId } "
    "  } "
    "}"
)

log = logging.getLogger("glassdoor")


class GlassdoorCollector:
    """Glassdoor APP 评论采集器（legacy 单线程版）"""

    def __init__(self) -> None:
        init_all_tables()
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
        new_count = 0
        for page in range(1, total_pages + 1):
            # 断点检查
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM reviews WHERE employer_id = %s AND page = %s LIMIT 1",
                        (employer_id, page))
                    if cur.fetchone():
                        log.debug("  page %d already collected, skip", page)
                        continue
            finally:
                put_conn(conn)

            body = {
                "operationName": "EmployerReviewsData",
                "variables": {
                    "employerId": employer_id,
                    "page": page,
                    "pageSize": PAGE_SIZE,
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
                conn2 = get_conn()
                try:
                    with conn2.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO reviews (
                                   review_id, employer_id, employer_name, page,
                                   featured, review_date_time, summary, is_current_job,
                                   length_of_employment, location_id, location_name,
                                   rating_overall, rating_recommend, rating_ceo,
                                   rating_business_outlook, rating_career_opp,
                                   rating_comp_benefits, rating_culture_values,
                                   rating_diversity, rating_senior_leadership,
                                   rating_work_life_balance,
                                   pros, cons, advice, count_helpful,
                                   has_employer_response, employer_responses,
                                   job_title, collected_at
                               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                         %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                         %s, %s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (review_id) DO NOTHING""",
                            [(
                                d["reviewId"], d["employerId"], d.get("employerName", ""),
                                d.get("page", 0), d.get("featured", False),
                                d.get("reviewDateTime"), d.get("summary"),
                                d.get("isCurrentJob"), d.get("lengthOfEmployment"),
                                d.get("locationId"), d.get("locationName"),
                                d.get("ratingOverall"), d.get("ratingRecommendToFriend"),
                                d.get("ratingCeo"), d.get("ratingBusinessOutlook"),
                                d.get("ratingCareerOpportunities"),
                                d.get("ratingCompensationAndBenefits"),
                                d.get("ratingCultureAndValues"),
                                d.get("ratingDiversityAndInclusion"),
                                d.get("ratingSeniorLeadership"),
                                d.get("ratingWorkLifeBalance"),
                                d.get("pros"), d.get("cons"), d.get("advice"),
                                d.get("countHelpful", 0),
                                d.get("hasEmployerResponse", False),
                                json.dumps(d.get("employerResponses") or []),
                                d.get("jobTitle"), d.get("captured_at"),
                            ) for d in docs])
                        inserted = cur.rowcount
                        conn2.commit()
                        new_count += inserted
                finally:
                    put_conn(conn2)

            log.info("  page %d/%d: %d reviews (new: %d)",
                     page, total_pages, len(reviews), new_count)

            delay = random.uniform(*DELAY_BETWEEN_PAGES)
            time.sleep(delay)

            actual_pages = er.get("numberOfPages", total_pages)
            if actual_pages > total_pages:
                total_pages = actual_pages
                log.info("  total_pages updated to %d", total_pages)

        return new_count

    @staticmethod
    def _transform_review(r: dict, employer_id: int, employer_name: str,
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
            "captured_at": datetime.now(timezone.utc),
        }

    # ------------------------------------------------------------------
    # 公司列表 / 发现
    # ------------------------------------------------------------------
    def discover_companies(self, terms: list[str] | None = None,
                            max_pages: int = DISCOVERY_MAX_PAGES) -> int:
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

                batch = []
                for r in results:
                    emp = r.get("employer", {})
                    eid = emp.get("id")
                    if not eid:
                        continue

                    ratings = r.get("employerRatings", {}) or {}
                    counts = emp.get("counts", {}) or {}
                    global_jobs = counts.get("globalJobCount", {}) or {}

                    batch.append((
                        eid,
                        emp.get("shortName", ""),
                        emp.get("shortName", ""),
                        emp.get("squareLogoUrl"),
                        ratings.get("overallRating"),
                        counts.get("reviewCount", 0),
                        counts.get("salaryCount", 0),
                        global_jobs.get("jobCount", 0),
                        term,
                        datetime.now(timezone.utc),
                    ))

                if batch:
                    conn = get_conn()
                    try:
                        with conn.cursor() as cur:
                            cur.executemany(
                                """INSERT INTO employers (
                                       employer_id, name, short_name, logo_url,
                                       overall_rating, review_count, salary_count,
                                       job_count, discovered_via, discovered_at
                                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (employer_id) DO NOTHING""",
                                batch)
                            inserted = cur.rowcount
                            conn.commit()
                            new_count += inserted
                            total_results += len(batch)
                    finally:
                        put_conn(conn)

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
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT employer_id FROM employers
                       WHERE review_count >= %s
                       ORDER BY review_count DESC""",
                    (min_reviews,))
                return [row[0] for row in cur.fetchall()]
        finally:
            put_conn(conn)

    def seed_employers(self) -> None:
        for eid in SEED_EMPLOYERS:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM employers WHERE employer_id = %s", (eid,))
                    if cur.fetchone():
                        continue
            finally:
                put_conn(conn)

            name = ""
            try:
                body = {
                    "operationName": "EmployerReviewsData",
                    "variables": {
                        "employerId": eid,
                        "page": 1, "pageSize": 1,
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

            conn2 = get_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        """INSERT INTO employers (employer_id, name, discovered_at)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (employer_id) DO UPDATE SET name = EXCLUDED.name""",
                        (eid, name, datetime.now(timezone.utc)))
                    conn2.commit()
            finally:
                put_conn(conn2)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def collect_all(self, employer_ids: list[int] | None = None,
                     max_employers: int = 0,
                     max_pages_per_employer: int = 0) -> dict:
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

                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO employers (employer_id, name)
                               VALUES (%s, %s)
                               ON CONFLICT (employer_id) DO UPDATE SET name = EXCLUDED.name""",
                            (eid, name))
                        conn.commit()
                finally:
                    put_conn(conn)

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

    log.info("=== Phase 1: Discover companies ===")
    test_terms = ["Software", "Consulting", "Tech", "Health", "Finance"]
    new_employers = collector.discover_companies(terms=test_terms, max_pages=3)
    log.info("Discovered %d new employers", new_employers)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM employers")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM employers WHERE review_count > 0")
            with_reviews = cur.fetchone()[0]
    finally:
        put_conn(conn)
    log.info("Total employers in DB: %d (%d with reviews)", total, with_reviews)

    employer_ids = collector.get_employer_ids(min_reviews=10)
    log.info("=== Phase 2: Collect reviews for %d employers ===", len(employer_ids))

    stats = collector.collect_all(
        employer_ids=employer_ids,
        max_employers=50,
        max_pages_per_employer=10,
    )
    print(f"\nDone. {stats['total_new']} new reviews across {stats['companies']} companies.")


if __name__ == "__main__":
    main()
