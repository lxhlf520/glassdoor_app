"""Glassdoor API 限流压力测试 — 逐步降低延迟找到 429 阈值"""
import json
import time
import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

# --------- SearchCompanies ----------
SEARCH_QUERY = (
    "query SearchCompanies($input: EmployerSearchInput) { "
    "  employerSearchRG(employerSearchInput: $input) { "
    "    employerResults { employer { id shortName "
    "      counts { reviewCount } } "
    "    employerRatings { overallRating } "
    "    } numOfPagesAvailable pageNumber } }"
)

REVIEWS_QUERY = (
    "query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!) { "
    "  employer(id: $employerId) { name } "
    "  employerReviews: employerReviewsRG(employerReviewsInput: { "
    "    employer: { id: $employerId } sort: RELEVANCE "
    "    page: { num: $page size: $pageSize } worldwideFilter: true "
    "  }) { filteredReviewsCount } }"
)

URL = "https://api.glassdoor.com/mobile-graph"
BASE_HEADERS = {
    "x-gd-id": GD_ID,
    "x-gd-asst": "",
    "gd-csrf-token": "android",
    "x-gd-glassbowl-user": "false",
    "apollographql-client-name": "android",
    "apollographql-client-version": "12.21.0",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0",
    "accept": "multipart/mixed; deferSpec=20220824, application/json",
    "cookie": f"gdId={GD_ID}; __cf_bm={CF_BM}",
}


def make_request(operation: str, body: dict, delay: float) -> tuple[int, int]:
    """返回 (status_code, response_len)"""
    headers = BASE_HEADERS.copy()
    headers["x-gd-asst"] = f"{time.time()}.0"
    headers["x-gd-operation"] = operation
    try:
        resp = requests.post(
            URL,
            params={"locale": "zh_CN_#Hans"},
            headers=headers, json=body,
            impersonate="chrome110", timeout=30,
        )
        time.sleep(delay)
        return resp.status_code, len(resp.content)
    except Exception as e:
        return 0, 0


def stress_test(name: str, operation: str, body: dict,
                delays: list[float], rounds: int = 3,
                warmup: int = 2):
    """Running stress test with decreasing delays to find 429 threshold"""
    print(f"\n{'='*60}")
    print(f"STRESS TEST: {name}")
    print(f"  Delay levels: {delays}s")
    print(f"  Rounds per level: {rounds}")
    print(f"{'='*60}")

    # warmup
    print(f"\n[WARMUP] {warmup} requests with 5s delay...")
    for _ in range(warmup):
        s, l = make_request(operation, body, 5.0)
        print(f"  status={s} len={l}")

    results = {}
    for delay in delays:
        stats = {"200": 0, "429": 0, "other": 0, "avg_len": 0}
        print(f"\n--- delay={delay}s ---")
        for i in range(rounds):
            s, l = make_request(operation, body, delay)
            if s == 200:
                stats["200"] += 1
            elif s == 429:
                stats["429"] += 1
            else:
                stats["other"] += 1
            stats["avg_len"] += l
            marker = "✓" if s == 200 else "✗" if s == 429 else "?"
            print(f"  [{i+1}/{rounds}] {marker} status={s} len={l}")
        stats["avg_len"] //= rounds
        results[delay] = stats

    print(f"\n--- RESULTS: {name} ---")
    print(f"{'Delay':>8s}  {'200':>5s}  {'429':>5s}  {'other':>5s}  {'avg_len':>8s}")
    for delay, st in results.items():
        print(f"{delay:7.1f}s  {st['200']:>5d}  {st['429']:>5d}  "
              f"{st['other']:>5d}  {st['avg_len']:>8d}")

    return results


# ========== Test 1: SearchCompanies ==========
search_body = {
    "operationName": "SearchCompanies",
    "variables": {
        "employerSearchInput": {
            "employerName": "Software",
            "numPerPage": 10,  # 小页面减小响应
            "pageRequested": 1,
        }
    },
    "query": SEARCH_QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

stress_test(
    "SearchCompanies (10 per page)",
    "SearchCompanies",
    search_body,
    delays=[5.0, 3.0, 1.5, 0.5, 0.2, 0.1, 0.05, 0.0],
    rounds=5,
)

# ========== Test 2: EmployerReviewsData ==========
reviews_body = {
    "operationName": "EmployerReviewsData",
    "variables": {
        "employerId": 1138,  # Apple
        "page": 1,
        "pageSize": 20,
        "sort": "RELEVANCE",
        "language": "eng",
        "applyDefaultCriteria": True,
        "employmentStatuses": ["REGULAR", "PART_TIME"],
        "location": {},
        "onlyCurrentEmployees": False,
    },
    "query": REVIEWS_QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

stress_test(
    "EmployerReviewsData (20 per page)",
    "EmployerReviewsData",
    reviews_body,
    delays=[5.0, 3.0, 1.5, 0.5, 0.2, 0.1, 0.05, 0.0],
    rounds=5,
)

print("\n\n=== STRESS TEST COMPLETE ===")
