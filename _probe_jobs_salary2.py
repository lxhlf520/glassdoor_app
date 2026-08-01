"""More targeted probes for jobs/salary endpoints"""
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
ename = emp.get("shortName", "")

# Try JobDetailsAndroid with a known listingId from web? We don't have one.
# Instead try ListSavedJobsAndroid (no employer filter needed)
print("=== ListSavedJobsAndroid (auth required probably) ===")
body = {
    "operationName": "ListSavedJobsAndroid",
    "variables": {"adSlotName": "", "numPerPage": 10, "pageNumber": 1},
    "query": (
        "query ListSavedJobsAndroid($adSlotName: String, $numPerPage: Int, $pageNumber: Int) { "
        "listSavedJobs(adSlotName: $adSlotName, numPerPage: $numPerPage, pageNumber: $pageNumber) { "
        "jobViews { __typename ...JobViewFragment } pageNumber totalPages } } "
        "fragment JobViewFragment on JobView { job { listingId jobTitleText } header { adOrderId ageInDays applied appliedSource "
        "easyApply expired goc locId locationName locationType normalizedJobTitle employerNameFromSearch employer { __typename name "
        "squareLogoUrl id } payPeriod payPeriodAdjustedPay { p90 p50 p10 } occupations { key } rating salarySource "
        "savedJobId isSponsoredJob payCurrency jobViewUrl jobCountryId jobResultTrackingKey } overview { primaryIndustry { industryId "
        "industryName sectorId sectorName } } gaTrackerData { requiresTracking trackingUrl } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "ListSavedJobsAndroid")

# Try SalarySearchFilters (no variables)
print("\n=== SalarySearchFilters ===")
body = {
    "operationName": "SalarySearchFilters",
    "query": "query SalarySearchFilters { industrySectors { id name } yearsOfExperience { id } }",
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "SalarySearchFilters")

# Try GetSalaryReport with gocId
print("\n=== GetSalaryReport with gocId ===")
body = {
    "operationName": "GetSalaryReport",
    "variables": {
        "employerId": eid,
        "jobTitle": "Software Engineer",
        "gocId": 142,  # guess
        "salaryReportPageNumber": 1,
        "salaryReportPageSize": 10,
        "salaryReportSort": "SUBMISSION_DATE"
    },
    "query": (
        "query GetSalaryReport($cityId: Int, $countryId: Int, $employerId: Int!, $jobTitle: String!, $jobTitleId: Int, "
        "$metroId: Int, $stateId: Int, $yearsOfExperience: YearsOfExperienceEnum, $gocId: Int, "
        "$salaryReportSort: IndividualSalariesSortPropertyEnum, $salaryReportPageNumber: Int!, $salaryReportPageSize: Int!) { "
        "fusedIndividualSalaries(fusedIndividualSalariesInput: { individualSalariesForGocInput: { employer: { id: $employerId } "
        "goc: { gocId: $gocId } location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } "
        "jobTitle: { id: $jobTitleId text: $jobTitle } page: { num: $salaryReportPageNumber size: $salaryReportPageSize } "
        "sort: $salaryReportSort yearsOfExperience: $yearsOfExperience } "
        "individualSalariesForJobTitleInput: { employer: { id: $employerId } jobTitle: { id: $jobTitleId text: $jobTitle } "
        "location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } "
        "page: { num: $salaryReportPageNumber size: $salaryReportPageSize } sort: $salaryReportSort yearsOfExperience: $yearsOfExperience } } ) { "
        "individualSalariesForJobTitle { totalNumberOfPages totalNumberOfRecords currentPageNumber results { jobTitle "
        "annualTotalPayAnonymityMinAmount annualTotalPayAnonymityMaxAmount annualBasePayAmount annualCashBonusAmount "
        "annualStockBonusAmount displayAnonymityPayAmounts reviewDate payPeriod yearsOfExperience city { id name } country { id name } "
        "metro { id name } state { id name } } } individualSalariesForGoc { totalNumberOfPages totalNumberOfRecords currentPageNumber "
        "results { jobTitle payPeriod annualBasePayAmount annualAdditionalPayAmount annualTotalPayAnonymityMinAmount "
        "annualTotalPayAnonymityMaxAmount displayAnonymityPayAmounts reviewDate yearsOfExperience city { id name } country { id name } "
        "metro { id name } state { id name } } } } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "GetSalaryReport")

# Try web GraphQL endpoint
print("\n=== Web GraphQL: jobs by employer ===")
resp = requests.get(
    "https://www.glassdoor.com/api/webapp/jobs.json",
    params={"employerId": eid, "page": 1, "pageSize": 10},
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    timeout=30,
)
print(f"[web jobs] HTTP {resp.status_code}")
print(resp.text[:500])
