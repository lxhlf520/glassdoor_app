"""Try many JobsSearchAndroid variable combinations"""
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
    return resp.status_code, resp.json()


c = MongoClient("mongodb://localhost:27017")
emp = c["glassdoor"]["app_employers"].find_one({"employerId": 6036})
eid = emp["employerId"]
ename = emp.get("shortName", "")

QUERY = (
    "query JobsSearchAndroid($adSlotName: String, $pageTypeEnum: PageTypeEnum, $searchParams: SearchParams, "
    "$onlyCurrentGlassdoorAwards: Boolean! = true, $blcAwardsLimit: Int! = 0, $bptwAwardsLimit: Int! = 0) { "
    "jobListings(contextHolder: { adSlotName: $adSlotName pageTypeEnum: $pageTypeEnum searchParams: $searchParams } ) { "
    "jobListings { jobview { __typename ...JobViewFragment } } totalJobsCount } } "
    "fragment JobViewFragment on JobView { job { listingId jobTitleText } header { employer { id name } "
    "jobTitleText payPeriod payPeriodAdjustedPay { p90 p50 p10 } locationName jobViewUrl jobCountryId } }"
)

variants = [
    {"label": "basic keyword", "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10}},
    {"label": "keyword + employer filter", "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10, "filterParams": [{"filterKey": "employerId", "values": [str(eid)]}]}},
    {"label": "employer only string", "searchParams": {"pageNumber": 1, "pageSize": 10, "filterParams": [{"filterKey": "employerId", "values": [str(eid)]}]}},
    {"label": "employer as int", "searchParams": {"pageNumber": 1, "pageSize": 10, "filterParams": [{"filterKey": "employerId", "values": [eid]}]}},
    {"label": "with disableAllFacets", "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10, "disableAllFacets": False}},
    {"label": "with attributionCode", "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10, "attributionCode": None}},
    {"label": "advanced searchParams", "searchParams": {"keyword": "software engineer", "pageNumber": 1, "pageSize": 10, "locationId": 0, "locationType": "STATE", "filterParams": []}},
]

page_types = ["EI_JOBS", "GD_JOB_SEARCH", "GD_APP_JOB_SEARCH", "GD_APP_EI_JOBS", "GD_JOB_SERPS"]
ad_slots = ["", "app_top_jobs", "android_top_jobs", "app_ei_jobs"]

for v in variants:
    for pt in page_types:
        for ad in ad_slots:
            body = {
                "operationName": "JobsSearchAndroid",
                "variables": {
                    "adSlotName": ad,
                    "pageTypeEnum": pt,
                    "searchParams": v["searchParams"],
                    "onlyCurrentGlassdoorAwards": False,
                },
                "query": QUERY,
                "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
            }
            status, data = post(body, "JobsSearchAndroid")
            has_data = "data" in data and data["data"] and "jobListings" in (data["data"] or {})
            has_errors = "errors" in data
            print(f"[{status}] {v['label']} | pt={pt} ad={ad} | data={has_data} errors={has_errors}")
            if has_data and not has_errors:
                print("  SUCCESS:", json.dumps(data, indent=2)[:400])
                raise SystemExit
