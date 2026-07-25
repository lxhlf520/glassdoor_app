"""Check discovery progress records and DB totals."""
from pymongo import MongoClient

c = MongoClient("mongodb://localhost:27017")
db = c.glassdoor

print("employers:", db.app_employers.count_documents({}))
print("reviews:  ", db.app_reviews.count_documents({}))

print("\nProgress records (deep phase):")
for r in db.app_discovery_progress.find({"phase": "deep"}).sort("doneAt", 1):
    print(f"  {r['term']:<20s} +{r.get('newCount', 0)}")
print("deep done count:", db.app_discovery_progress.count_documents({"phase": "deep"}))
print("bigram done count:", db.app_discovery_progress.count_documents({"phase": "bigram2"}))
