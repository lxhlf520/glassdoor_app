"""Test api.glassdoor.com/mobile-graph through current node."""
import sys, time, json
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
import curl_cffi.requests as creq

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

api = ClashAPI()
print("node:", api.current())

headers = {
    "x-gd-id": GD_ID,
    "x-gd-asst": f"{time.time()}.0",
    "x-gd-operation": "EmployerReviewsData",
    "gd-csrf-token": "android",
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

query = ("query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!) { "
         "  employerReviews: employerReviewsRG(employerReviewsInput: { "
         "    employer: { id: $employerId } "
         "    page: { num: $page size: $pageSize } "
         "  }) { filteredReviewsCount numberOfPages reviews { reviewId } } }")

body = {
    "operationName": "EmployerReviewsData",
    "variables": {"employerId": 1138, "page": 1, "pageSize": 100},
    "query": query,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

px = {"http": api.mixed, "https": api.mixed}
r = creq.post("https://api.glassdoor.com/mobile-graph",
              params={"locale": "zh_CN_#Hans"}, headers=headers, json=body,
              proxies=px, impersonate="chrome110", timeout=30)
print("status:", r.status_code, "len:", len(r.content))
print("cf-mitigated:", r.headers.get("cf-mitigated"))
if r.status_code == 200:
    d = r.json()
    er = d.get("data", {}).get("employerReviews", {})
    print("reviews:", len(er.get("reviews", [])),
          "total:", er.get("filteredReviewsCount"),
          "pages:", er.get("numberOfPages"))
else:
    print(r.text[:300])
