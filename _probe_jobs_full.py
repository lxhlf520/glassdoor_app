"""Try full JobsSearchAndroid query exactly as in APK"""
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
        print("errors:", json.dumps(data["errors"], indent=2)[:500])
    else:
        print("data keys:", list((data.get("data") or {}).keys()))
        print(json.dumps(data, indent=2)[:600])
    return data


c = MongoClient("mongodb://localhost:27017")
emp = c["glassdoor"]["app_employers"].find_one({"employerId": 6036})
eid = emp["employerId"]

FULL_QUERY = (
    "query JobsSearchAndroid($adSlotName: String, $pageTypeEnum: PageTypeEnum, $searchParams: SearchParams, "
    "$onlyCurrentGlassdoorAwards: Boolean! = true, $blcAwardsLimit: Int! = 30, $bptwAwardsLimit: Int! = 30) { "
    "jobListings(contextHolder: { adSlotName: $adSlotName pageTypeEnum: $pageTypeEnum searchParams: $searchParams } ) { "
    "jobListings { jobview { __typename ...JobViewFragment } } "
    "searchResultsMetadata { searchCriteria { keyword location { id name } } jobAlert { jobAlertId emailFrequencyEnumId } } "
    "paginationCursors { __typename ...PaginationCursorFragment } companyFilterOptions { id shortName } "
    "filterOptions totalJobsCount } } "
    "fragment GlassdoorAwards on Employer { bestLedCompanies(limit: $blcAwardsLimit, onlyCurrent: $onlyCurrentGlassdoorAwards) { "
    "id isCurrent name timePeriod rank } bestPlacesToWork(limit: $bptwAwardsLimit, onlyCurrent: $onlyCurrentGlassdoorAwards) { "
    "id isCurrent listType name timePeriod rank } } "
    "fragment JobViewFragment on JobView { job { listingId jobTitleText } header { adOrderId ageInDays applied appliedSource "
    "easyApply expired goc locId locationName locationType normalizedJobTitle employerNameFromSearch employer { __typename name "
    "squareLogoUrl id ...GlassdoorAwards } payPeriod payPeriodAdjustedPay { p90 p50 p10 } occupations { key } rating salarySource "
    "savedJobId isSponsoredJob payCurrency jobViewUrl jobCountryId jobResultTrackingKey } overview { primaryIndustry { industryId "
    "industryName sectorId sectorName } } gaTrackerData { requiresTracking trackingUrl } } "
    "fragment PaginationCursorFragment on PaginationCursor { cursor pageNumber }"
)

print("=== Full JobsSearchAndroid with keyword ===")
body = {
    "operationName": "JobsSearchAndroid",
    "variables": {
        "adSlotName": "",
        "pageTypeEnum": "GD_JOB_SEARCH",
        "searchParams": {
            "keyword": "software engineer",
            "pageNumber": 1,
            "pageSize": 10,
        },
    },
    "query": FULL_QUERY,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "JobsSearchAndroid")

print("\n=== Full JobsSearchAndroid with employer filter ===")
body["variables"]["searchParams"] = {
    "keyword": "",
    "pageNumber": 1,
    "pageSize": 10,
    "filterParams": [{"filterKey": "employerId", "values": [str(eid)]}]
}
post(body, "JobsSearchAndroid")
