"""Diagnose 429 ban: direct vs Clash proxy egress."""
import time
import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

QUERY = (
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!, "
    "$sort: ReviewsSortOrderEnum, $language: String) { "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId } sort: $sort "
    "    page: { num: $page size: $pageSize } "
    "    worldwideFilter: true useRowProfileTldForRatings: false language: $language "
    "  }) { filteredReviewsCount numberOfPages reviews { reviewId } } "
    "}"
)

headers = {
    "x-gd-id": GD_ID, "x-gd-asst": f"{time.time()}.0",
    "x-gd-operation": "EmployerReviewsData", "gd-csrf-token": "android",
    "apollographql-client-name": "android",
    "apollographql-client-version": "12.21.0",
    "content-type": "application/json",
    "user-agent": ("Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                   "Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0"),
    "accept": "application/json",
    "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
}
body = {
    "operationName": "EmployerReviewsData",
    "variables": {"employerId": 6036, "page": 1, "pageSize": 100,
                  "sort": "RELEVANCE", "language": "eng"},
    "query": QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

def test(label, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.post("https://api.glassdoor.com/mobile-graph",
                          params={"locale": "zh_CN_#Hans"},
                          headers=headers, json=body,
                          impersonate="chrome110", timeout=30, proxies=proxies)
        n = 0
        if r.status_code == 200:
            er = (r.json().get("data") or {}).get("employerReviews") or {}
            n = len(er.get("reviews") or [])
        print(f"{label}: status={r.status_code} reviews={n}")
        return r.status_code
    except Exception as e:
        print(f"{label}: ERROR {str(e)[:120]}")
        return -1

test("direct      ")
time.sleep(1)
test("clash-proxy ", proxy="http://127.0.0.1:7890")
