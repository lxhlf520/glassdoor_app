"""分析 flows.mitm，聚焦非 GetAppConfiguration 的关键请求"""
import json

from mitmproxy import io

FLOWS_PATH = "d:/PycharmProjects/AiSpiderProject/glassdoor/capture/flows.mitm"


def main():
    reader = io.FlowReader(open(FLOWS_PATH, "rb"))
    seen_ops = set()
    count = 0
    for flow in reader.stream():
        req = flow.request
        if "glassdoor" not in req.pretty_host:
            continue
        if "media.glassdoor.com" in req.pretty_host:
            continue
        if "rudderstack" in req.pretty_host:
            continue

        op_name = req.headers.get("x-apollo-operation-name", "")
        url_key = req.pretty_url.split("?")[0]

        # 去重：同 URL+同操作名只展示一次
        dedup_key = f"{req.method}|{url_key}|{op_name}"
        if dedup_key in seen_ops:
            continue
        seen_ops.add(dedup_key)
        count += 1

        resp = flow.response
        resp_len = len(resp.content) if resp and resp.content else 0
        resp_body = ""
        if resp and resp.content:
            try:
                resp_body = resp.get_text()
                if len(resp_body) > 2000:
                    resp_body = resp_body[:2000] + "...(truncated)"
            except Exception:
                resp_body = f"(binary, len={resp_len})"

        request_body = req.get_text() if req.content else "(no body)"
        if len(request_body) > 1500:
            request_body = request_body[:1500] + "...(truncated)"

        # 尝试格式化 JSON
        try:
            if request_body.startswith("{"):
                req_json = json.loads(request_body)
                request_body = json.dumps(req_json, indent=2, ensure_ascii=False)
                if len(request_body) > 2000:
                    request_body = request_body[:2000] + "...(truncated)"
        except Exception:
            pass

        try:
            if resp_body.startswith("{"):
                resp_json = json.loads(resp_body)
                resp_body = json.dumps(resp_json, indent=2, ensure_ascii=False)
                if len(resp_body) > 2000:
                    resp_body = resp_body[:2000] + "...(truncated)"
        except Exception:
            pass

        print(f"\n{'='*60}")
        print(f"#{count} [{dedup_key}]")
        print(f"Status: {resp.status_code if resp else 'N/A'} | Body len: {resp_len}")
        print(f"\nKey headers:")
        important_keys = ["x-gd-operation", "x-gd-asst", "x-gd-id", "gd-csrf-token",
                          "authorization", "cookie", "x-gd-glassbowl-user", "user-agent"]
        for k in important_keys:
            v = req.headers.get(k)
            if v:
                if k == "cookie":
                    print(f"  {k}: {v[:100]}")
                elif k == "authorization":
                    print(f"  {k}: {v[:80]}")
                else:
                    print(f"  {k}: {v}")

        print(f"\nRequest body:")
        print(request_body)
        print(f"\nResponse body (first 2k):")
        print(resp_body)

        if count >= 30:
            print("\n...(truncated at 30)")
            break

    print(f"\nTotal unique endpoints: {count}")


if __name__ == "__main__":
    main()
