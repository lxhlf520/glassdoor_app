"""Compare HistoricalCommentary vs app_employers"""
from pymongo import MongoClient
from collections import Counter

c = MongoClient("mongodb://localhost:27017")
db = c["glassdoor"]

hc = db["HistoricalCommentary"]
app = db["app_employers"]
prog = db["app_discovery_progress"]

# Discovery progress
print("=== Discovery Progress ===")
total_done = prog.count_documents({})
print(f"Completed terms: {total_done}")
for p in prog.find().sort("doneAt", -1).limit(15):
    key = p.get("key", "")
    nc = p.get("newCount", 0)
    done = p.get("doneAt", "")
    print(f"  {key}: +{nc} new, done {done}")

# Load IDs
print("\n=== Loading IDs ===")
hc_ids = set(d["Employer_ID"] for d in hc.find({}, {"Employer_ID": 1}))
app_ids = set(d["employerId"] for d in app.find({}, {"employerId": 1}))

only_old = hc_ids - app_ids
only_new = app_ids - hc_ids
common = hc_ids & app_ids

print(f"HistoricalCommentary: {len(hc_ids):,}")
print(f"app_employers:        {len(app_ids):,}")
print(f"Common:               {len(common):,}")
print(f"Only in old (missing):{len(only_old):,}")
print(f"Only in new (extra):  {len(only_new):,}")

# ID ranges
print(f"\n=== ID Ranges ===")
print(f"HistoricalCommentary: min={min(hc_ids):,} max={max(hc_ids):,}")
print(f"app_employers:        min={min(app_ids):,} max={max(app_ids):,}")

# Distribution of old-only IDs
print(f"\n=== Old-only ID distribution ===")
thresholds = [1000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]
for t in thresholds:
    below = sum(1 for eid in only_old if eid < t)
    print(f"  ID < {t:>12,}: {below:>8,} ({below/len(only_old)*100:.1f}%)")

print(f"\n=== New-only ID distribution ===")
for t in thresholds:
    below = sum(1 for eid in only_new if eid < t)
    print(f"  ID < {t:>12,}: {below:>8,} ({below/len(only_new)*100:.1f}%)")

# Review count of app_employers
print(f"\n=== app_employers review counts ===")
has_reviews = app.count_documents({"reviewCount": {"$gt": 0}})
zero_reviews = app.count_documents({"reviewCount": 0})
print(f"  reviewCount > 0: {has_reviews:,}")
print(f"  reviewCount = 0: {zero_reviews:,}")

# Old-only status distribution
print(f"\n=== Old-only status distribution (all) ===")
# We need to check status of ALL old-only entries
pipeline = [
    {"$match": {"Employer_ID": {"$in": list(only_old)}}},
    {"$group": {"_id": "$status", "count": {"$sum": 1}}}
]
for doc in hc.aggregate(pipeline):
    print(f"  {doc['_id']}: {doc['count']:,}")

# Check how old was collected (look at collector.py patterns)
print(f"\n=== HistoricalCommentary schema fields ===")
sample = hc.find_one()
print(f"  Fields: {list(sample.keys())}")

# Check if old version used ID enumeration
# Count IDs in ranges
print(f"\n=== HistoricalCommentary ID density by 100K buckets (first 1M) ===")
for start in range(0, 1_000_000, 100_000):
    end = start + 100_000
    cnt = sum(1 for eid in hc_ids if start <= eid < end)
    if cnt > 0:
        print(f"  [{start:>10,} - {end:>10,}): {cnt:>6,}")
