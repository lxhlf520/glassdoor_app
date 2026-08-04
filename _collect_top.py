"""Collect all 4 modules for Amazon/Apple/Google into PG, then verify."""
import sys, time, json, logging
from datetime import datetime, timezone
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("collect")

import psycopg2
from glassdoor_collector.config import PG_CONFIG
from glassdoor_collector.db import init_all_tables
from glassdoor_collector.infra import fetch_graphql
from glassdoor_collector.parallel import (
    REVIEWS_QUERY, fetch_page, transform_review,
    REVIEW_COLUMNS, _review_doc_to_row,
)
from glassdoor_collector.modules import (
    BENEFITS_QUERY, INTERVIEWS_QUERY, JOBS_QUERY,
    BenefitsCollector, InterviewsCollector, JobsCollector,
    transform_benefit_review, transform_interview, transform_job,
    BENEFIT_COLUMNS, INTERVIEW_COLUMNS, JOB_COLUMNS,
    _benefit_doc_to_row, _interview_doc_to_row, _job_doc_to_row,
)

COMPANIES = [
    (6036, "Amazon"),
    (1138, "Apple"),
    (9079, "Google"),
]

MAX_REVIEW_PAGES = 30  # 每公司最多采集页数（验证用，非全量）

# ── init ──
log.info("Initializing tables...")
init_all_tables()
conn = psycopg2.connect(**PG_CONFIG)

def bulk_insert(table, cols, rows, conflict_key):
    """Insert rows (each is a tuple) with ON CONFLICT DO NOTHING."""
    if not rows:
        return 0
    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT ({conflict_key}) DO NOTHING"
    cur.executemany(sql, rows)
    count = cur.rowcount
    conn.commit()
    cur.close()
    return count


def safe_num(v):
    """Convert rating values to float. API sometimes returns strings like POSITIVE/NEGATIVE."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════
# MODULE 1: REVIEWS (using parallel.py logic)
# ═══════════════════════════════════════════
log.info("=" * 50)
log.info("MODULE: reviews")

# Clear existing for these companies
cur = conn.cursor()
for eid, _ in COMPANIES:
    cur.execute("DELETE FROM reviews WHERE employer_id = %s", (eid,))
    cur.execute("DELETE FROM review_progress WHERE employer_id = %s", (eid,))
conn.commit()
cur.close()

for eid, ename in COMPANIES:
    log.info(f"  Reviews: {ename} (eid={eid})")
    total_items = 0
    page = 1
    total_pages = 99999
    
    while page <= total_pages:
        for retry in range(3):
            status, data = fetch_page(eid, page)
            if status == 200:
                break
            log.warning(f"    page={page} retry {retry+1}/3 (HTTP {status})")
            time.sleep(3)
        else:
            log.error(f"    page={page} FAILED after 3 retries")
            break
        
        er = (data.get("data") or {}).get("employerReviews") or {}
        reviews = er.get("reviews") or []
        
        if page == 1:
            actual_pages = min(er.get("numberOfPages") or 0, 99999)
            total_pages = min(actual_pages, MAX_REVIEW_PAGES)
            log.info(f"    page=1 total={er.get('filteredReviewsCount')} pages={actual_pages} collecting={total_pages}")
            if not reviews or total_pages == 0:
                break
        
        rows = []
        for r in reviews:
            doc = transform_review(r, eid, ename, page)
            # Fix: API sometimes returns POSITIVE/NEGATIVE strings for numeric fields
            for k in ("ratingOverall", "ratingRecommendToFriend", "ratingCeo",
                      "ratingBusinessOutlook", "ratingCareerOpportunities",
                      "ratingCompensationAndBenefits", "ratingCultureAndValues",
                      "ratingDiversityAndInclusion", "ratingSeniorLeadership",
                      "ratingWorkLifeBalance"):
                if k in doc:
                    doc[k] = safe_num(doc[k])
            rows.append(_review_doc_to_row(doc))
        
        n = bulk_insert("reviews", REVIEW_COLUMNS, rows, "review_id")
        total_items += n
        log.info(f"    page={page} fetched={len(reviews)} inserted={n}")
        page += 1
        time.sleep(0.3)
    
    log.info(f"    DONE: {total_items} reviews for {ename}")


# ═══════════════════════════════════════════
# MODULE 2: BENEFITS
# ═══════════════════════════════════════════
log.info("=" * 50)
log.info("MODULE: benefits")

# Clear & re-collect
cur = conn.cursor()
for eid, _ in COMPANIES:
    cur.execute("DELETE FROM benefits WHERE employer_id = %s", (eid,))
    cur.execute("DELETE FROM benefits_progress WHERE employer_id = %s", (eid,))
conn.commit()
cur.close()

for eid, ename in COMPANIES:
    log.info(f"  Benefits: {ename} (eid={eid})")
    
    # Probe countries
    body = {
        "operationName": "EmployerBenefits",
        "variables": {"employerId": eid, "countryId": 1, "benefitsReviewsPageNumber": 1, "benefitsReviewsPageSize": 1, "employmentStatus": "REGULAR"},
        "query": BENEFITS_QUERY,
    }
    status, data = fetch_graphql("EmployerBenefits", body, timeout=60)
    countries = (data.get("data") or {}).get("countriesForEmployerBenefits") or []
    log.info(f"    countries: {[(c.get('id'), c.get('name','')[:30]) for c in countries[:10]]}")
    
    total_items = 0
    for country in countries:
        cid = country.get("id")
        cname = country.get("name", "")
        if not cid:
            continue
        
        page = 1
        while True:
            body = {
                "operationName": "EmployerBenefits",
                "variables": {"employerId": eid, "countryId": cid, "benefitsReviewsPageNumber": page, "benefitsReviewsPageSize": 10000, "employmentStatus": "REGULAR"},
                "query": BENEFITS_QUERY,
            }
            for retry in range(3):
                status, data = fetch_graphql("EmployerBenefits", body, timeout=120)
                if status == 200:
                    break
                log.warning(f"    {cname} page={page} retry {retry+1}/3 (HTTP {status})")
                time.sleep(3)
            else:
                log.error(f"    {cname} page={page} FAILED after 3 retries")
                break
            
            br = (data.get("data") or {}).get("overviewBenefitReviews") or []
            n = len(br) if isinstance(br, list) else 0
            
            if page == 1:
                ov = (data.get("data") or {}).get("benefitsOverviewForCountry") or {}
                log.info(f"    {cname} (cid={cid}) total={ov.get('totalBenefitReviews',0)}")
            
            if n == 0:
                break
            
            docs = [transform_benefit_review(r, eid, ename, cid) for r in br]
            rows = [_benefit_doc_to_row(doc) for doc in docs]
            cnt = bulk_insert("benefits", BENEFIT_COLUMNS, rows, "benefit_review_id, employer_id")
            total_items += cnt
            log.info(f"    {cname} page={page} fetched={n} inserted={cnt}")
            page += 1
            time.sleep(0.2)
    
    log.info(f"    DONE: {total_items} benefit reviews for {ename}")


# ═══════════════════════════════════════════
# MODULE 3: INTERVIEWS
# ═══════════════════════════════════════════
log.info("=" * 50)
log.info("MODULE: interviews")

cur = conn.cursor()
for eid, _ in COMPANIES:
    cur.execute("DELETE FROM interviews WHERE employer_id = %s", (eid,))
    cur.execute("DELETE FROM interviews_progress WHERE employer_id = %s", (eid,))
conn.commit()
cur.close()

for eid, ename in COMPANIES:
    log.info(f"  Interviews: {ename} (eid={eid})")
    total_items = 0
    page = 1
    total_pages = 99999
    PAGE_SZ = 1000  # 5000 会对某些 employer 触发 INTERNAL 错误
    
    while page <= total_pages:
        body = {
            "operationName": "EmployerInterviewsList",
            "variables": {"employerId": eid, "page": page, "pageSize": PAGE_SZ, "sort": "RELEVANCE"},
            "query": INTERVIEWS_QUERY,
        }
        for retry in range(3):
            status, data = fetch_graphql("EmployerInterviewsList", body, timeout=120)
            if status == 200 and not data.get("errors"):
                break
            if data.get("errors"):
                err_msg = data["errors"][0].get("message", "")[:80]
                log.warning(f"    page={page} retry {retry+1}/3 GraphQL error: {err_msg}")
            else:
                log.warning(f"    page={page} retry {retry+1}/3 (HTTP {status})")
            time.sleep(3)
        else:
            log.error(f"    page={page} FAILED after 3 retries")
            break
        
        il = (data.get("data") or {}).get("employerInterviewsList") or {}
        items = il.get("interviews") or []
        
        if page == 1:
            total_pages = min(il.get("totalNumberOfPages") or 0, 99999)
            log.info(f"    page=1 total={il.get('filteredInterviewCount')} pages={total_pages}")
        
        if not items:
            log.info(f"    page={page} empty, done")
            break
        
        docs = [transform_interview(r, eid, ename, page) for r in items]
        rows = [_interview_doc_to_row(doc) for doc in docs]
        n = bulk_insert("interviews", INTERVIEW_COLUMNS, rows, "interview_id")
        total_items += n
        log.info(f"    page={page} fetched={len(items)} inserted={n}")
        page += 1
        time.sleep(0.3)
    
    log.info(f"    DONE: {total_items} interviews for {ename}")


# ═══════════════════════════════════════════
# MODULE 4: JOBS
# ═══════════════════════════════════════════
log.info("=" * 50)
log.info("MODULE: jobs")

cur = conn.cursor()
for eid, _ in COMPANIES:
    cur.execute("DELETE FROM jobs WHERE employer_id = %s", (eid,))
    cur.execute("DELETE FROM jobs_progress WHERE employer_id = %s", (eid,))
conn.commit()
cur.close()

for eid, ename in COMPANIES:
    log.info(f"  Jobs: {ename} (eid={eid})")
    total_items = 0
    page = 1
    total_pages = 99999
    PAGE_SZ = 100
    
    while page <= total_pages:
        body = {
            "operationName": "JobsSearchAndroid",
            "variables": {
                "pageTypeEnum": "SERP",
                "searchParams": {
                    "filterParams": [{"filterKey": "employerId", "values": str(eid)}],
                    "pageNumber": page,
                    "numPerPage": PAGE_SZ,
                },
            },
            "query": JOBS_QUERY,
        }
        for retry in range(3):
            status, data = fetch_graphql("JobsSearchAndroid", body, timeout=60)
            if status == 200:
                break
            log.warning(f"    page={page} retry {retry+1}/3 (HTTP {status})")
            time.sleep(3)
        else:
            log.error(f"    page={page} FAILED after 3 retries")
            break
        
        jl = (data.get("data") or {}).get("jobListings") or {}
        items = jl.get("jobListings") or []
        
        if page == 1:
            total = jl.get("totalJobsCount") or 0
            total_pages = min((total + PAGE_SZ - 1) // PAGE_SZ, 99999) if total else 1
            log.info(f"    page=1 total={total} pages={total_pages}")
        
        if not items:
            break
        
        docs = [transform_job(it.get("jobview") or {}, eid, ename, page) for it in items]
        rows = [_job_doc_to_row(doc) for doc in docs if doc.get("listingId")]
        if rows:
            n = bulk_insert("jobs", JOB_COLUMNS, rows, "listing_id, employer_id")
            total_items += n
            log.info(f"    page={page} fetched={len(items)} inserted={n}")
        page += 1
        time.sleep(0.3)
    
    log.info(f"    DONE: {total_items} jobs for {ename}")


# ═══════════════════════════════════════════
# VERIFY
# ═══════════════════════════════════════════
log.info("=" * 50)
log.info("VERIFICATION")

cur = conn.cursor()
for table in ["reviews", "benefits", "interviews", "jobs"]:
    parts = [f"\n  {table}:"]
    for eid, ename in COMPANIES:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE employer_id = %s", (eid,))
        cnt = cur.fetchone()[0]
        parts.append(f"  {ename}={cnt}")
    log.info("".join(parts))

cur.close()
conn.close()
log.info("ALL DONE")
