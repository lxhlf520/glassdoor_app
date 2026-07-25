"""Healthcheck all nodes via mihomo API, switch to a fast alive one, test glassdoor."""
import sys, json, time, requests
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
from curl_cffi import requests as creq

api = ClashAPI()
q = requests.utils.quote

# trigger concurrent healthcheck of the selector group
url = f"{api.base}/proxies/{q(api.group, safe='')}/healthcheck"
r = api.s.get(url, params={"url": "http://www.gstatic.com/generate_204", "timeout": 4000},
              timeout=120)
print("healthcheck:", r.status_code)

# read per-node delay history
alive = []
for node in api.nodes():
    try:
        info = api.s.get(f"{api.base}/proxies/{q(node, safe='')}", timeout=5).json()
        hist = info.get("history") or []
        if hist and hist[-1].get("delay", 0) > 0:
            alive.append((hist[-1]["delay"], node))
    except Exception:
        pass

alive.sort()
print(f"alive nodes: {len(alive)}/{len(api.nodes())}")
for d, n in alive[:15]:
    print(f"  {d:5d}ms  {n}")

json.dump([n for _, n in alive],
          open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_alive_nodes.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

if not alive:
    print("NO ALIVE NODE - subscription may be expired")
    raise SystemExit

# switch to fastest and test
node = alive[0][1]
print("\nswitching to fastest:", node)
eg = api.switch_and_wait(node, settle=2)
print("egress:", eg)

s = creq.Session(impersonate="chrome", proxies={"http": api.mixed, "https": api.mixed})
r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=25)
print("glassdoor:", r.status_code, "len:", len(r.text))
