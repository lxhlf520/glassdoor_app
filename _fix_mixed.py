"""Check runtime config via API; force rebuild mixed inbound on 7890."""
import requests, time

BASE = "http://127.0.0.1:9090"
H = {"Authorization": "Bearer glassdoor123"}

r = requests.get(BASE + "/configs", headers=H, timeout=5)
c = r.json()
print("runtime mixed-port:", c.get("mixed-port"), "| port:", c.get("port"),
      "| mode:", c.get("mode"))

# force rebuild: switch to 7891 then back to 7890
for p in (7891, 7890):
    r = requests.patch(BASE + "/configs", headers=H, json={"mixed-port": p}, timeout=5)
    print(f"PATCH mixed-port={p}:", r.status_code, r.text[:80])
    time.sleep(1)
