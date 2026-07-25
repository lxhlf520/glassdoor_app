"""Switch to a fresh node and test glassdoor reachability."""
import sys
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
from curl_cffi import requests as creq

api = ClashAPI()
cur = api.current()
print("current node:", cur)
try:
    print("egress:", api.egress_ip())
except Exception as e:
    print("egress fail:", e)

# quick glassdoor check on current node
s = creq.Session(impersonate="chrome",
                 proxies={"http": api.mixed, "https": api.mixed})
r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=20)
print("glassdoor on current node:", r.status_code, "len:", len(r.text))

if r.status_code != 200:
    # try switching to first US node
    us = [n for n in api.nodes() if "美国" in n]
    print("US nodes available:", len(us))
    for node in us[:3]:
        print("switching to:", node)
        eg = api.switch_and_wait(node)
        print("  egress:", eg)
        s = creq.Session(impersonate="chrome",
                         proxies={"http": api.mixed, "https": api.mixed})
        try:
            r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=20)
            print("  glassdoor:", r.status_code, "len:", len(r.text))
            if r.status_code == 200:
                print("FRESH NODE OK:", node)
                break
        except Exception as e:
            print("  fail:", type(e).__name__, e)
