"""Inspect benefits and interviews response structure"""
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
        print(json.dumps(data, indent=2)[:1200])
    return data


c = MongoClient("mongodb://localhost:27017")
emp = c["glassdoor"]["app_employers"].find_one({"employerId": 6036})
eid = emp["employerId"]

# Benefits
body = {
    "operationName": "EmployerBenefits",
    "variables": {
        "employerId": eid,
        "countryId": 1,
        "benefitsReviewsPageNumber": 1,
        "benefitsReviewsPageSize": 5,
        "employmentStatus": "REGULAR",
    },
    "query": (
        "query EmployerBenefits($employerId: Int!, $countryId: Int!, $benefitsReviewsPageNumber: Int!, "
        "$benefitsReviewsPageSize: Int!, $employmentStatus: EmploymentStatusEnum) { "
        "benefitsOverviewForCountry(benefitsInput: { employerId: $employerId countryId: $countryId employmentStatus: $employmentStatus }) { "
        "employerBenefitSummary { comment } overallBenefitRating totalBenefitReviews "
        "benefitsCategoryToStatisticAggregates { benefitCategory { id name } "
        "benefitStatisticAggregateList { benefit { id name } benefitRatingDenominator benefitRatingNumerator verified } } } "
        "countriesForEmployerBenefits(employerId: $employerId) { id name } "
        "employmentStatusEnumsForBenefitReviews(employerId: $employerId, countryId: $countryId) "
        "overviewBenefitReviews(benefitsInput: { employmentStatus: $employmentStatus countryId: $countryId employerId: $employerId "
        "page: { size: $benefitsReviewsPageSize num: $benefitsReviewsPageNumber } }) { "
        "__typename ...EmployerBenefitsReviewsFragment } } "
        "fragment EmployerBenefitsReviewsFragment on BenefitReview { id rating createDate currentJob userEnteredJobTitle "
        "city { name state { name } metro { name } country { name } } benefitComments { helpfulVotes comment id } }"
    ),
    "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
}
print("=== EmployerBenefits ===")
post(body, "EmployerBenefits")

# Interviews
body = {
    "operationName": "EmployerInterviewsList",
    "variables": {
        "employerId": eid,
        "page": 1,
        "pageSize": 5,
        "sort": "RELEVANCE",
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
print("\n=== EmployerInterviewsList ===")
post(body, "EmployerInterviewsList")
