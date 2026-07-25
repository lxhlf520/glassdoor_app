"""mitmproxy addon: 实时显示 Glassdoor 请求的关键信息"""
import json

from mitmproxy import http


class GlassdoorMonitor:
    def request(self, flow: http.HTTPFlow) -> None:
        if "glassdoor" not in flow.request.pretty_host:
            return
        if "media.glassdoor.com" in flow.request.pretty_host:
            return
        if "rudderstack" in flow.request.pretty_host:
            return

        op = flow.request.headers.get("x-apollo-operation-name", "")
        url = flow.request.pretty_url.split("?")[0]

        print(f"\n{'='*50}")
        print(f"[REQ] {flow.request.method} {url}")
        print(f"  operation: {op}")

        # key headers
        for h in ["x-gd-operation", "x-gd-glassbowl-user", "authorization", "gd-csrf-token"]:
            v = flow.request.headers.get(h)
            if v:
                print(f"  {h}: {v[:80]}")

        # request body (first 3000 chars)
        if flow.request.content:
            body = flow.request.get_text()
            if body:
                try:
                    j = json.loads(body)
                    body = json.dumps(j, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                if len(body) > 3000:
                    body = body[:3000] + "..."
                print(f"  Body: {body}")

    def response(self, flow: http.HTTPFlow) -> None:
        if "glassdoor" not in flow.request.pretty_host:
            return
        if "media.glassdoor.com" in flow.request.pretty_host:
            return
        if "rudderstack" in flow.request.pretty_host:
            return

        resp = flow.response
        resp_len = len(resp.content) if resp and resp.content else 0
        op = flow.request.headers.get("x-apollo-operation-name", "")
        print(f"[RESP] {resp.status_code} | {resp_len} bytes | op={op}")

        if resp and resp.content and resp_len < 10000:
            try:
                body = resp.get_text()
                j = json.loads(body)
                # 只打印顶层 keys
                if isinstance(j, dict):
                    top_keys = list(j.keys())[:5]
                    print(f"  Top keys: {top_keys}")
                    # 如果有关键字段，打印摘要
                    if "data" in j and isinstance(j["data"], dict):
                        inner_keys = list(j["data"].keys())[:3]
                        print(f"  data keys: {inner_keys}")
                elif isinstance(j, list):
                    print(f"  Array length: {len(j)}")
            except Exception:
                pass


addons = [GlassdoorMonitor()]
