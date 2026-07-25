"""简单提取所有公司评论中的 employerId 和名称"""
import json
import re

from mitmproxy import io

with open("d:/PycharmProjects/AiSpiderProject/glassdoor/capture/session2.mitm", "rb") as f:
    reader = io.FlowReader(f)
    seen = set()
    for flow in reader.stream():
        req = flow.request
        if "glassdoor" not in req.pretty_host:
            continue
        if not flow.response or not flow.response.content:
            continue
        op = req.headers.get("x-gd-operation", "")
        if op == "EmployerReviewsData":
            try:
                body = json.loads(flow.response.get_text())
                emp = body.get("data", {}).get("employer", {})
                er = body.get("data", {}).get("employerReviews", {})
                eid = emp.get("id") or emp.get("shortName")
                if eid and eid not in seen:
                    seen.add(eid)
                    # parse employerId from request body
                    req_text = req.get_text() or ""
                    m = re.search(r'"employerId":\s*(\d+)', req_text)
                    rid = m.group(1) if m else "?"
                    print(f"employerId={rid} | name={emp.get('name')} | total_reviews={er.get('filteredReviewsCount')} | pages={er.get('numberOfPages')}")
            except Exception as exc:
                pass

print(f"\nTotal unique employers: {len(seen)}")
