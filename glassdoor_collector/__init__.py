"""Glassdoor Collector - APP API data collection toolkit.

Modules:
- discover: company discovery via employerSearchRG
- collector: single-threaded review collector (legacy)
- parallel: multi-threaded parallel review collector
- modules: benefits/interviews/jobs module collector
- infra: shared infrastructure (rate limiting, node rotation, fingerprint rotation)
- clash: FlClash/mihomo proxy controller
- migrate: MongoDB to PostgreSQL migration
"""

__version__ = "1.0.0"
