"""PostgreSQL 连接池 + 表初始化

所有表使用 CREATE TABLE IF NOT EXISTS，首次调用 init_all_tables() 时自动建表。
每个线程应从连接池获取独立连接：db.get_conn()

用法:
    from .db import get_conn, init_all_tables
    init_all_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(...)
"""
import logging
import threading

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from .config import PG_CONFIG

log = logging.getLogger("db")

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

POOL_MIN = 2
POOL_MAX = 12  # 8 workers + modules workers + margin


def get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(POOL_MIN, POOL_MAX, **PG_CONFIG)
                log.info("PG pool created: %s:%s/%s",
                         PG_CONFIG["host"], PG_CONFIG["port"], PG_CONFIG["dbname"])
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def get_conn():
    """从连接池获取连接，自动丢弃断开的死连接。"""
    pool = get_pool()
    conn = pool.getconn()
    # 检查连接是否存活，死了就重建
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.rollback()  # 清除健康检查产生的事务
    except Exception:
        log.warning("stale PG connection, reconnecting")
        try:
            conn.close()
        except Exception:
            pass
        pool.putconn(conn, close=True)
        conn = pool.getconn()
    return conn


def put_conn(conn):
    get_pool().putconn(conn)


# ---------------------------------------------------------------------------
# 表 DDL
# ---------------------------------------------------------------------------

DDL_EMPLOYERS = """
CREATE TABLE IF NOT EXISTS employers (
    employer_id    INTEGER PRIMARY KEY,
    name           TEXT NOT NULL DEFAULT '',
    short_name     TEXT,
    logo_url       TEXT,
    overall_rating REAL,
    review_count   INTEGER DEFAULT 0,
    salary_count   INTEGER DEFAULT 0,
    job_count      INTEGER DEFAULT 0,
    discovered_via TEXT,
    discovered_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_employers_id ON employers (employer_id);
"""

DDL_DISCOVERY_PROGRESS = """
CREATE TABLE IF NOT EXISTS discovery_progress (
    key       TEXT PRIMARY KEY,
    phase     TEXT,
    term      TEXT,
    new_count INTEGER DEFAULT 0,
    done_at   TIMESTAMPTZ
);
"""

DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id                 BIGINT NOT NULL,
    employer_id               INTEGER NOT NULL,
    employer_name             TEXT DEFAULT '',
    page                      INTEGER DEFAULT 0,
    featured                  BOOLEAN DEFAULT FALSE,
    review_date_time          TEXT,
    summary                   TEXT,
    is_current_job            BOOLEAN,
    length_of_employment      TEXT,
    location_id               INTEGER,
    location_name             TEXT,
    rating_overall            REAL,
    rating_recommend          REAL,
    rating_ceo                REAL,
    rating_business_outlook   REAL,
    rating_career_opp         REAL,
    rating_comp_benefits      REAL,
    rating_culture_values     REAL,
    rating_diversity          REAL,
    rating_senior_leadership  REAL,
    rating_work_life_balance  REAL,
    pros                      TEXT,
    cons                      TEXT,
    advice                    TEXT,
    count_helpful             INTEGER DEFAULT 0,
    has_employer_response     BOOLEAN DEFAULT FALSE,
    employer_responses        JSONB DEFAULT '[]'::jsonb,
    job_title                 TEXT,
    collected_at              TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (review_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_employer ON reviews (employer_id);
"""

DDL_REVIEW_PROGRESS = """
CREATE TABLE IF NOT EXISTS review_progress (
    employer_id   INTEGER PRIMARY KEY,
    employer_name TEXT DEFAULT '',
    total_pages   INTEGER DEFAULT 0,
    done_pages    INTEGER[] DEFAULT '{}'::int[],
    failed_pages  INTEGER[] DEFAULT '{}'::int[],
    collected     INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'in_progress',
    started_at    TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""

DDL_BENEFITS = """
CREATE TABLE IF NOT EXISTS benefits (
    benefit_review_id            TEXT NOT NULL,
    employer_id                  INTEGER NOT NULL,
    employer_name                TEXT DEFAULT '',
    country_id                   INTEGER,
    type                         TEXT DEFAULT 'review',
    rating                       REAL,
    create_date                  TEXT,
    current_job                  BOOLEAN,
    user_entered_job_title       TEXT,
    city_name                    TEXT,
    state_name                   TEXT,
    metro_name                   TEXT,
    country_name                 TEXT,
    benefit_comments             JSONB DEFAULT '[]'::jsonb,
    comment                      TEXT,
    overall_benefit_rating       REAL,
    total_benefit_reviews        INTEGER DEFAULT 0,
    benefits_category_aggregates JSONB DEFAULT '[]'::jsonb,
    collected_at                 TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (benefit_review_id, employer_id)
);
CREATE INDEX IF NOT EXISTS idx_benefits_employer ON benefits (employer_id);
"""

DDL_BENEFITS_PROGRESS = """
CREATE TABLE IF NOT EXISTS benefits_progress (
    employer_id   INTEGER PRIMARY KEY,
    employer_name TEXT DEFAULT '',
    total_pages   INTEGER DEFAULT 0,
    next_page     INTEGER DEFAULT 1,
    collected     INTEGER DEFAULT 0,
    failed_pages  INTEGER[] DEFAULT '{}'::int[],
    status        TEXT DEFAULT 'in_progress',
    ctx           JSONB DEFAULT '{}'::jsonb,
    started_at    TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""

DDL_INTERVIEWS = """
CREATE TABLE IF NOT EXISTS interviews (
    interview_id         BIGINT NOT NULL,
    employer_id          INTEGER NOT NULL,
    employer_name        TEXT DEFAULT '',
    page                 INTEGER DEFAULT 0,
    advice               TEXT,
    count_helpful        INTEGER DEFAULT 0,
    difficulty           TEXT,
    experience           TEXT,
    employer_name_detail TEXT,
    employer_logo_url    TEXT,
    employer_responses   JSONB DEFAULT '[]'::jsonb,
    featured             BOOLEAN DEFAULT FALSE,
    job_title            TEXT,
    negotiation_desc     TEXT,
    outcome              TEXT,
    process_description  TEXT,
    review_date_time     TEXT,
    source               TEXT,
    user_questions       JSONB DEFAULT '[]'::jsonb,
    collected_at         TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (interview_id)
);
CREATE INDEX IF NOT EXISTS idx_interviews_employer ON interviews (employer_id);
"""

DDL_INTERVIEWS_PROGRESS = """
CREATE TABLE IF NOT EXISTS interviews_progress (
    employer_id   INTEGER PRIMARY KEY,
    employer_name TEXT DEFAULT '',
    total_pages   INTEGER DEFAULT 0,
    next_page     INTEGER DEFAULT 1,
    collected     INTEGER DEFAULT 0,
    failed_pages  INTEGER[] DEFAULT '{}'::int[],
    status        TEXT DEFAULT 'in_progress',
    started_at    TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""

DDL_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    listing_id               BIGINT NOT NULL,
    employer_id              INTEGER NOT NULL,
    employer_name            TEXT DEFAULT '',
    page                     INTEGER DEFAULT 0,
    job_title_text           TEXT,
    normalized_job_title     TEXT,
    location_name            TEXT,
    location_type            TEXT,
    loc_id                   INTEGER,
    job_country_id           INTEGER,
    age_in_days              INTEGER,
    applied                  BOOLEAN,
    easy_apply               BOOLEAN,
    expired                  BOOLEAN,
    is_sponsored_job         BOOLEAN,
    pay_period               TEXT,
    pay_currency             TEXT,
    pay_p10                  REAL,
    pay_p50                  REAL,
    pay_p90                  REAL,
    rating                   REAL,
    salary_source            TEXT,
    goc                      INTEGER,
    job_view_url             TEXT,
    employer_name_from_search TEXT,
    employer_logo_url        TEXT,
    primary_industry_id      INTEGER,
    primary_industry_name    TEXT,
    sector_id                INTEGER,
    sector_name              TEXT,
    collected_at             TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (listing_id, employer_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_employer ON jobs (employer_id);
"""

DDL_JOBS_PROGRESS = """
CREATE TABLE IF NOT EXISTS jobs_progress (
    employer_id   INTEGER PRIMARY KEY,
    employer_name TEXT DEFAULT '',
    total_pages   INTEGER DEFAULT 0,
    next_page     INTEGER DEFAULT 1,
    collected     INTEGER DEFAULT 0,
    failed_pages  INTEGER[] DEFAULT '{}'::int[],
    status        TEXT DEFAULT 'in_progress',
    ctx           JSONB DEFAULT '{}'::jsonb,
    started_at    TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""

ALL_DDL = [
    DDL_EMPLOYERS,
    DDL_DISCOVERY_PROGRESS,
    DDL_REVIEWS,
    DDL_REVIEW_PROGRESS,
    DDL_BENEFITS,
    DDL_BENEFITS_PROGRESS,
    DDL_INTERVIEWS,
    DDL_INTERVIEWS_PROGRESS,
    DDL_JOBS,
    DDL_JOBS_PROGRESS,
]

_inited = False
_init_lock = threading.Lock()


def init_all_tables():
    """首次调用时创建所有表 + 索引。可安全重复调用。"""
    global _inited
    if _inited:
        return
    with _init_lock:
        if _inited:
            return
        conn = get_conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                for ddl in ALL_DDL:
                    cur.execute(ddl)
            log.info("All tables initialized.")
        finally:
            put_conn(conn)
        _inited = True


def main():
    """CLI 入口：建表"""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="初始化 PostgreSQL 数据库表")
    parser.add_argument(
        "--print-ddl", action="store_true",
        help="仅打印 DDL 不执行")
    args = parser.parse_args()

    if args.print_ddl:
        for ddl in ALL_DDL:
            print(ddl.strip())
            print()
        return

    print(f"连接到 {PG_CONFIG['host']}:{PG_CONFIG['port']}/{PG_CONFIG['dbname']}")
    init_all_tables()
    print("建表完成。各模块启动时也会自动建表（幂等）。")
