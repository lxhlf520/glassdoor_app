"""Glassdoor 全模块综合测试

测试范围：
1. 核心模块导入
2. MongoDB 连接 + 数据统计
3. PostgreSQL 连接 + 数据验证
4. Glassdoor API 连通性（1 次请求）
5. collector_infra 基础设施状态
6. module_collector 子模块结构
"""
import sys
import time

REGISTRY: list[tuple[str, "callable"]] = []
RESULTS: list[tuple[str, bool, str]] = []


def register(name: str):
    """注册测试函数的装饰器"""
    def decorator(fn):
        REGISTRY.append((name, fn))
        return fn
    return decorator


# ============================================================================
# 1. 核心模块导入
# ============================================================================
@register("import clash_api")
def test_import_clash():
    from clash_api import ClashAPI
    api = ClashAPI()
    alive = api.alive()
    assert isinstance(alive, bool)


@register("import collector_infra")
def test_import_infra():
    from collector_infra import (
        rate_limiter, rotator, fp_rotator, fetch_graphql, mongo_client, DB_NAME
    )
    assert DB_NAME == "glassdoor"


@register("import collector")
def test_import_collector():
    from collector import GlassdoorCollector
    c = GlassdoorCollector()
    assert c.db is not None


@register("import discover_all")
def test_import_discover():
    from discover_all import CompanyDiscoverer, SINGLE_LETTER_TERMS, DEEP_TERMS
    d = CompanyDiscoverer()
    assert len(SINGLE_LETTER_TERMS) == 26
    assert len(DEEP_TERMS) >= 30


@register("import parallel_collector")
def test_import_parallel():
    from parallel_collector import ParallelCollector
    pc = ParallelCollector()
    assert pc.db is not None


@register("import module_collector")
def test_import_modules():
    from module_collector import (
        BenefitsCollector, InterviewsCollector, JobsCollector,
        BaseModuleCollector,
    )
    assert issubclass(BenefitsCollector, BaseModuleCollector)
    assert issubclass(InterviewsCollector, BaseModuleCollector)
    assert issubclass(JobsCollector, BaseModuleCollector)


# ============================================================================
# 2. MongoDB 连接 + 数据统计
# ============================================================================
@register("MongoDB connectivity")
def test_mongo_connect():
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)
    client.server_info()
    db = client["glassdoor"]
    collections = db.list_collection_names()
    assert "app_employers" in collections
    assert "app_reviews" in collections


@register("MongoDB data stats")
def test_mongo_stats():
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017")
    db = client["glassdoor"]
    emp_count = db["app_employers"].count_documents({})
    review_count = db["app_reviews"].count_documents({})
    assert emp_count >= 456000, f"employers only {emp_count}"
    # 追加统计信息
    RESULTS[-1] = (RESULTS[-1][0], True,
                   f"OK: employers={emp_count:,}, reviews={review_count:,}")


# ============================================================================
# 3. PostgreSQL 连接 + 数据验证
# ============================================================================
@register("PostgreSQL connectivity")
def test_pg_connect():
    import psycopg2
    conn = psycopg2.connect(
        host="localhost", port=5432,
        user="postgres", password="long123456",
        dbname="glassdoor", connect_timeout=5,
    )
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM employers")
    pg_count = cur.fetchone()[0]
    cur.close(); conn.close()
    assert pg_count >= 456000, f"PG employers only {pg_count}"
    RESULTS[-1] = (RESULTS[-1][0], True, f"OK: pg_employers={pg_count:,}")


@register("PG-Mongo data consistency")
def test_pg_mongo_consistency():
    import psycopg2
    from pymongo import MongoClient
    conn = psycopg2.connect(
        host="localhost", port=5432,
        user="postgres", password="long123456",
        dbname="glassdoor",
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT employer_id, name, review_count FROM employers "
        "ORDER BY RANDOM() LIMIT 100"
    )
    pg_rows = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    cur.close(); conn.close()

    mongo = MongoClient("mongodb://localhost:27017")
    col = mongo["glassdoor"]["app_employers"]
    mismatch = 0
    for eid, (pg_name, pg_rc) in pg_rows.items():
        doc = col.find_one({"employerId": eid})
        if not doc:
            mismatch += 1; continue
        m_name = doc.get("shortName") or doc.get("name", "")
        m_rc = doc.get("reviewCount") or 0
        if m_name != pg_name or m_rc != pg_rc:
            mismatch += 1
    assert mismatch == 0, f"{mismatch}/100 mismatches"
    RESULTS[-1] = (RESULTS[-1][0], True, f"OK: 100/100 consistent")


# ============================================================================
# 4. Glassdoor API 连通性
# ============================================================================
@register("Glassdoor API ping")
def test_api_ping():
    import curl_cffi.requests as requests
    import uuid

    gd_id = str(uuid.uuid4())
    resp = requests.post(
        "https://api.glassdoor.com/mobile-graph",
        params={"locale": "zh_CN_#Hans"},
        headers={
            "x-gd-id": gd_id,
            "x-gd-asst": f"{time.time()}.0",
            "x-gd-operation": "SearchCompanies",
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
        },
        json={
            "operationName": "SearchCompanies",
            "variables": {
                "employerSearchInput": {
                    "employerName": "a", "numPerPage": 5, "pageRequested": 1,
                }
            },
            "query": (
                "query SearchCompanies($employerSearchInput: EmployerSearchInput) { "
                "  employerSearchRG(employerSearchInput: $employerSearchInput) { "
                "    __typename pageNumber numOfPagesAvailable "
                "  } }"
            ),
            "extensions": {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}},
        },
        impersonate="chrome110", timeout=30,
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    data = resp.json()
    sr = (data.get("data") or {}).get("employerSearchRG") or {}
    pages = sr.get("numOfPagesAvailable", 0)
    assert pages > 0, "numOfPagesAvailable is 0"
    RESULTS[-1] = (RESULTS[-1][0], True, f"OK: numOfPagesAvailable={pages}")


# ============================================================================
# 5. 基础设施状态
# ============================================================================
@register("infra rate limiter")
def test_rate_limiter():
    from collector_infra import rate_limiter
    assert rate_limiter.rate > 0


@register("infra fingerprint pool")
def test_fp_pool():
    from collector_infra import fp_rotator, FP_POOL
    fp = fp_rotator.take()
    assert fp in FP_POOL


# ============================================================================
# 6. module_collector 子模块验证
# ============================================================================
@register("BenefitsCollector structure")
def test_benefits_structure():
    from module_collector import BenefitsCollector
    bc = BenefitsCollector(max_employers=0)
    assert bc.name == "benefits"
    assert bc.out_collection == "app_benefits"


@register("InterviewsCollector structure")
def test_interviews_structure():
    from module_collector import InterviewsCollector
    ic = InterviewsCollector(max_employers=0)
    assert ic.name == "interviews"
    assert ic.out_collection == "app_interviews"


@register("JobsCollector structure")
def test_jobs_structure():
    from module_collector import JobsCollector
    jc = JobsCollector(max_employers=0)
    assert jc.name == "jobs"
    assert jc.out_collection == "app_jobs"


# ============================================================================
# 汇总
# ============================================================================
def main():
    n_total = len(REGISTRY)
    if n_total == 0:
        print("ERROR: No tests registered!")
        return 1

    print("=" * 60)
    print(f"Glassdoor 全模块综合测试 ({n_total} tests)")
    print("=" * 60)

    for name, fn in REGISTRY:
        try:
            fn()
            RESULTS.append((name, True, "OK"))
        except Exception as e:
            RESULTS.append(
                (name, False, f"{type(e).__name__}: {str(e)[:200]}")
            )

    for name, ok, msg in RESULTS:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}")
        if not ok or (ok and msg != "OK"):
            print(f"         {msg}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{'=' * 60}")
    print(f"Result: {passed}/{len(RESULTS)} passed, {failed} failed")

    if failed > 0:
        print("\nFAILURES:")
        for name, ok, msg in RESULTS:
            if not ok:
                print(f"  - {name}: {msg}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
