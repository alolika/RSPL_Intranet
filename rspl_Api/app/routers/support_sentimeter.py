"""Customer Sentimeter — keyword frequency and customer-mood bubble chart.

Reads dbo.call_summaries (Worldnettech DB) — same connection as the rest of
the app. Ported from the standalone sentimeter_local.py PoC.

Endpoints
---------
GET /support/sentimeter/options   — distinct software_name + customer_mood lists
GET /support/sentimeter/data      — filtered rows (up to 10 000)
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/support", tags=["support-sentimeter"])

TABLE = "dbo.call_summaries"


@router.get("/sentimeter/options")
def get_sentimeter_options(_cu: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT software_name FROM {TABLE} "
            "WHERE software_name IS NOT NULL AND LTRIM(RTRIM(software_name)) <> '' "
            "ORDER BY software_name"
        )
        software = [str(r[0]) for r in cursor.fetchall()]

        cursor.execute(
            f"SELECT DISTINCT customer_mood FROM {TABLE} "
            "WHERE customer_mood IS NOT NULL AND LTRIM(RTRIM(customer_mood)) <> '' "
            "ORDER BY customer_mood"
        )
        moods = [str(r[0]) for r in cursor.fetchall()]

    return {"software": software, "moods": moods}


@router.get("/sentimeter/data")
def get_sentimeter_data(
    date_from: str | None = None,
    date_to: str | None = None,
    customer: str | None = None,
    executive: str | None = None,
    software: str | None = None,
    mood: str | None = None,
    keyword: str | None = None,
    _cu: CurrentUser = Depends(get_current_user),
) -> dict:
    clauses: list[str] = []
    params: list = []

    if date_from:
        clauses.append("CAST(call_date AS DATE) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("CAST(call_date AS DATE) <= ?")
        params.append(date_to)
    if customer and customer.strip():
        clauses.append("customer_name LIKE ?")
        params.append(f"%{customer.strip()}%")
    if executive and executive.strip():
        clauses.append("executive_name LIKE ?")
        params.append(f"%{executive.strip()}%")
    if software and software.strip():
        clauses.append("software_name LIKE ?")
        params.append(f"%{software.strip()}%")
    if mood and mood.strip():
        clauses.append("customer_mood LIKE ?")
        params.append(f"%{mood.strip()}%")
    if keyword and keyword.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM STRING_SPLIT(ISNULL(keywords, ''), ',') s "
            "WHERE LTRIM(RTRIM(s.value)) LIKE ?)"
        )
        params.append(f"%{keyword.strip()}%")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT TOP (10000) id, call_date, executive_name, customer_name, "
        f"software_name, customer_mood, keywords, call_duration, mood_reason "
        f"FROM {TABLE}{where} ORDER BY call_date DESC"
    )

    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        raw = rows_to_dicts(cursor, limit=10000)

    rows = []
    for r in raw:
        cd = r["call_date"]
        if isinstance(cd, (datetime, date)):
            cd = cd.isoformat()
        rows.append({
            "id": r["id"],
            "call_date": cd,
            "executive_name": r["executive_name"],
            "customer_name": r["customer_name"],
            "software_name": r["software_name"],
            "customer_mood": r["customer_mood"],
            "keywords": r["keywords"],
            "call_duration": r["call_duration"],
            "mood_reason": r["mood_reason"],
        })

    return {"rows": rows}
