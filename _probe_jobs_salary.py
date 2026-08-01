"""Debug JobsSearchAndroid and salary queries"""
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

QUERY_JOBS = (
    "query JobsSearchAndroid($adSlotName: String, $pageTypeEnum: PageTypeEnum, $searchParams: SearchParams, "
    "$onlyCurrentGlassdoorAwards: Boolean! = true, $blcAwardsLimit: Int! = 0, $bptwAwardsLimit: Int! = 0) { "
    "jobListings(contextHolder: { adSlotName: $adSlotName pageTypeEnum: $pageTypeEnum searchParams: $searchParams } ) { "
    "jobListings { jobview { __typename ...JobViewFragment } } "
    "paginationCursors { __typename ...PaginationCursorFragment } totalJobsCount } } "
    "fragment JobViewFragment on JobView { job { listingId jobTitleText } header { adOrderId ageInDays applied appliedSource "
    "easyApply expired goc locId locationName locationType normalizedJobTitle employerNameFromSearch employer { __typename name "
    "squareLogoUrl id } payPeriod payPeriodAdjustedPay { p90 p50 p10 } occupations { key } rating salarySource "
    "savedJobId isSponsoredJob payCurrency jobViewUrl jobCountryId jobResultTrackingKey } overview { primaryIndustry { industryId "
    "industryName sectorId sectorName } } gaTrackerData { requiresTracking trackingUrl } } "
    "fragment PaginationCursorFragment on PaginationCursor { cursor pageNumber }"
)

# Test 1: Jobs with keyword + employerId filter
print("=== Test 1: JobsSearchAndroid keyword+employerId ===")
body = {
    "operationName": "JobsSearchAndroid",
    "variables": {
        "adSlotName": "app_top_jobs_employer",
        "pageTypeEnum": "EI_JOBS",
        "searchParams": {
            "filterParams": [
                {"filterKey": "employerId", "values": [str(eid)]}
            ],
            "keyword": "software engineer",
            "pageNumber": 1,
            "pageSize": 10,
            "sort": "DATE"
        },
        "onlyCurrentGlassdoorAwards": False,
    },
    "query": QUERY_JOBS,
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "JobsSearchAndroid")

# Test 2: Jobs with only employerId, no keyword
print("\n=== Test 2: JobsSearchAndroid employerId only ===")
body["variables"]["searchParams"]["keyword"] = ""
post(body, "JobsSearchAndroid")

# Test 3: Jobs with EI_OVERVIEW pageType
print("\n=== Test 3: JobsSearchAndroid EI_OVERVIEW ===")
body["variables"]["pageTypeEnum"] = "EI_OVERVIEW"
post(body, "JobsSearchAndroid")

# Test 4: SearchAggregatedSalaryEstimates with keyword
print("\n=== Test 4: SearchAggregatedSalaryEstimates with keyword ===")
body = {
    "operationName": "SearchAggregatedSalaryEstimates",
    "variables": {
        "employerId": eid,
        "employerName": ename,
        "jobTitle": "software engineer",
        "pageNumber": 1,
        "pageSize": 10,
        "sort": "SALARY_ASC"
    },
    "query": (
        "query SearchAggregatedSalaryEstimates($cityId: Int, $countryId: Int, $metroId: Int, $stateId: Int, "
        "$employerId: Int, $employerName: String, $goc: GOCIdent, $jobTitle: String!, $jobTitleId: Int, "
        "$pageNumber: Int!, $pageSize: Int!, $payPeriod: PayPeriodEnum, $sort: SalariesSortOrder, $yearsOfExperience: YearsOfExperienceEnum) { "
        "aggregatedSalaryEstimates(aggregatedSalaryEstimatesInput: { employer: { id: $employerId name: $employerName } goc: $goc "
        "jobTitle: { id: $jobTitleId text: $jobTitle } location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } "
        "page: { num: $pageNumber size: $pageSize } sort: $sort viewAsPayPeriodId: $payPeriod yearsOfExperience: $yearsOfExperience } ) { "
        "numPages results { basePayStatistics { mean } currency { code id } employer { id name shortName squareLogoUrl } "
        "jobTitle { id text gocId mgocId } payPeriod totalAdditionalPayStatistics { mean } totalPayStatistics { __typename ...PayStatistics } } "
        "resultCount queryLocation { id name type } } } "
        "fragment PayStatistics on StatisticsResult { percentiles { ident value } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "SearchAggregatedSalaryEstimates")

# Test 5: SearchSalaryEstimates keyword search
print("\n=== Test 5: SearchSalaryEstimates keyword ===")
body = {
    "operationName": "SearchSalaryEstimates",
    "variables": {
        "employerId": eid,
        "employerName": ename,
        "keyword": "software engineer",
        "pageNumber": 1,
        "pageSize": 10,
        "sort": "SALARY_ASC"
    },
    "query": (
        "query SearchSalaryEstimates($cityId: Int, $countryId: Int, $employerId: Int, $employerName: String, "
        "$industrySectorId: IndustrySectorIdent, $keyword: String!, $metroId: Int, $pageNumber: Int!, $pageSize: Int!, "
        "$sort: SalariesSortOrder, $stateId: Int, $yearsOfExperience: YearsOfExperienceEnum) { "
        "keywordSalaryEstimates(keywordSalaryEstimatesInput: { keyword: $keyword keywordAggregateSalaryInput: { "
        "employer: { id: $employerId name: $employerName } page: { num: $pageNumber size: $pageSize } sort: $sort } "
        "keywordOccSalaryInput: { yearsOfExperience: $yearsOfExperience industry: $industrySectorId } "
        "location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } } ) { "
        "aggregateSalaryResponse { numPages results { basePayStatistics { mean } currency { code id } employer { id name shortName squareLogoUrl } "
        "jobTitle { id text gocId mgocId } payPeriod totalAdditionalPayStatistics { mean } totalPayStatistics { __typename ...PayStatistics } } "
        "resultCount queryLocation { id name type } } } } "
        "fragment PayStatistics on StatisticsResult { percentiles { ident value } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "SearchSalaryEstimates")
