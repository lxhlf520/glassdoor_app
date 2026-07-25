"""Burst test: fixed FRESH gdId + NO cookie, 700 reqs on ONE node.

If clean past ~550 -> the quota key was the stale __cf_bm cookie.
If 429 at ~500     -> key is gdId or TLS/header fingerprint.
"""
import sys, time, uuid
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
import curl_cffi.requests as creq
from parallel_collector import REVIEWS_QUERY

NODE = "🇯🇵 日本2-VIP88a"   # 已验证恢复健康
N_REQ = 700
RATE = 3.0

api = ClashAPI()
print("switching to:", NODE)
print("egress:", api.switch_and_wait(NODE, settle=1.5))

print("RANDOM gdId per request | NO cookie | rate", RATE, "req/s")


def make_headers():
    return {
        "x-gd-id": str(uuid.uuid4()),          # 每请求随机设备 ID
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

s = creq.Session(impersonate="chrome110", proxies={"http": api.mixed, "https": api.mixed})
ok = fail = first_429_at = 0
t0 = time.time()
for i in range(1, N_REQ + 1):
    try:
        r = s.post("https://api.glassdoor.com/mobile-graph",
                   params={"locale": "zh_CN_#Hans"}, headers=make_headers(),
                   json=body, timeout=30)
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            if r.status_code == 429 and not first_429_at:
                first_429_at = i
                print(f"*** FIRST 429 at request #{i} after {time.time()-t0:.0f}s")
    except Exception as e:
        fail += 1
        print(f"  exc #{i}: {type(e).__name__}")
    if i % 50 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] {i}/{N_REQ} ok={ok} fail={fail} "
              f"({time.time()-t0:.0f}s)")
    # pace
    want = t0 + i / RATE
    d = want - time.time()
    if d > 0:
        time.sleep(d)

print(f"\nDONE ok={ok} fail={fail} first_429_at={first_429_at} "
      f"elapsed={time.time()-t0:.0f}s")
