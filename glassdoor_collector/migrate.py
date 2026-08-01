"""MongoDB app_employers → PostgreSQL glassdoor 迁移

环境变量配置（见 config.py）:
  MONGO_URI         MongoDB 连接串 (默认 mongodb://localhost:27017)
  PG_HOST           PostgreSQL 主机 (默认 localhost)
  PG_PORT           PostgreSQL 端口 (默认 5432)
  PG_USER           PostgreSQL 用户 (默认 postgres)
  PG_PASSWORD       PostgreSQL 密码 (默认 long123456)
  PG_DBNAME         PostgreSQL 数据库 (默认 glassdoor)
"""
import time

import psycopg2
import psycopg2.extras
from pymongo import MongoClient

from .config import MIGRATE_BATCH_SIZE as BATCH_SIZE, MONGO_URI, PG_CONFIG


def _upsert_batch(cur, batch, conn) -> dict:
    """批量 UPSERT，返回 {inserted, updated}"""
    result = {"inserted": 0, "updated": 0}
    rows = psycopg2.extras.execute_values(
        cur,
        """INSERT INTO employers (
               employer_id, name, short_name, logo_url, overall_rating,
               review_count, salary_count, job_count, discovered_via, discovered_at
           ) VALUES %s
           ON CONFLICT (employer_id) DO UPDATE SET
               name          = EXCLUDED.name,
               short_name    = EXCLUDED.short_name,
               logo_url      = EXCLUDED.logo_url,
               overall_rating= EXCLUDED.overall_rating,
               review_count  = EXCLUDED.review_count,
               salary_count  = EXCLUDED.salary_count,
               job_count     = EXCLUDED.job_count,
               discovered_via= EXCLUDED.discovered_via,
               discovered_at = EXCLUDED.discovered_at
           RETURNING (xmax = 0) AS is_new""",
        batch,
        template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        fetch=True,
    )
    for row in rows:
        if row[0]:
            result["inserted"] += 1
        else:
            result["updated"] += 1
    return result


def main():
    # 1. 连接 PG
    pg_conn = psycopg2.connect(**PG_CONFIG)
    pg_conn.autocommit = False
    cur = pg_conn.cursor()

    # 2. 建库 / 建表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS employers (
            employer_id   INTEGER PRIMARY KEY,
            name          TEXT NOT NULL,
            short_name    TEXT,
            logo_url      TEXT,
            overall_rating REAL,
            review_count  INTEGER DEFAULT 0,
            salary_count  INTEGER DEFAULT 0,
            job_count     INTEGER DEFAULT 0,
            discovered_via TEXT,
            discovered_at TIMESTAMPTZ,
            created_at    TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    pg_conn.commit()
    print("Table 'employers' ready.")

    # 3. 连 MongoDB
    mongo = MongoClient(MONGO_URI)
    col = mongo["glassdoor"]["app_employers"]
    total = col.count_documents({})
    print(f"MongoDB total: {total:,}")

    inserted = 0
    updated = 0
    start = time.time()
    last_log = start

    batch = []
    for doc in col.find({}, batch_size=BATCH_SIZE).sort("employerId", 1):
        batch.append((
            doc["employerId"],
            doc.get("name") or doc.get("shortName", ""),
            doc.get("shortName"),
            doc.get("logoUrl"),
            doc.get("overallRating"),
            doc.get("reviewCount", 0) or 0,
            doc.get("salaryCount", 0) or 0,
            doc.get("jobCount", 0) or 0,
            doc.get("discoveredVia"),
            doc.get("discoveredAt"),
        ))

        if len(batch) >= BATCH_SIZE:
            c = _upsert_batch(cur, batch, pg_conn)
            inserted += c["inserted"]
            updated += c["updated"]
            pg_conn.commit()
            batch.clear()

        now = time.time()
        done = inserted + updated
        if now - last_log > 15 and done > 0:
            pct = done / total * 100
            elapsed = now - start
            rate = done / elapsed
            eta = (total - done) / rate if rate > 0 else 0
            print(f"  {done:,}/{total:,} ({pct:.1f}%)  "
                  f"inserted={inserted:,}  rate={rate:.0f}/s  eta={eta:.0f}s")
            last_log = now

    # 最后一批
    if batch:
        c = _upsert_batch(cur, batch, pg_conn)
        inserted += c["inserted"]
        updated += c["updated"]
        pg_conn.commit()

    elapsed = time.time() - start
    done = inserted + updated
    print(f"\nDone: {done:,} rows in {elapsed:.0f}s")
    print(f"  inserted={inserted:,}  updated={updated:,}")

    # 验证
    cur.execute("SELECT COUNT(*) FROM employers")
    pg_total = cur.fetchone()[0]
    print(f"PG employers: {pg_total:,}")
    assert pg_total == total, f"MISMATCH: mongo={total} pg={pg_total}"
    print("Verification PASSED.")

    cur.execute("""SELECT COUNT(*), MIN(employer_id), MAX(employer_id),
                          AVG(review_count)::INT, SUM(review_count)
                   FROM employers""")
    stats = cur.fetchone()
    print(f"  min_id={stats[1]}  max_id={stats[2]}  "
          f"avg_reviews={stats[3]}  total_reviews={stats[4]:,}")

    cur.close()
    pg_conn.close()
    mongo.close()


if __name__ == "__main__":
    main()
