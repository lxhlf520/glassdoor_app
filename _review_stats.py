"""Review collection progress snapshot."""
from pymongo import MongoClient
from datetime import datetime, timezone

c = MongoClient("mongodb://localhost:27017")
db = c.glassdoor

n_reviews = db.app_reviews.count_documents({})
print(f"reviews total: {n_reviews:,}")

done = db.app_review_progress.count_documents(
    {"status": {"$in": ["done", "done_empty", "done_with_errors"]}})
in_prog = db.app_review_progress.count_documents({"status": "in_progress"})
print(f"employers done: {done:,}  in_progress: {in_prog:,}")

# 最近活动时间
latest = db.app_review_progress.find_one(
    sort=[("updatedAt", -1)], projection={"updatedAt": 1, "employerId": 1})
if latest:
    print("latest progress update:", latest.get("updatedAt"), "eid:", latest.get("employerId"))

now = datetime.now(timezone.utc)
print("now:", now)
