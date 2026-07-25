from pymongo import MongoClient
db = MongoClient('mongodb://localhost:27017')['glassdoor']
print('=== app_employers ===')
total = db['app_employers'].count_documents({})
with_r = db['app_employers'].count_documents({'reviewCount': {'$gt': 0}})
print(f'Total: {total}, with reviews: {with_r}')

print('\nTop 10 companies by reviewCount:')
for doc in db['app_employers'].aggregate([{'$sort': {'reviewCount': -1}}, {'$limit': 10}]):
    print(f'  {doc.get("shortName","?")} (id={doc["employerId"]}): {doc.get("reviewCount",0)} reviews')

print('\n=== app_reviews ===')
review_total = db['app_reviews'].count_documents({})
distinct_employers = len(db['app_reviews'].distinct('employerId'))
print(f'Total reviews: {review_total}, from {distinct_employers} companies')
