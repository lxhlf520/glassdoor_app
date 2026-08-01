"""Probe Pay & Benefits / Jobs / Interviews GraphQL endpoints"""
import json
import time
import uuid

import curl_cffi.requests as requests
from pymongo import MongoClient

MONGO_URI = "mongodb://localhost:27017"

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
    if resp.status_code == 200:
        data = resp.json()
        if "errors" in data:
            print("  errors:", json.dumps(data["errors"], indent=2)[:500])
        return data
    print("  text:", resp.text[:300])
    return None


# Pick a test employer with reviews
c = MongoClient(MONGO_URI)
db = c["glassdoor"]
emp = db["app_employers"].find_one({"reviewCount": {"$gt": 100}}, sort=[("reviewCount", -1)])
print("Test employer:", emp)
eid = emp["employerId"]
ename = emp.get("shortName", "")

# 1. Benefits
print("\n=== 1. EmployerBenefits ===")
body = {
    "operationName": "EmployerBenefits",
    "variables": {
        "employerId": eid,
        "countryId": 1,
        "benefitsReviewsPageNumber": 1,
        "benefitsReviewsPageSize": 100,
        "employmentStatus": "REGULAR"
    },
    "query": (
        "query EmployerBenefits($employerId: Int!, $countryId: Int!, $benefitsReviewsPageNumber: Int!, "
        "$benefitsReviewsPageSize: Int!, $employmentStatus: EmploymentStatusEnum) { "
        "benefitsOverviewForCountry(benefitsInput: { employerId: $employerId countryId: $countryId employmentStatus: $employmentStatus } ) { "
        "employerBenefitSummary { comment } overallBenefitRating totalBenefitReviews "
        "benefitsCategoryToStatisticAggregates { benefitCategory { id name } "
        "benefitStatisticAggregateList { benefit { id name } benefitRatingDenominator benefitRatingNumerator verified } } } "
        "countriesForEmployerBenefits(employerId: $employerId) { id name } "
        "employmentStatusEnumsForBenefitReviews(employerId: $employerId, countryId: $countryId) "
        "overviewBenefitReviews(benefitsInput: { employmentStatus: $employmentStatus countryId: $countryId employerId: $employerId "
        "page: { size: $benefitsReviewsPageSize num: $benefitsReviewsPageNumber } } ) { "
        "__typename ...EmployerBenefitsReviewsFragment } } "
        "fragment EmployerBenefitsReviewsFragment on BenefitReview { id rating createDate currentJob userEnteredJobTitle "
        "city { name state { name } metro { name } country { name } } benefitComments { helpfulVotes comment id } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "EmployerBenefits")

# 2. Interviews
print("\n=== 2. EmployerInterviewsList ===")
body = {
    "operationName": "EmployerInterviewsList",
    "variables": {
        "employerId": eid,
        "page": 1,
        "pageSize": 100,
        "sort": "RELEVANCE"
    },
    "query": (
        "query EmployerInterviewsList($employerId: Int!, $difficulties: [InterviewDifficultyLevelEnum], $gocId: GOCIdent, "
        "$location: LocationIdent, $jobTitle: JobTitleIdent, $outcomes: [InterviewOutcomeEnum], $page: Int!, $pageSize: Int!, "
        "$sort: InterviewsSortOrderEnum!) { "
        "employerInterviewsList: employerInterviewsIG(employerInterviewsInput: { employer: { id: $employerId } "
        "difficulties: $difficulties goc: $gocId jobTitle: $jobTitle location: $location outcomes: $outcomes "
        "page: { num: $page size: $pageSize } sort: $sort } ) { "
        "interviews { __typename ...EmployerInterviewFragment } filteredInterviewCount totalNumberOfPages queryJobTitle { mgocId } "
        "employer { primaryIndustryId } } } "
        "fragment EmployerInterviewFragment on InterviewIG { advice countHelpful difficulty id experience employer { name squareLogoUrl } "
        "employerResponses { response responseDateTime } featured jobTitle { text } negotiationDescription outcome processDescription "
        "reviewDateTime source userQuestions { question answerCount } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "EmployerInterviewsList")

# 3. Jobs
print("\n=== 3. JobsSearchAndroid (by employer) ===")
body = {
    "operationName": "JobsSearchAndroid",
    "variables": {
        "adSlotName": "app_top_jobs_employer",
        "pageTypeEnum": "EI_JOBS",
        "searchParams": {
            "filterParams": [
                {"filterKey": "employerId", "values": [str(eid)]}
            ],
            "pageNumber": 1,
            "pageSize": 100,
            "sort": "DATE"
        },
        "onlyCurrentGlassdoorAwards": True,
        "blcAwardsLimit": 0,
        "bptwAwardsLimit": 0
    },
    "query": (
        "query JobsSearchAndroid($adSlotName: String, $pageTypeEnum: PageTypeEnum, $searchParams: SearchParams, "
        "$onlyCurrentGlassdoorAwards: Boolean! = true, $blcAwardsLimit: Int! = 30, $bptwAwardsLimit: Int! = 30) { "
        "jobListings(contextHolder: { adSlotName: $adSlotName pageTypeEnum: $pageTypeEnum searchParams: $searchParams } ) { "
        "jobListings { jobview { __typename ...JobViewFragment } } "
        "searchResultsMetadata { searchCriteria { keyword location { id name } } "
        "jobAlert { jobAlertId emailFrequencyEnumId } } "
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
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "JobsSearchAndroid")

# 4. Salaries - aggregated by employer
print("\n=== 4. SearchAggregatedSalaryEstimates (by employer) ===")
body = {
    "operationName": "SearchAggregatedSalaryEstimates",
    "variables": {
        "employerId": eid,
        "employerName": ename,
        "jobTitle": "",
        "pageNumber": 1,
        "pageSize": 100,
        "sort": "SALARY_ASC"
    },
    "query": (
        "query SearchAggregatedSalaryEstimates($cityId: Int, $countryId: Int, $metroId: Int, $stateId: Int, "
        "$employerId: Int, $employerName: String, $goc: GOCIdent, $jobTitle: String!, $jobTitleId: Int, "
        "$pageNumber: Int!, $pageSize: Int!, $payPeriod: PayPeriodEnum, $sort: SalariesSortOrder, $yearsOfExperience: YearsOfExperienceEnum) { "
        "aggregatedSalaryEstimates(aggregatedSalaryEstimatesInput: { employer: { id: $employerId name: $employerName } goc: $goc "
        "jobTitle: { id: $jobTitleId text: $jobTitle } location: { cityId: $cityId countryId: $countryId metroId: $metroId stateId: $stateId } "
        "page: { num: $pageNumber size: $pageSize } sort: $sort viewAsPayPeriodId: $payPeriod yearsOfExperience: $yearsOfExperience } ) { "
        "numPages results { basePayStatistics { mean } currency { code id } employer { counts { globalJobCount { jobCount } } id name "
        "shortName squareLogoUrl ratings { overallRating } } jobTitle { id text gocId mgocId } payPeriod totalAdditionalPayStatistics { mean } "
        "totalPayStatistics { __typename ...PayStatistics } } resultCount queryLocation { id name type } } } "
        "fragment PayStatistics on StatisticsResult { percentiles { ident value } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
post(body, "SearchAggregatedSalaryEstimates")

# 5. GetSalaryReport for a specific job title (discovered from aggregated)
print("\n=== 5. GetSalaryReport (individual salaries) ===")
body = {
    "operationName": "GetSalaryReport",
    "variables": {
        "employerId": eid,
        "jobTitle": "Software Engineer",
        "salaryReportPageNumber": 1,
        "salaryReportPageSize": 100,
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
