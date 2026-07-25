"""Decisive test: are FRESH nodes (different ASNs, unused today) clean?

For each diverse node: switch, fire 20 reqs @ 3 req/s with random gdId,
count ok / 429. Clean => chronic 429 was per-subnet on JP ranges.
"""
import sys, time, uuid
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
import curl_cffi.requests as creq
from parallel_collector import REVIEWS_QUERY

NODES = ["🇺🇸 美国07-VIP22c", "🇹🇼 台湾4-VIP88a", "🇩🇪 德国-VIP88a", "🇰🇷 韩国-VIP88a"]
N_REQ = 20
RATE = 3.0

api = ClashAPI()
body = {
    "operationName": "EmployerReviewsData",
    "variables": {"employerId": 1138, "page": 1, "pageSize": 100,
                  "sort": "RELEVANCE", "language": "eng",
                  "applyDefaultCriteria": True,
                  "employmentStatuses": ["REGULAR", "PART_TIME"],
                  "location": {}, "onlyCurrentEmployees": False},
    "query": REVIEWS_QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}


def make_headers():
    return {
        "x-gd-id": str(uuid.uuid4()),
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
    }


s = creq.Session(impersonate="chrome110", proxies={"http": api.mixed, "https": api.mixed})
for node in NODES:
    eg = api.switch_and_wait(node, settle=1.5)
    print(f"\n=== {node} egress={eg}")
    if not eg:
        print("  switch failed, skip")
        continue
    ok = r429 = other = 0
    t0 = time.time()
    for i in range(1, N_REQ + 1):
        try:
            r = s.post("https://api.glassdoor.com/mobile-graph",
                       params={"locale": "zh_CN_#Hans"}, headers=make_headers(),
                       json=body, timeout=30)
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                r429 += 1
            else:
                other += 1
        except Exception:
            other += 1
        want = t0 + i / RATE
        d = want - time.time()
        if d > 0:
            time.sleep(d)
    print(f"  ok={ok} 429={r429} other={other} ({time.time()-t0:.0f}s)")
