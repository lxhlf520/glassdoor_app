"""探测 employerSearchRG 的各种参数组合，找到可用的分片方式"""
import json
import time
import uuid

import curl_cffi.requests as requests
from pymongo import MongoClient

QUERY = (
    "query SearchCompanies($employerSearchInput: EmployerSearchInput) { "
    "  employerSearchRG(employerSearchInput: $employerSearchInput) { "
    "    __typename ...CompaniesSearchResultFragment "
    "  } "
    "}  "
    "fragment EmployerResultFragment on UgcSearchV3EmployerResult { "
    "  employer { id shortName counts { reviewCount salaryCount } } "
    "}  "
    "fragment CompaniesSearchResultFragment on UgcSearchV3EmployerResultWrapper { "
    "  employerResults { __typename ...EmployerResultFragment } "
    "  numOfPagesAvailable pageNumber "
    "}"
)

def headers():
    return {
        "x-gd-id": str(uuid.uuid4()),
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": "SearchCompanies",
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36",
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
    }

def call(**kwargs):
    inp = {"numPerPage": 50, "pageRequested": 1}
    # Map plain kwargs to snakeCase
    key_map = {
        "employer_name": "employerName",
        "location_id": "locationId",
        "location_type": "locationType",
        "industry_id": "industryId",
        "industry_sector_id": "industrySectorId",
        "city_id": "cityId",
        "country_id": "countryId",
        "state_id": "stateId",
        "metro_id": "metroId",
    }
    for k, v in kwargs.items():
        inp[key_map.get(k, k)] = v
    body = {
        "operationName": "SearchCompanies",
        "variables": {"employerSearchInput": inp},
        "query": QUERY,
        "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
    }
    resp = requests.post(
        "https://api.glassdoor.com/mobile-graph",
        params={"locale": "zh_CN_#Hans"},
        headers=headers(), json=body, impersonate="chrome110", timeout=30,
    )
    d = resp.json()
    if d.get("errors"):
        return 0, 0, d["errors"][0].get("message", "")
    sr = (d.get("data") or {}).get("employerSearchRG") or {}
    np = sr.get("numOfPagesAvailable") or 0
    results = sr.get("employerResults") or []
    ids = [(r.get("employer") or {}).get("id") for r in results]
    return np, len(results), None

c = MongoClient("mongodb://localhost:27017")
existing_set = set(c["glassdoor"]["app_employers"].distinct("employerId"))
print(f"Existing employers: {len(existing_set)}\n")

# Test various param combinations
print("=== Parameter variations ===")
tests = [
    # Industry-related
    {"employer_name": "a", "industry_id": 10013},  # IT sector
    {"employer_name": "a", "industry_sector_id": 10013},
    # Country
    {"employer_name": "a", "country_id": 1},  # US
    # State
    {"employer_name": "a", "state_id": 1},
    # City (different param name)
    {"employer_name": "a", "city_id": 1154532},
    # Metro
    {"employer_name": "a", "metro_id": 1},
]

for t in tests:
    np, n, err = call(**t)
    status = f"pages={np} got={n}" if not err else f"ERR: {err[:60]}"
    print(f"  {t}: {status}")
    time.sleep(0.5)

# Test single-letter search depth
print("\n=== Single letter search: page depth ===")
for letter in ["e", "s", "t", "x", "z"]:
    np, n, _ = call(employer_name=letter)
    print(f"  '{letter}': pages={np} top50={n}")
    time.sleep(0.5)

# Test: what's the max numOfPagesAvailable for the broadest keywords?
print("\n=== Broadest keyword pages ===")
for kw in ["a", "e", "i", "o", "in", "co", "te", "re"]:
    np, n, _ = call(employer_name=kw)
    print(f"  '{kw}': pages={np}")
    time.sleep(0.4)

# Test: alphabetical enumeration depth
print("\n=== Alphabetical enumeration ===")
for c in "abcdefghijklmnopqrstuvwxyz":
    np, n, _ = call(employer_name=c)
    print(f"  '{c}': pages={np}", end="  ")
    if (ord(c) - ord('a') + 1) % 7 == 0:
        print()
    time.sleep(0.3)
print()

# Test: digit search
print("\n=== Digit search ===")
for d in "0123456789":
    np, n, _ = call(employer_name=d)
    print(f"  '{d}': pages={np} got={n}")
    time.sleep(0.3)
