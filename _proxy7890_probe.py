"""Probe Clash 7890 egress: geo, glassdoor status, controller API."""
import requests
from curl_cffi import requests as creq

PX = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

# 1) egress geo via 7890
try:
    r = requests.get("http://ip-api.com/json", timeout=10, proxies=PX)
    d = r.json()
    print("7890 egress:", d.get("query"), d.get("country"), d.get("regionName"),
          d.get("isp"), "| as:", d.get("as"))
except Exception as e:
    print("geo via 7890 fail:", type(e).__name__, e)

# 2) glassdoor via 7890
try:
    s = creq.Session(impersonate="chrome", proxies=PX)
    r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=20)
    print("glassdoor via 7890:", r.status_code, "len:", len(r.text))
except Exception as e:
    print("glassdoor via 7890 fail:", type(e).__name__, e)

# 3) clash controller (for node switching)
for port in (9090, 9097, 7897):
    try:
        r = requests.get(f"http://127.0.0.1:{port}/version", timeout=3)
        if r.ok:
            print("controller on", port, r.json())
            break
    except Exception:
        pass
else:
    print("controller: not found on 9090/9097/7897")
