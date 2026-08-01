"""精确测量: 单字母搜索 3-99 页的累计新增公司数（排除与现有 324K 的重叠）"""
import time, uuid
import curl_cffi.requests as requests
from pymongo import MongoClient

QUERY = (
    "query SearchCompanies($employerSearchInput: EmployerSearchInput) { "
    "  employerSearchRG(employerSearchInput: $employerSearchInput) { "
    "    __typename ...CompaniesSearchResultFragment "
    "  } "
    "}  "
    "fragment EmployerResultFragment on UgcSearchV3EmployerResult { "
    "  employer { id shortName counts { reviewCount } } "
    "}  "
    "fragment CompaniesSearchResultFragment on UgcSearchV3EmployerResultWrapper { "
    "  employerResults { __typename ...EmployerResultFragment } "
    "  numOfPagesAvailable pageNumber "
    "}"
)

def headers():
    return {
        "x-gd-id": str(uuid.uuid4()), "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": "SearchCompanies", "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false", "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0", "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36",
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
    }

def call(keyword, page):
    body = {
        "operationName": "SearchCompanies",
        "variables": {"employerSearchInput": {
            "employerName": keyword, "numPerPage": 100, "pageRequested": page}},
        "query": QUERY,
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
    }
    resp = requests.post(
        "https://api.glassdoor.com/mobile-graph", params={"locale": "zh_CN_#Hans"},
        headers=headers(), json=body, impersonate="chrome110", timeout=30,
    )
    d = resp.json()
    if d.get("errors"): return []
    sr = (d.get("data") or {}).get("employerSearchRG") or {}
    results = sr.get("employerResults") or []
    return [(r.get("employer") or {}).get("id") for r in results if (r.get("employer") or {}).get("id")]

c = MongoClient("mongodb://localhost:27017")
existing = set(c["glassdoor"]["app_employers"].distinct("employerId"))
print(f"Existing: {len(existing)}\n")

# For 'a', sample every 10th page from 3 to 99
cumulative_new = set()
print("=== 'a' pages 3-99: cumulative new vs existing ===")
for p in range(3, 100, 10):
    ids = call("a", p)
    new = [i for i in ids if i not in existing]
    before = len(cumulative_new)
    cumulative_new.update(i for i in ids if i not in existing)
    added = len(cumulative_new) - before
    print(f"  page {p:2d}: got={len(ids)} new_vs_existing={len(new)} added_to_cumulative={added} cumulative={len(cumulative_new)}")
    time.sleep(0.4)

# Extrapolate
sampled = len(range(3, 100, 10))  # 10 samples
if sampled > 0:
    avg_per_page = len(cumulative_new) / sampled
    est_total = int(avg_per_page * 97)  # 97 pages (3-99)
    print(f"\n  → sampled {sampled} pages, avg new/page={avg_per_page:.1f}, est total={est_total:,}")
    print(f"  → existing={len(existing):,} + est_new={est_total:,} = ~{len(existing)+est_total:,}")

# Now test cross-letter overlap: do 'a' and 'e' return very different companies?
print("\n=== Cross-letter overlap: 'a' vs 'e' page 5 ===")
ids_a = set(call("a", 5))
time.sleep(0.5)
ids_e = set(call("e", 5))
overlap = ids_a & ids_e
print(f"  'a' p5: {len(ids_a)}, 'e' p5: {len(ids_e)}, overlap: {len(overlap)}")
print(f"  overlap ratio: {len(overlap)/max(len(ids_a), len(ids_e)):.1%}")

# Also test 'co' deep pages
print("\n=== 'co' depth check ===")
for p in [1, 10, 50, 100, 150, 200]:
    ids = call("co", p)
    print(f"  page {p:3d}: got={len(ids)}")
    if len(ids) == 0:
        print(f"  → 'co' cutoff around {p}")
        break
    time.sleep(0.3)
