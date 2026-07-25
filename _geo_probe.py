"""Check direct egress geo + whether glassdoor 429 ban has lifted."""
import requests
from curl_cffi import requests as creq

# 1) direct egress geo
try:
    r = requests.get("http://ip-api.com/json", timeout=8)
    d = r.json()
    print("direct egress:", d.get("query"), d.get("country"), d.get("regionName"),
          d.get("isp"), "| as:", d.get("as"))
except Exception as e:
    print("geo fail:", type(e).__name__, e)

# 2) glassdoor direct still banned?
try:
    s = creq.Session(impersonate="chrome")
    r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=15)
    print("glassdoor direct:", r.status_code, "len:", len(r.text))
except Exception as e:
    print("glassdoor fail:", type(e).__name__, e)
