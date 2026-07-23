"""Mirrors Section_General/MyCaller.aspx, backed by
App_Code/VB/MyCallerHistory.vb's GetLatLongMYCallerHistory (class WebService,
despite the misleading MyCallerHistory.asmx CodeBehind path — same stale-path
pattern as Org_ChartWebservice.vb).
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/general/my-caller", tags=["general-my-caller"])

_DEFAULT_LAT = 18.4911754
_DEFAULT_LNG = 73.8566163


class LookupOption(BaseModel):
    label: str
    value: str


class CallLocation(BaseModel):
    username: str
    latitude: float
    longitude: float
    call_type: bool
    html_table: str


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    # Confirmed live: 248 of 271 users have a blank/NULL IMEINo, and a
    # handful of RSPL_CallHistory rows also have a blank IMEINumber — the
    # plain equality join was matching every blank-IMEI user against those
    # blank-IMEI call rows (''=''), which inflated this list to 164 "users
    # with call history" when only ~23 users actually have a real device
    # IMEI on file. 163 of those 164 were false matches with zero real
    # connection to any call. Requiring a non-blank IMEINo on both sides
    # keeps this to genuine matches only.
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT um.Name AS UserName FROM UserMaster um "
            "INNER JOIN RSPL_CallHistory rch ON um.IMEINo = rch.IMEINumber "
            "WHERE um.IMEINo IS NOT NULL AND um.IMEINo <> '' AND rch.IMEINumber IS NOT NULL AND rch.IMEINumber <> ''"
        )
        return [LookupOption(label=r["UserName"], value=r["UserName"]) for r in rows_to_dicts(cursor)]


def _coord(value: str | None, default: float) -> float:
    try:
        parsed = float(value) if value else 0.0
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@router.get("/call-locations", response_model=list[CallLocation])
def get_call_locations(
    user_name: str, call_count: int, call_date: date, show_all: bool = False, call_type: str = "0"
) -> list[CallLocation]:
    # Same blank-IMEI false-match issue as get_users() above — requiring a
    # non-blank IMEINo on both sides of the join so calls only ever get
    # attributed to the user whose real device made them, not to an
    # arbitrary user who simply has no IMEI on file.
    query = f"""
        SELECT TOP {int(call_count)} rch.MobileNo, rch.Latitude, rch.Longitude, rch.CallTypeID,
               um.Name AS UserName, um.UserID
        FROM UserMaster um
        INNER JOIN RSPL_CallHistory rch ON rch.IMEINumber = um.IMEINo
        WHERE CAST(rch.CreationDate AS date) = ?
          AND um.IMEINo IS NOT NULL AND um.IMEINo <> ''
          AND rch.IMEINumber IS NOT NULL AND rch.IMEINumber <> ''
    """
    params: list = [call_date]
    if user_name and user_name.lower() not in ("none", "all"):
        query += " AND um.Name = ?"
        params.append(user_name)
    # Was accepted by the frontend's Call Type dropdown (All/Incoming/
    # Outgoing) but never actually sent to this endpoint, and this endpoint
    # never had a parameter for it either — the filter had zero effect.
    # CallTypeID is a real 2-state column (confirmed live: only 0/1 exist);
    # the frontend's own value scheme is offset by one (1=Incoming,
    # 2=Outgoing) to leave room for "0=All" as a UI-only sentinel.
    if call_type == "1":
        query += " AND rch.CallTypeID = 0"
    elif call_type == "2":
        query += " AND rch.CallTypeID = 1"
    query += " ORDER BY rch.CreationTime DESC"

    results: list[CallLocation] = []
    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

        for r in rows:
            lat = _coord(r["Latitude"], _DEFAULT_LAT)
            lng = _coord(r["Longitude"], _DEFAULT_LNG)
            call_type = bool(r["CallTypeID"])
            html_table = ""

            # webproc_GetCustOutstandingByMobile references [License].[License_Retailware]
            # via a linked server not configured on this DB instance (same gap as
            # Section_GST) — this is enrichment only, so a failure here shouldn't
            # break the whole call-locations response.
            try:
                if show_all:
                    cursor.execute(
                        "EXEC Proc_GetCallerLog ?, ?, ?, ?, ?, ?",
                        call_count,
                        call_date.strftime("%d-%b-%Y"),
                        r["UserID"],
                        r["CallTypeID"],
                        r["Latitude"] or "0.0",
                        r["Longitude"] or "0.0",
                    )
                    log_rows = rows_to_dicts(cursor)
                    if log_rows:
                        html_table = log_rows[0].get("HTMLSubject") or ""
                elif r["MobileNo"]:
                    cursor.execute("EXEC webproc_GetCustOutstandingByMobile ?", r["MobileNo"])
                    field_rows = rows_to_dicts(cursor)
                    html_table = " | ".join(f["FieldText"] for f in field_rows if f.get("FieldText"))
            except Exception:
                html_table = ""

            results.append(
                CallLocation(
                    username=r["UserName"] or "",
                    latitude=lat,
                    longitude=lng,
                    call_type=call_type,
                    html_table=html_table,
                )
            )

    return results
