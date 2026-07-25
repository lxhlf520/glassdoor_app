"""测试 SearchCompanies 分页 + 大批量"""
import json, time
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


def search_companies(term: str, page: int = 1, num_per_page: int = 100):
    url = "https://api.glassdoor.com/mobile-graph"
    params = {"locale": "zh_CN_#Hans"}
    headers = {
        "x-gd-id": GD_ID,
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": "SearchCompanies",
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0",
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
        "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
    }
    body = {
        "operationName": "SearchCompanies",
        "variables": {
            "employerSearchInput": {
                "employerName": term,
                "numPerPage": num_per_page,
                "pageRequested": page,
            }
        },
        "query": QUERY,
        "extensions": {
            "clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}
        },
    }

    resp = requests.post(url, params=params, headers=headers, json=body,
                         impersonate="chrome110", timeout=30)
    if resp.status_code == 200 and len(resp.content) > 100:
        data = resp.json()
        er = data.get("data", {}).get("employerSearchRG", {})
        pages = er.get("numOfPagesAvailable", 0)
        results = er.get("employerResults", [])
        ids = [r["employer"]["id"] for r in results]
        return pages, ids, results
    return 0, [], []


# 测试 numPerPage 上限和分页
for term, max_page in [("Apple", 3), ("Tech", 2), ("Health", 2)]:
    for num_per in [100, 50, 20]:
        pages, ids, _ = search_companies(term, page=1, num_per_page=num_per)
        print(f"'{term}' numPerPage={num_per}: page1/{pages} pages, got {len(ids)} ids")
        break  # 只测一种 numPerPage
    print(f"  IDs: {ids[:5]}... ({len(ids)} total)")

    # 如果有多页，取第2页
    if pages > 1:
        _, ids2, _ = search_companies(term, page=2, num_per_page=20)
        print(f"  Page 2: {len(ids2)} ids")
    time.sleep(2)
