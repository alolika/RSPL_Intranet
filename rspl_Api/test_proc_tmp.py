from app.db import get_cursor, rows_to_dicts
with get_cursor() as cur:
    cur.execute("SELECT OBJECT_DEFINITION(OBJECT_ID('webProc_AddCustomer'))")
    print(cur.fetchone()[0])
