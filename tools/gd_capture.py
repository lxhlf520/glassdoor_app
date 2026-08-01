"""mitmproxy addon: 完整捕获 Glassdoor api/mobile-graph 请求(含全部 header 与 body),写入文件供逆向分析"""
import json
import os
import time

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "capture", "flows")
os.makedirs(OUT_DIR, exist_ok=True)

INTERESTING_HOSTS = ("api.glassdoor.com", "www.glassdoor.com")


class GDCapture:
    def __init__(self):
        self.count = 0
        self.seen_hosts = set()

    def request(self, flow):
        host = flow.request.pretty_host
        self.seen_hosts.add(host)

    def response(self, flow):
        host = flow.request.pretty_host
        path = flow.request.path.split("?")[0]

        # 打印所有主机的概览,方便定位
        is_target = any(h in host for h in INTERESTING_HOSTS) or "mobile-graph" in path
        if not is_target:
            return

        self.count += 1
        op = flow.request.headers.get("x-apollo-operation-name", "") or \
             flow.request.headers.get("x-gd-operation", "")

        rec = {
            "ts": time.time(),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "operation": op,
            "req_headers": dict(flow.request.headers),
            "req_body": flow.request.get_text() if flow.request.content else None,
            "status": flow.response.status_code if flow.response else None,
            "resp_headers": dict(flow.response.headers) if flow.response else None,
        }
        # 响应体只存较小的
        if flow.response and flow.response.content and len(flow.response.content) < 200000:
            rec["resp_body"] = flow.response.get_text()

        fn = os.path.join(OUT_DIR, f"{self.count:04d}_{op or 'noop'}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)

        print(f"\n[CAPTURED #{self.count}] {flow.request.method} {host}{path} op={op} -> {rec['status']}")
        # 关键动态 header
        for h in ["x-gd-id", "x-gd-asst", "x-gd-operation", "gd-csrf-token",
                  "authorization", "apollographql-client-version", "user-agent", "cookie"]:
            v = flow.request.headers.get(h)
            if v:
                print(f"    {h}: {v[:120]}")

    def done(self):
        print(f"\n[SUMMARY] captured={self.count}, hosts seen={sorted(self.seen_hosts)}")


addons = [GDCapture()]
