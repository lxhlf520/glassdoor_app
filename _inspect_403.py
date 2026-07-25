"""Inspect the 403 page: CF challenge type, headers, and same-session retry behavior."""
import sys, time
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
from curl_cffi import requests as creq

api = ClashAPI()
print("current node:", api.current())

s = creq.Session(impersonate="chrome", proxies={"http": api.mixed, "https": api.mixed})
for i in range(3):
    r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=20)
    print(f"try{i+1}: {r.status_code} len={len(r.text)} "
          f"cf-mitigated={r.headers.get('cf-mitigated')} cf-ray={r.headers.get('cf-ray')}")
    print("  cookies:", list(s.cookies.keys()))
    if i == 0:
        body = r.text
        markers = ["challenge-platform", "cf-chl", "Just a moment", "turnstile",
                   "cType", "managed", "__cf_bm", "cf_clearance", "Enable JavaScript"]
        for m in markers:
            if m.lower() in body.lower():
                print("  marker:", m)
        open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_403_page.html", "w",
             encoding="utf-8").write(body)
    if r.status_code == 200:
        print("PASSED on retry")
        break
    time.sleep(3)
