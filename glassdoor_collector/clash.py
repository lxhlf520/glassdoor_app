"""FlClash (mihomo) controller API: list/switch proxy nodes, verify egress.

Usage:
    from clash_api import ClashAPI
    api = ClashAPI()
    api.current()            # current node name of selector group
    api.nodes()              # all node names in selector group
    api.switch(node_name)    # switch selector group to node
    api.egress_ip()          # egress IP via mixed port proxy
"""
import os
import time
import requests

# 支持环境变量覆盖，适配不同机器的 Clash 配置
GROUP = os.environ.get("CLASH_GROUP", "🔰节点选择")
BASE = os.environ.get("CLASH_BASE", "http://127.0.0.1:9090")
SECRET = os.environ.get("CLASH_SECRET", "glassdoor123")
MIXED = os.environ.get("CLASH_MIXED", "http://127.0.0.1:7890")


class ClashAPI:
    def __init__(self, base=BASE, secret=SECRET, group=GROUP, mixed=MIXED):
        self.base = base
        self.group = group
        self.mixed = mixed
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {secret}"

    def _group_url(self):
        return f"{self.base}/proxies/{requests.utils.quote(self.group, safe='')}"

    def alive(self):
        try:
            r = self.s.get(self.base + "/version", timeout=3)
            return r.ok
        except Exception:
            return False

    def group_info(self):
        r = self.s.get(self._group_url(), timeout=5)
        r.raise_for_status()
        return r.json()

    def current(self):
        return self.group_info().get("now")

    def nodes(self):
        return list(self.group_info().get("all", []))

    def switch(self, node):
        r = self.s.put(self._group_url(), json={"name": node}, timeout=5)
        return r.status_code in (200, 204)

    def delay(self, node, timeout=5000):
        url = f"{self.base}/proxies/{requests.utils.quote(node, safe='')}/delay"
        try:
            r = self.s.get(url, params={"url": "http://www.gstatic.com/generate_204",
                                        "timeout": timeout}, timeout=timeout / 1000 + 2)
            if r.ok:
                return r.json().get("delay")
        except Exception:
            pass
        return None

    def egress_ip(self, timeout=8):
        px = {"http": self.mixed, "https": self.mixed}
        r = requests.get("http://ip-api.com/json", proxies=px, timeout=timeout)
        d = r.json()
        return d.get("query"), f"{d.get('country')} {d.get('isp')}"

    def switch_and_wait(self, node, settle=1.5):
        """Switch node; return new egress ip or None on failure."""
        if not self.switch(node):
            return None
        time.sleep(settle)
        try:
            return self.egress_ip()
        except Exception:
            return None


if __name__ == "__main__":
    api = ClashAPI()
    print("api alive:", api.alive())
    print("current:", api.current())
    print("nodes:", len(api.nodes()))
