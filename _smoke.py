"""Smoke test: rotator init + one real fetch_page."""
import sys
sys.path.insert(0, r"d:\PycharmProjects\AiSpiderProject\glassdoor")
import parallel_collector as pc

print("rotator enabled:", pc.rotator.enabled, "| nodes:", len(pc.rotator.nodes),
      "| current:", pc.rotator.current)

status, data = pc.fetch_page(1138, 1)
print("fetch_page status:", status)
if status == 200:
    er = (data.get("data") or {}).get("employerReviews") or {}
    print("reviews:", len(er.get("reviews") or []),
          "| numberOfPages:", er.get("numberOfPages"),
          "| total:", er.get("filteredReviewsCount"))
