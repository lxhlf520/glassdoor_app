"""Quick DB check for reviews and progress."""
from glassdoor_collector.db import get_conn, put_conn

conn = get_conn()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM reviews")
        print("reviews total:", cur.fetchone()[0])

        cur.execute("SELECT status, COUNT(*) FROM review_progress GROUP BY status")
        rows = cur.fetchall()
        print("progress by status:", rows)

        cur.execute("SELECT COUNT(*) FROM employers WHERE review_count > 0")
        print("employers with reviews:", cur.fetchone()[0])

        # Show a few employers with highest review_count
        cur.execute("SELECT employer_id, name, review_count FROM employers WHERE review_count > 0 ORDER BY review_count DESC LIMIT 5")
        print("top 5 employers:", cur.fetchall())
finally:
    put_conn(conn)
