"""Probe EmployerOverviewPage for jobs/salary hints"""
import json
import time
import uuid

import curl_cffi.requests as requests
from pymongo import MongoClient


def headers(operation: str) -> dict:
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


def post(body: dict, operation: str):
    resp = requests.post(
        "https://api.glassdoor.com/mobile-graph",
        params={"locale": "zh_CN_#Hans"},
        headers=headers(operation),
        json=body,
        impersonate="chrome110",
        timeout=30,
    )
    print(f"[{operation}] HTTP {resp.status_code}")
    data = resp.json()
    if "errors" in data:
        print("errors:", json.dumps(data["errors"], indent=2)[:300])
    else:
        print("data keys:", list((data.get("data") or {}).keys()))
    return data


c = MongoClient("mongodb://localhost:27017")
emp = c["glassdoor"]["app_employers"].find_one({"employerId": 6036})
eid = emp["employerId"]

body = {
    "operationName": "EmployerOverviewPage",
    "variables": {
        "awardsLimit": 0,
        "employerId": eid,
        "isROWProfile": False,
        "onlyFeaturedAwards": False,
        "preferredTldId": 1,
        "profileId": 0,
        "rowTld": 1,
    },
    "query": (
        "query EmployerOverviewPage($awardsLimit: Int, $employerId: Int!, $isROWProfile: Boolean!, "
        "$onlyFeaturedAwards: Boolean, $preferredTldId: Int!, $profileId: Int!, $rowTld: Int) { "
        "employer(id: $employerId) { id name shortName } "
        "employerInterviews: employerInterviewsIG(employerInterviewsInput: { employer: { id: $employerId } }) { "
        "interviewExperienceCounts { count type percentage } totalInterviewCount difficultySubmissionCount difficultySum } "
        "}"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "EmployerOverviewPage")
