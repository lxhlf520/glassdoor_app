"""Quick cookie validity check with one SearchCompanies request."""
import time
import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

QUERY = (
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

headers = {
    "x-gd-id": GD_ID,
    "x-gd-asst": f"{time.time()}.0",
    "x-gd-operation": "SearchCompanies",
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
    "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
}

body = {
    "operationName": "SearchCompanies",
    "variables": {"employerSearchInput": {
        "employerName": "ou", "numPerPage": 100, "pageRequested": 1}},
    "query": QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

resp = requests.post(
    "https://api.glassdoor.com/mobile-graph",
    params={"locale": "zh_CN_#Hans"},
    headers=headers, json=body,
    impersonate="chrome110", timeout=30,
)
print("status:", resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    sr = data.get("data", {}).get("employerSearchRG", {})
    results = sr.get("employerResults", [])
    print("results:", len(results), "pages:", sr.get("numOfPagesAvailable"))
    print("COOKIE OK")
else:
    print(resp.text[:300])
    print("COOKIE EXPIRED or blocked")
