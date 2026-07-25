"""Isolate the 429 quota key: gdId vs __cf_bm vs IP.

On a JUST-429'd node, send single requests with different identity combos.
"""
import sys, time, uuid
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
import curl_cffi.requests as creq

GD_ID_OLD = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM_OLD = ("SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1"
             "-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm")

api = ClashAPI()
print("node:", api.current(), "(just got 429s on this one)")

QUERY = ("query EmployerReviewsData($employerId: Int!, $page: Int!, $pageSize: Int!) { "
         "  employerReviews: employerReviewsRG(employerReviewsInput: { "
         "    employer: { id: $employerId } page: { num: $page size: $pageSize } "
         "  }) { reviews { reviewId } } }")


def hit(gd_id, cf_bm, label):
    headers = {
        "x-gd-id": gd_id,
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
    if cf_bm:
        headers["cookie"] = f"gdId={gd_id}; __cf_bm={cf_bm}"
    body = {"operationName": "EmployerReviewsData",
            "variables": {"employerId": 1138, "page": 1, "pageSize": 100},
            "query": QUERY,
            "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}}}
    try:
        r = creq.post("https://api.glassdoor.com/mobile-graph",
                      params={"locale": "zh_CN_#Hans"}, headers=headers, json=body,
                      proxies={"http": api.mixed, "https": api.mixed},
                      impersonate="chrome110", timeout=30)
        n = -1
        if r.status_code == 200:
            try:
                n = len((r.json().get("data") or {}).get("employerReviews", {}).get("reviews") or [])
            except Exception:
                pass
        print(f"  {label}: {r.status_code} reviews={n} len={len(r.content)}")
        return r.status_code
    except Exception as e:
        print(f"  {label}: EXC {type(e).__name__} {str(e)[:80]}")
        return None


new_id = str(uuid.uuid4())
print("new gdId:", new_id)

hit(GD_ID_OLD, CF_BM_OLD, "A old gdId + old cf_bm (control, expect 429)")
time.sleep(3)
hit(new_id, None, "B NEW gdId + NO cookie")
time.sleep(3)
hit(new_id, CF_BM_OLD, "C NEW gdId + old cf_bm")
time.sleep(3)
hit(GD_ID_OLD, None, "D old gdId + NO cookie")
