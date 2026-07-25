"""Probe FlClashCore control port 56355 and list selector groups/nodes."""
import requests

BASE = "http://127.0.0.1:56355"

for path in ("/version", "/configs", "/proxies"):
    try:
        r = requests.get(BASE + path, timeout=4)
        print(path, r.status_code, r.text[:200].replace("\n", " "))
    except Exception as e:
        print(path, "fail", type(e).__name__, str(e)[:120])
