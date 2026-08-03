"""快速测试各模块 GraphQL endpoint 是否正常返回数据。

用法：
    uv run _test_modules.py
    uv run _test_modules.py --eid 1138,222,3514
    uv run _test_modules.py --modules benefits,jobs

不写 progress 表，纯检查 API 连通性和数据格式。
"""
import argparse
import json
import logging
import time
from datetime import datetime, timezone

from glassdoor_collector.infra import (
    fetch_graphql, rate_limiter, TUNNEL_MODE, ACTIVE_PROXY,
)
from glassdoor_collector.modules import (
    BENEFITS_QUERY, INTERVIEWS_QUERY, JOBS_QUERY,
    transform_benefit_review, transform_interview, transform_job,
)

log = logging.getLogger("test")

# 测试用的雇主列表（知名大公司，数据量充足）
DEFAULT_EIDS = [
    1138,       # Apple Inc.
    222,        # Ecolab
    3514,       # The HEINEKEN Company
    2841163,    # TELUS Digital
    1596815,    # DeepMind Technologies Limited
]


def test_benefits(eid: int) -> dict:
    """测试 benefits endpoint，只拉第 1 页。"""
    body = {
        "operationName": "EmployerBenefits",
        "variables": {
            "employerId": eid,
            "countryId": 1,
            "benefitsReviewsPageNumber": 1,
            "benefitsReviewsPageSize": 20,
            "employmentStatus": "REGULAR",
        },
        "query": BENEFITS_QUERY,
    }
    rate_limiter.acquire()
    status, data = fetch_graphql("EmployerBenefits", body, timeout=30)

    result = {"employerId": eid, "status": status}
    if status != 200:
        result["error"] = "HTTP status or network error"
        return result

    d = data.get("data") or {}
    overview = (d.get("benefitsOverviewForCountry") or {})
    reviews = d.get("overviewBenefitReviews") or []
    countries = d.get("countriesForEmployerBenefits") or []
    result["totalReviews"] = overview.get("totalBenefitReviews", 0)
    result["reviewsPage1"] = len(reviews) if isinstance(reviews, list) else 0
    result["countries"] = len(countries) if isinstance(countries, list) else 0

    if reviews and isinstance(reviews, list):
        r0 = reviews[0]
        doc = transform_benefit_review(r0, eid, "test", 1)
        result["sampleReviewId"] = doc.get("benefitReviewId")
        result["sampleRating"] = doc.get("rating")
    return result


def test_interviews(eid: int) -> dict:
    """测试 interviews endpoint，只拉第 1 页。"""
    body = {
        "operationName": "EmployerInterviewsList",
        "variables": {
            "employerId": eid,
            "page": 1,
            "pageSize": 50,
            "sort": "RELEVANCE",
        },
        "query": INTERVIEWS_QUERY,
    }
    rate_limiter.acquire()
    status, data = fetch_graphql("EmployerInterviewsList", body, timeout=30)

    result = {"employerId": eid, "status": status}
    if status != 200:
        result["error"] = "HTTP status or network error"
        return result

    il = ((data.get("data") or {}).get("employerInterviewsList") or {})
    interviews = il.get("interviews") or []
    result["totalPages"] = il.get("totalNumberOfPages", 0)
    result["interviewsPage1"] = len(interviews) if isinstance(interviews, list) else 0

    if interviews and isinstance(interviews, list):
        r0 = interviews[0]
        doc = transform_interview(r0, eid, "test", 1)
        result["sampleInterviewId"] = doc.get("interviewId")
        result["sampleDifficulty"] = doc.get("difficulty")
        result["sampleOutcome"] = doc.get("outcome")
    return result


def test_jobs(eid: int) -> dict:
    """测试 jobs endpoint，只拉第 1 页。"""
    body = {
        "operationName": "JobsSearchAndroid",
        "variables": {
            "pageTypeEnum": "SERP",
            "searchParams": {
                "filterParams": [
                    {"filterKey": "employerId", "values": str(eid)}
                ],
                "pageNumber": 1,
            },
        },
        "query": JOBS_QUERY,
    }
    rate_limiter.acquire()
    status, data = fetch_graphql("JobsSearchAndroid", body, timeout=30)

    result = {"employerId": eid, "status": status}
    if status != 200:
        result["error"] = "HTTP status or network error"
        return result

    jl = ((data.get("data") or {}).get("jobListings") or {})
    items = jl.get("jobListings") or []
    total = jl.get("totalJobsCount") or 0
    result["totalJobs"] = total
    result["jobsPage1"] = len(items) if isinstance(items, list) else 0

    if items and isinstance(items, list):
        it0 = items[0]
        jv = it0.get("jobview") or {}
        doc = transform_job(jv, eid, "test", 1)
        result["sampleListingId"] = doc.get("listingId")
        result["sampleJobTitle"] = doc.get("jobTitleText")
        result["sampleGoc"] = doc.get("goc")
        result["gocType"] = type(doc.get("goc")).__name__
    return result


def summarize(results: list[dict], module: str):
    """打印单个模块的汇总。"""
    ok = [r for r in results if r["status"] == 200]
    fail = [r for r in results if r["status"] != 200]
    empty = [r for r in ok if _is_empty(r, module)]

    print(f"\n{'='*60}")
    print(f"  {module.upper()} — {len(ok)} OK, {len(fail)} FAIL, {len(empty)} EMPTY")
    print(f"{'='*60}")
    for r in fail:
        print(f"  ✗ eid={r['employerId']}  status={r['status']}  {r.get('error','')}")
    for r in empty:
        print(f"  ~ eid={r['employerId']}  {_empty_reason(r, module)}")
    for r in ok:
        if r not in empty:
            print(f"  ✓ eid={r['employerId']}  {_ok_summary(r, module)}")


def _is_empty(r: dict, module: str) -> bool:
    if module == "benefits":
        return r.get("totalReviews", 0) == 0 and r.get("reviewsPage1", 0) == 0
    elif module == "interviews":
        return r.get("totalPages", 0) == 0 and r.get("interviewsPage1", 0) == 0
    elif module == "jobs":
        return r.get("totalJobs", 0) == 0 and r.get("jobsPage1", 0) == 0
    return False


def _empty_reason(r: dict, module: str) -> str:
    if module == "benefits":
        return f"totalReviews=0 reviews={r.get('reviewsPage1',0)} countries={r.get('countries',0)}"
    elif module == "interviews":
        return f"totalPages=0 interviews=0"
    elif module == "jobs":
        return f"totalJobs=0 jobs=0"
    return "no data"


def _ok_summary(r: dict, module: str) -> str:
    if module == "benefits":
        return (f"reviews={r.get('reviewsPage1',0)} total={r.get('totalReviews',0)} "
                f"sample={r.get('sampleReviewId','?')} rating={r.get('sampleRating','?')}")
    elif module == "interviews":
        return (f"interviews={r.get('interviewsPage1',0)} pages={r.get('totalPages',0)} "
                f"sample={r.get('sampleInterviewId','?')} difficulty={r.get('sampleDifficulty','?')}")
    elif module == "jobs":
        return (f"jobs={r.get('jobsPage1',0)} total={r.get('totalJobs',0)} "
                f"listing={r.get('sampleListingId','?')} goc={r.get('sampleGoc','?')}({r.get('gocType','?')})")
    return ""


def main():
    parser = argparse.ArgumentParser(description="测试 Glassdoor 模块 API")
    parser.add_argument("--eid", type=str, default=None,
                        help="逗号分隔的 employer_id，默认测试知名大公司")
    parser.add_argument("--modules", default="all",
                        help="benefits,interviews,jobs 或 all")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if args.eid:
        eids = [int(x.strip()) for x in args.eid.split(",") if x.strip()]
    else:
        eids = DEFAULT_EIDS

    modules = [m.strip() for m in args.modules.lower().split(",")]
    if "all" in modules:
        modules = ["benefits", "interviews", "jobs"]

    print(f"Proxy: {'tunnel' if TUNNEL_MODE else 'clash'} → {ACTIVE_PROXY}")
    print(f"Testing {len(eids)} employers: {eids}")
    print(f"Modules: {modules}")

    overall = {}

    for mod in modules:
        results = []
        for eid in eids:
            log.info("Testing %s eid=%d", mod, eid)
            try:
                if mod == "benefits":
                    r = test_benefits(eid)
                elif mod == "interviews":
                    r = test_interviews(eid)
                elif mod == "jobs":
                    r = test_jobs(eid)
                else:
                    log.warning("Unknown module: %s", mod)
                    continue
                results.append(r)
            except Exception as e:
                log.warning("Exception testing %s eid=%d: %s", mod, eid, e)
                results.append({"employerId": eid, "status": -1, "error": str(e)[:100]})
            time.sleep(0.5)  # 避免打太快
        summarize(results, mod)
        overall[mod] = results

    # 最终汇总
    print(f"\n{'='*60}")
    print("  FINAL SUMMARY")
    print(f"{'='*60}")
    for mod, results in overall.items():
        ok = sum(1 for r in results if r["status"] == 200)
        fail = sum(1 for r in results if r["status"] != 200)
        print(f"  {mod:12s}: {ok}/{len(results)} OK, {fail} FAIL")


if __name__ == "__main__":
    main()
