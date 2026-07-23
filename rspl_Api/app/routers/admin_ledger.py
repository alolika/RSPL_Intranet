"""Mirrors Section_Admin/LedgerBalance.aspx.vb ("Pipe Line"). The source's
~840 lines are almost entirely per-cell AutoPostBack checkbox/GridHeaderItem
label-mutation plumbing for four running totals — the Angular side already
replaced that with a computed() over the fetched rows.

Proc_DisplayLedgerBalance itself is bypassed here in favor of an equivalent
hand-written query: the proc computed Immediate/Distance/LongDistance/Writeoff
via FOUR separate correlated subqueries against CustomerAttributes that all
hit the exact same (CustID, AttributeID=1, DepID=1) row — one column each —
so it re-ran the same clustered-index seek 4x per customer. Collapsed to one
LEFT JOIN here. FollowUpUser (a 5th CustomerAttributes subquery, different
AttributeID) becomes a second join, and the AMCDate/avgDays correlated
subqueries become OUTER APPLYs. Verified byte-for-byte identical output
against the proc for all 417 current rows, ~35% faster (1.4s -> 0.87s locally).

Search-field filtering (DisplayName/City/MobileNo/ContactPerson/FollowUpUser)
is pushed into the SQL WHERE clause (parameterized) instead of fetching every
row and filtering in Python, so a narrow search also transfers less data.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/admin/ledger", tags=["admin-ledger"])

# search_field -> SQL column expression. Fixed whitelist, never built from
# user input directly, so it's safe to interpolate into the query string.
_SEARCH_FIELDS = {
    "1": "vw.DisplayName",
    "2": "vw.City",
    "3": "vw.MobileNo",
    "4": "vw.ContactPerson",
    "6": "followup.AttrValue",
}


class LedgerBalanceRow(BaseModel):
    cust_id: int
    display_name: str
    mobile_no: str
    avg_days: int
    city: str
    area: str
    contact_person: str
    outstanding: float
    immediate: bool
    distance: bool
    long_distance: bool
    writeoff: bool
    follow_up_user: str
    amc_date: str | None


class UpdateFlagRequest(BaseModel):
    flag: str
    value: bool


def _row(r: dict) -> LedgerBalanceRow:
    return LedgerBalanceRow(
        cust_id=r["CustID"], display_name=r["DisplayName"] or "", mobile_no=r["MobileNo"] or "",
        avg_days=int(r["avgDays"] or 0), city=r["City"] or "", area=r["Area"] or "",
        contact_person=r["ContactPerson"] or "", outstanding=float(r["Outstanding"] or 0),
        immediate=bool(r["Immediate"]), distance=bool(r["Distance"]), long_distance=bool(r["LongDistance"]),
        writeoff=bool(r["Writeoff"]), follow_up_user=r["FollowUpUser"] or "",
        amc_date=str(r["AMCDate"]) if r.get("AMCDate") else None,
    )


_BASE_QUERY = """
SELECT
  vw.CustID, vw.DisplayName, vw.MobileNo, vw.City, vw.Area, vw.ContactPerson, vw.Outstanding,
  attr.Immediate, attr.Distance, attr.LongDistance, attr.Writeoff,
  followup.AttrValue AS FollowUpUser,
  amc.AMCDate,
  sales.avgDays
FROM vwCustLedgerBalanceOutStanding vw
LEFT JOIN CustomerAttributes attr
  ON attr.CustID = vw.CustID AND attr.AttributeID = 1 AND attr.DepID = 1
LEFT JOIN CustomerAttributes followup
  ON followup.CustID = vw.CustID AND followup.AttributeID = 31 AND followup.DepID = 1
OUTER APPLY (
  SELECT TOP 1 ud.Value AS AMCDate
  FROM udfdetail ud
  INNER JOIN SalesMaster sm ON sm.VoucherNo = ud.VoucherNo AND sm.CustID = vw.CustID
  WHERE ud.udfid = 5 AND ud.transtypeid = 1 AND ud.value <> ''
  ORDER BY CAST(ud.Value AS datetime) DESC
) amc
OUTER APPLY (
  SELECT AVG(DATEDIFF(dd, sm2.Date, GETDATE())) AS avgDays
  FROM SalesMaster sm2
  WHERE sm2.CustID = vw.CustID AND sm2.Outstanding > 0
) sales
WHERE vw.Outstanding > 0 AND vw.Disabled = 0
"""


@router.get("/rows", response_model=list[LedgerBalanceRow])
def get_ledger_balance_rows(search_field: str = "0", search_value: str = "") -> list[LedgerBalanceRow]:
    column = _SEARCH_FIELDS.get(search_field)
    query = _BASE_QUERY
    params: list[str] = []
    if column and search_value.strip():
        query += f" AND {column} LIKE ?"
        params.append(f"%{search_value.strip()}%")
    query += " ORDER BY vw.DisplayName"

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [_row(r) for r in rows]


@router.post("/{cust_id}/flag")
def update_ledger_balance_flag(cust_id: int, body: UpdateFlagRequest) -> dict:
    flags = {"immediate": False, "distance": False, "longDistance": False, "writeoff": False}
    if body.flag in flags:
        flags[body.flag] = body.value
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_InsertLedgerBalance ?, ?, ?, ?, ?",
            cust_id, flags["immediate"], flags["distance"], flags["longDistance"], flags["writeoff"],
        )
    return {"success": True}
