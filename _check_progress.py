"""Check discovery progress."""
from pymongo import MongoClient
from collections import Counter

c = MongoClient("mongodb://localhost:27017")
db = c.glassdoor

total = db.app_employers.count_documents({})
reviews = db.app_reviews.count_documents({})
print(f"Total employers: {total}")
print(f"Total reviews:   {reviews}")

# Count by discoveredVia
pipeline = [
    {"$group": {"_id": "$discoveredVia", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}},
    {"$limit": 30},
]
print("\nTop discovery terms:")
for r in db.app_employers.aggregate(pipeline):
    print(f"  {r['_id']:<20s} {r['count']:>6d}")

# Rating stats
pipeline2 = [
    {"$group": {"_id": None, "avgRating": {"$avg": "$overallRating"},
                "totalReviewCount": {"$sum": "$reviewCount"}}},
]
for r in db.app_employers.aggregate(pipeline2):
    print(f"\nAvg rating: {r.get('avgRating', 0):.2f}")
    print(f"Sum reviewCount: {r.get('totalReviewCount', 0):,}")
