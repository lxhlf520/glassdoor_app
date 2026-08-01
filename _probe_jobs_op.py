"""Try different x-gd-operation headers for JobsSearchAndroid"""
import json
import time
import uuid

import curl_cffi.requests as requests
from pymongo import MongoClient


def make_headers(operation: str) -> dict:
    return {
        "x-gd-id": str(uuid.uuid4()),
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": operation,
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0"
        ),
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
    }


def post(body: dict, operation_header: str):
    resp = requests.post(
        "https://api.glassdoor.com/mobile-graph",
        params={"locale": "zh_CN_#Hans"},
        headers=make_headers(operation_header),
        json=body,
        impersonate="chrome110",
        timeout=30,
    )
    data = resp.json()
    ok = "data" in data and "jobListings" in (data.get("data") or {})
    print(f"op={operation_header} status={resp.status_code} ok={ok}")
    if ok:
        print(json.dumps(data, indent=2)[:400])
    return ok


c = MongoClient("mongodb://localhost:27017")
emp = c["glassdoor"]["app_employers"].find_one({"employerId": 6036})
eid = emp["employerId"]

QUERY = (
    "query JobsSearchAndroid($searchParams: SearchParams) { "
    "jobListings(contextHolder: { pageTypeEnum: GD_JOB_SEARCH searchParams: $searchParams } ) { "
    "jobListings { jobview { job { listingId jobTitleText } header { employer { id name } jobTitleText payPeriod locationName } } } "
    "totalJobsCount } }"
)

body = {
    "operationName": "JobsSearchAndroid",
    "variables": {
        "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10}
    },
    "query": QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}

ops = ["JobsSearchAndroid", "JobSearch", "SearchJobs", "GD_JOB_SEARCH", "EI_JOBS", "employerJobs", "JobSearchAndroid"]
for op in ops:
    if post(body, op):
        break
