"""提取 SearchCompanies 请求/响应"""
import json
from mitmproxy import io

with open("d:/PycharmProjects/AiSpiderProject/glassdoor/capture/session2.mitm", "rb") as f:
    reader = io.FlowReader(f)
    idx = 0
    for flow in reader.stream():
        req = flow.request
        if "glassdoor" not in req.pretty_host:
            continue
        op = req.headers.get("x-gd-operation", "")
        if op == "SearchCompanies" and flow.response and flow.response.content:
            idx += 1
            out_req = f"d:/PycharmProjects/AiSpiderProject/glassdoor/capture/search_{op}_{idx}_req.json"
            out_resp = f"d:/PycharmProjects/AiSpiderProject/glassdoor/capture/search_{op}_{idx}_resp.json"

            try:
                body = json.loads(req.get_text() or "{}")
            except Exception:
                body = req.get_text() or ""
            with open(out_req, "w", encoding="utf-8") as fw:
                if isinstance(body, dict):
                    json.dump(body, fw, indent=2, ensure_ascii=False)
                else:
                    fw.write(str(body))

            resp_body = json.loads(flow.response.get_text() or "{}")
            with open(out_resp, "w", encoding="utf-8") as fw:
                json.dump(resp_body, fw, indent=2, ensure_ascii=False)

            print(f"[{idx}] {out_req} ({len(req.get_text() or '')} bytes)")
            print(f"    {out_resp} ({len(flow.response.get_text() or '')} bytes)")
