"""Quick DB stats."""
from glassdoor_collector.db import get_conn, put_conn

c = get_conn()
cur = c.cursor()

cur.execute("SELECT COUNT(*) FROM benefits")
print(f"benefits docs: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT employer_id), COUNT(DISTINCT country_id) FROM benefits")
r = cur.fetchone()
print(f"unique employers: {r[0]}, unique countries: {r[1]}")

cur.execute("SELECT country_id, COUNT(*) c FROM benefits WHERE type='review' GROUP BY 1 ORDER BY c DESC LIMIT 10")
print("top countries:")
for row in cur.fetchall():
    print(f"  country_id={row[0]} count={row[1]}")

cur.execute("SELECT employer_id, country_id, COUNT(*) c FROM benefits WHERE type='review' GROUP BY 1,2 ORDER BY c DESC LIMIT 5")
print("top (employer, country) pairs:")
for row in cur.fetchall():
    print(f"  eid={row[0]} cid={row[1]} count={row[2]}")

cur.execute("SELECT status, COUNT(*) FROM benefits_progress GROUP BY 1")
print("progress statuses:")
for row in cur.fetchall():
    print(f"  {row}")

put_conn(c)
