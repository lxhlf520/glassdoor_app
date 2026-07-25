"""Enable mihomo external-controller in FlClash patchClashConfig (app must be closed)."""
import json, shutil

PREF = r"C:\Users\13662\AppData\Roaming\com.follow\clash\shared_preferences.json"
shutil.copy2(PREF, PREF + ".bak")

raw = json.load(open(PREF, encoding="utf-8"))
cfg = json.loads(raw["flutter.config"])
patch = cfg["patchClashConfig"]
patch["external-controller"] = "127.0.0.1:9090"
patch["secret"] = "glassdoor123"
raw["flutter.config"] = json.dumps(cfg, ensure_ascii=False)

with open(PREF, "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False)

print("patched: external-controller =", patch["external-controller"])
