"""Is the ~500-600 wall keyed on TLS fingerprint (JA3/JA4)?

The global wall just hit: ALL nodes instantly 429 (chrome110 impersonate).
Fire 3 reqs per DIFFERENT impersonate on the SAME node.
Any 200 => the quota key is the TLS fingerprint, not IP/identity.
"""
import sys, time, uuid
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
import curl_cffi.requests as creq
from parallel_collector import REVIEWS_QUERY

IMPS = ["chrome110",   # control: expect 429
        "chrome120", "chrome124", "chrome131", "chrome133a",
        "safari15_5", "safari17_0", "safari18_0",
        "edge101", "firefox133", "chrome131_android", "chrome99_android"]
N_REQ = 3

api = ClashAPI()
print("node:", api.current(), "egress:", api.egress_ip())

# use session per impersonate (TLS negotiation is per-session)
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


proxies = {"http": api.mixed, "https": api.mixed}
for imp in IMPS:
    try:
        s = creq.Session(impersonate=imp, proxies=proxies)
    except Exception as e:
        print(f"{imp:>20}: SESSION FAIL {e}")
        continue
    ok = r429 = other = 0
    for _ in range(N_REQ):
        try:
            r = s.post("https://api.glassdoor.com/mobile-graph",
                       params={"locale": "zh_CN_#Hans"}, headers=make_headers(),
                       json=body, timeout=30)
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                r429 += 1
                if r429 == 1:
                    print(f"{imp:>20}: FIRST 429 body={r.text[:120]!r}")
            else:
                other += 1
        except Exception as e:
            other += 1
        time.sleep(0.3)
    print(f"{imp:>20}: ok={ok} 429={r429} other={other}")
    time.sleep(1.0)
