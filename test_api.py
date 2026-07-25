"""测试：用 APP 原始请求体重放 API"""
import json
import time

import curl_cffi.requests as requests

GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

# 加载 APP 的原始请求体
with open("d:/PycharmProjects/AiSpiderProject/glassdoor/capture/reviews_request.json") as f:
    original_body = json.load(f)


def test(employer_id: int, page: int = 1):
    url = "https://api.glassdoor.com/mobile-graph"
    params = {"locale": "zh_CN_#Hans"}

    # 复制并修改变量
    body = json.loads(json.dumps(original_body))
    body["variables"]["employerId"] = employer_id
    body["variables"]["page"] = page
    body["variables"]["bestProfileId"] = None  # 去掉特定用户的 profile

    headers = {
        "x-gd-id": GD_ID,
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": "EmployerReviewsData",
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

    print(f"Test employer_id={employer_id} page={page} ...")
    resp = requests.post(url, params=params, headers=headers, json=body,
                         impersonate="chrome110", timeout=30)
    print(f"Status: {resp.status_code}, len={len(resp.content)}")

    if resp.status_code == 200 and len(resp.content) > 100:
        data = resp.json()
        emp = data.get("data", {}).get("employer", {})
        er = data.get("data", {}).get("employerReviews", {})
        print(f"  Company: {emp.get('name')}")
        print(f"  Total reviews: {er.get('filteredReviewsCount')}")
        print(f"  Total pages: {er.get('numberOfPages')}")
        reviews = er.get("reviews", [])
        print(f"  Reviews: {len(reviews)}")
        if reviews:
            r0 = reviews[0]
            print(f"  First: [{r0.get('ratingOverall')}*] {r0.get('summary','')[:60]}")
        return True
    else:
        print(f"  Body: {resp.text[:300]}")
        return False


if __name__ == "__main__":
    for pid in [1138, 1596815]:
        if test(pid, 1):
            print(f"  SUCCESS for employer_id={pid}\n")
        else:
            print(f"  FAILED for employer_id={pid}\n")
