"""提取 session2.mitm 中关键 Glassdoor 请求的完整内容"""
import json

from mitmproxy import io

FLOWS_PATH = "d:/PycharmProjects/AiSpiderProject/glassdoor/capture/session2.mitm"


def save_json(filename, data):
    path = f"d:/PycharmProjects/AiSpiderProject/glassdoor/capture/{filename}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {path} ({len(json.dumps(data))} bytes)")


def main():
    reader = io.FlowReader(open(FLOWS_PATH, "rb"))
    search_found = False
    reviews_found = False

    for flow in reader.stream():
        req = flow.request
        if "glassdoor" not in req.pretty_host:
            continue
        op = req.headers.get("x-gd-operation", "")

        # 跳过无响应的 flow
        if not flow.response or not flow.response.content:
            continue

        # 提取雇主搜索请求
        if not search_found and op == "EmployerAutocompleteSearch":
            search_found = True
            try:
                req_body = json.loads(req.get_text()) if req.content else {}
            except Exception:
                req_body = {"raw": req.get_text()[:500] if req.content else ""}
            try:
                resp_body = json.loads(flow.response.get_text()) if flow.response.content else {}
            except Exception:
                resp_body = {"raw": flow.response.get_text()[:500] if flow.response.content else ""}
            print(f"\n=== EmployerAutocompleteSearch ===")
            print(f"Status: {flow.response.status_code}, Len: {len(flow.response.content)}")
            save_json("search_request.json", req_body)
            save_json("search_response.json", resp_body)

        # 提取评论请求
        if not reviews_found and op == "EmployerReviewsData":
            reviews_found = True
            try:
                req_body = json.loads(req.get_text()) if req.content else {}
            except Exception:
                req_body = {"raw": req.get_text()[:500] if req.content else ""}
            try:
                resp_body = json.loads(flow.response.get_text()) if flow.response.content else {}
            except Exception:
                resp_body = {"raw": flow.response.get_text()[:500] if flow.response.content else ""}
            print(f"\n=== EmployerReviewsData ===")
            print(f"Status: {flow.response.status_code}, Len: {len(flow.response.content)}")
            print(f"employerId={req_body.get('variables', {}).get('employerId')}")
            print(f"page={req_body.get('variables', {}).get('page')}")
            save_json("reviews_request.json", req_body)
            save_json("reviews_response.json", resp_body)

        if search_found and reviews_found:
            break

    if not search_found:
        print("\nWARNING: EmployerAutocompleteSearch not found!")
    if not reviews_found:
        print("WARNING: EmployerReviewsData not found!")


if __name__ == "__main__":
    main()
