"""分析 mitmproxy flows.mitm 中 Glassdoor 相关请求"""
import json

from mitmproxy import io
from mitmproxy.exceptions import FlowReadException

FLOWS_PATH = "d:/PycharmProjects/AiSpiderProject/glassdoor/capture/flows.mitm"


def main():
    reader = io.FlowReader(open(FLOWS_PATH, "rb"))
    count = 0
    for flow in reader.stream():
        req = flow.request
        if "glassdoor" not in req.pretty_host:
            continue
        count += 1
        body = req.get_text() if req.content else "(no body)"
        if len(body) > 800:
            body = body[:800] + "...(truncated)"
        resp = flow.response
        resp_len = len(resp.content) if resp and resp.content else 0
        resp_body = ""
        if resp and resp.content:
            try:
                resp_body = resp.get_text()
                if len(resp_body) > 800:
                    resp_body = resp_body[:800] + "...(truncated)"
            except Exception:
                resp_body = f"(binary/cannot decode, len={resp_len})"

        print(f"\n{'='*60}")
        print(f"#{count} {req.method} {req.pretty_url}")
        print(f"Status: {resp.status_code if resp else 'N/A'} | Response len: {resp_len}")
        print(f"\nRequest headers:")
        for k, v in req.headers.items():
            if k.lower() in ("authorization", "cookie", "x-api-key"):
                print(f"  {k}: {v[:60]}...")
            else:
                print(f"  {k}: {v}")
        print(f"\nRequest body:")
        print(f"  {body}")
        print(f"\nResponse body:")
        print(f"  {resp_body}")

        if count >= 15:
            print("\n...(truncated at 15)")  # noqa: RUF001
            break

    print(f"\nTotal glassdoor flows: {count}")


if __name__ == "__main__":
    main()
