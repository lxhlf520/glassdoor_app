"""Test review collection speed levers: pageSize limit + concurrency."""
import threading
import time
import statistics
import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

QUERY = (
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
    "    reviews { reviewId ratingOverall } } "
    "}"
)


def headers(op):
    return {
        "x-gd-id": GD_ID, "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": op, "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": ("Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                       "Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0"),
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
        "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
    }


def fetch(employer_id, page, size):
    body = {
        "operationName": "EmployerReviewsData",
        "variables": {
            "employerId": employer_id, "page": page, "pageSize": size,
            "sort": "RELEVANCE", "language": "eng",
            "applyDefaultCriteria": True,
            "employmentStatuses": ["REGULAR", "PART_TIME"],
            "location": {}, "onlyCurrentEmployees": False,
        },
        "query": QUERY,
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
    }
    t0 = time.time()
    try:
        resp = requests.post(
            "https://api.glassdoor.com/mobile-graph",
            params={"locale": "zh_CN_#Hans"},
            headers=headers("EmployerReviewsData"), json=body,
            impersonate="chrome110", timeout=60,
        )
        dt = time.time() - t0
        if resp.status_code != 200:
            return resp.status_code, 0, 0, dt
        data = resp.json()
        er = (data.get("data") or {}).get("employerReviews") or {}
        n = len(er.get("reviews") or [])
        return 200, n, er.get("numberOfPages", 0), dt
    except Exception as e:
        return -1, 0, 0, time.time() - t0


print("=== 1) pageSize limit test (Amazon 6036) ===")
for size in (20, 50, 100, 200):
    st, n, npages, dt = fetch(6036, 1, size)
    print(f"  pageSize={size:<4d} -> status={st} reviews={n:<4d} "
          f"numPages={npages:<7d} {dt:.2f}s")
    time.sleep(0.5)

print("\n=== 2) Concurrency test: N threads x 6 reqs each (pageSize=20) ===")
for workers in (3, 5, 8):
    results = []
    def worker(wid):
        for i in range(6):
            r = fetch(6036, i + 1 + wid * 6, 20)
            results.append(r)
    ts = [threading.Thread(target=worker, args=(w,)) for w in range(workers)]
    t0 = time.time()
    for t in ts: t.start()
    for t in ts: t.join()
    total = time.time() - t0
    oks = [r for r in results if r[0] == 200]
    fails = len(results) - len(oks)
    lat = [r[3] for r in oks]
    rps = len(oks) / total if total else 0
    print(f"  {workers} threads: {len(oks)}/{len(results)} ok, {fails} fail, "
          f"total {total:.1f}s, {rps:.2f} req/s, "
          f"avg {statistics.mean(lat):.2f}s p95 {statistics.quantiles(lat, n=20)[-1]:.2f}s")
    time.sleep(2)
