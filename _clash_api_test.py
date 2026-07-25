"""Verify mihomo API on 9090, list selector group, show current node."""
import json, requests

H = {"Authorization": "Bearer glassdoor123"}
BASE = "http://127.0.0.1:9090"

r = requests.get(BASE + "/version", headers=H, timeout=5)
print("version:", r.status_code, r.json())

r = requests.get(BASE + "/proxies", headers=H, timeout=8)
proxies = r.json()["proxies"]
g = proxies.get("🔰节点选择")
if not g:
    print("group not found; groups:",
          [n for n, p in proxies.items() if p.get("type") == "Selector"])
else:
    print("group type:", g["type"], "| now:", g.get("now"), "| options:", len(g.get("all", [])))
    json.dump(g.get("all", []),
              open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_nodes.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
