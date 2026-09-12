from app.db import get_cursor, rows_to_dicts
with get_cursor() as cur:
    cur.execute("SELECT OBJECT_DEFINITION(OBJECT_ID('Webproc_ViewEnquiry'))")
    print(cur.fetchone()[0])
