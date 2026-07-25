"""Backfill discovery progress for already-completed bigram terms.

A bigram term is considered done if it has >= 150 entries with discoveredVia=term
(2 pages x 100 = ~200, some margin for dup/invalid).
Deep terms are NOT marked - Phase 2 has not run yet.
"""
from pymongo import MongoClient
from datetime import datetime, timezone

BIGRAM_TERMS = [
    "in", "er", "an", "on", "at", "es", "en", "or", "al", "ti",
    "te", "ic", "ar", "st", "re", "le", "ra", "li", "io", "nt",
    "ed", "it", "ve", "co", "de", "ri", "ro", "ne", "ma", "ta",
    "si", "el", "la", "ch", "me", "di", "un", "no", "pe", "ac",
    "ou", "se", "ca", "us", "ce", "il", "be", "pa", "mi", "to",
    "ni", "is", "po", "vi", "ci", "he", "fo", "sc", "pr", "mo",
]

c = MongoClient("mongodb://localhost:27017")
db = c.glassdoor

done = []
pending = []
for term in BIGRAM_TERMS:
    cnt = db.app_employers.count_documents({"discoveredVia": term})
    if cnt >= 150:
        done.append((term, cnt))
    else:
        pending.append((term, cnt))

print(f"Done bigrams ({len(done)}):")
for t, n in done:
    print(f"  {t:<6s} {n}")
print(f"\nPending bigrams ({len(pending)}):")
for t, n in pending:
    print(f"  {t:<6s} {n}")

# Backfill
now = datetime.now(timezone.utc)
for term, cnt in done:
    db.app_discovery_progress.update_one(
        {"key": f"bigram2:{term}"},
        {"$set": {"key": f"bigram2:{term}", "phase": "bigram2",
                  "term": term, "newCount": cnt, "doneAt": now,
                  "backfilled": True}},
        upsert=True,
    )
print(f"\nBackfilled {len(done)} progress records.")
