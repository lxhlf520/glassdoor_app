"""Scan alive nodes: live-delay check, switch, probe glassdoor. Find 200 nodes."""
import sys, json, time
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
from clash_api import ClashAPI
from curl_cffi import requests as creq

api = ClashAPI()
alive = json.load(open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_alive_nodes.json",
                       encoding="utf-8"))
print(f"candidates from history: {len(alive)}")

results = {}
good = []
MAX_TRY = 12
tried = 0
for node in alive:
    if tried >= MAX_TRY or len(good) >= 3:
        break
    d = api.delay(node, timeout=2500)
    if not d:
        print(f"  skip (dead now): {node}")
        continue
    tried += 1
    t0 = time.time()
    eg = api.switch_and_wait(node, settle=1.2)
    if not eg:
        print(f"  {node}: switch/egress fail")
        results[node] = "egress_fail"
        continue
    try:
        s = creq.Session(impersonate="chrome",
                         proxies={"http": api.mixed, "https": api.mixed})
        r = s.get("https://www.glassdoor.com/Reviews/index.htm", timeout=15)
        st = r.status_code
    except Exception as e:
        st = type(e).__name__
    results[node] = st
    print(f"  [{st}] {node}  via {eg[0]} ({eg[1]})  {time.time()-t0:.1f}s")
    if st == 200:
        good.append(node)

print("\n=== summary ===")
for n, st in results.items():
    print(f"  {st}: {n}")
print("GOOD nodes:", good)
json.dump({"results": results, "good": good},
          open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_node_glassdoor.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
