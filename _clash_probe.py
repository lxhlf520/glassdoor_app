"""Probe Clash RESTful API: list selector groups and node options."""
import requests

CTRL = None
for port in (9090, 9097, 61753, 7897):
    try:
        r = requests.get(f"http://127.0.0.1:{port}/version", timeout=3)
        if r.ok:
            CTRL = f"http://127.0.0.1:{port}"
            print("controller:", CTRL, r.json())
            break
    except Exception:
        pass

if not CTRL:
    print("no clash controller found")
    raise SystemExit

d = requests.get(f"{CTRL}/proxies", timeout=5).json()["proxies"]
print("total proxies:", len(d))
for name, info in d.items():
    t = info.get("type")
    if t in ("Selector", "URLTest", "Fallback", "LoadBalance"):
        opts = info.get("all", [])
        print(f"[{t}] {name}  now={info.get('now')}  options={len(opts)}")
        if t == "Selector" and opts:
            print("   first options:", opts[:10])
