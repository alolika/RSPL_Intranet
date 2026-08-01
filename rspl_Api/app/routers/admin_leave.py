"""Mirrors Section_Admin/LeaveAppRegister.aspx.vb (month-calendar view — the
source's gvLeave RadGrid DataSource assignment is commented out, so only
the calendar's CalDetails_DayRender query matters) and
LeaveAppRegister1.aspx.vb (filterable grid variant, same web_leaveapplication
table).
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/admin/leave", tags=["admin-leave"])


class LookupOption(BaseModel):
    label: str
    value: int


class LeaveAppRow(BaseModel):
    leave_id: int
    user_id: int
    name: str
    app_date: str
    from_date: str
    to_date: str
    for_days: str
    reason: str
    ho_sanctioned: bool
    ho_sanctioned_date: str | None
    ceo_sanctioned: bool
    ceo_sanctioned_date: str | None
    cancelled: bool
    article_id: int


def _row(r: dict) -> LeaveAppRow:
    return LeaveAppRow(
        leave_id=r["LeaveID"], user_id=r["UserID"], name=r["Name"] or "",
        app_date=r["AppDate"].isoformat() if r["AppDate"] else "",
        from_date=r["FromDate"].isoformat() if r["FromDate"] else "",
        to_date=r["ToDate"].isoformat() if r["ToDate"] else "",
        for_days=str(r["ForDays"] or ""), reason=r["Reason"] or "",
        ho_sanctioned=bool(r["HoSanctioned"]),
        ho_sanctioned_date=r["HOSanctioneddate"].isoformat() if r["HoSanctioned"] and r["HOSanctioneddate"] else None,
        ceo_sanctioned=bool(r["CEOSanctioned"]),
        ceo_sanctioned_date=r["CEOSanctiondate"].isoformat() if r["CEOSanctioned"] and r["CEOSanctiondate"] else None,
        cancelled=bool(r["CancelledByApplicant"] or r["CancelledbyHO"]), article_id=r["ArticleID"] or 0,
    )


@router.get("/rows", response_model=list[LeaveAppRow])
def get_leave_app_rows(from_date: date | None = None, to_date: date | None = None) -> list[LeaveAppRow]:
    # This feeds the calendar view (leave-app-register.ts), which renders one
    # cell per day of the viewed month and needs "every leave whose FromDate/
    # ToDate range touches this month" — it was filtering on AppDate (when the
    # application was SUBMITTED) instead, which is a different date entirely.
    # Confirmed live: 2,735 of 16,677 rows (16%) have an AppDate month that
    # differs from their FromDate month (applied ahead of time for a future
    # month), so viewing e.g. August's calendar silently missed every leave
    # that was actually dated in August but applied for in July. Fixed to an
    # overlap check against FromDate/ToDate — the leave's own dates — instead.
    # FromDate/ToDate are datetime columns but confirmed always stored at
    # midnight; the exclusive DATEADD upper bound is kept anyway as a safe
    # habit rather than assuming that never changes.
    where = ""
    params: list = []
    if from_date and to_date:
        where = " WHERE La.FromDate < DATEADD(day, 1, CAST(? AS DATE)) AND La.ToDate >= CAST(? AS DATE)"
        params = [to_date, from_date]
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT La.LeaveID, La.UserID, Um.Name, La.AppDate, La.FromDate, La.ToDate, La.ForDays, La.Reason, "
            f"La.HoSanctioned, La.HOSanctioneddate, La.CEOSanctioned, La.CEOSanctiondate, La.CancelledByApplicant, "
            f"La.CancelledbyHO, La.ArticleID FROM web_leaveapplication La INNER JOIN Usermaster um ON la.userid = um.userid"
            f"{where} ORDER BY Um.Name, La.leaveid DESC",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [_row(r) for r in rows]


@router.get("/register1-rows", response_model=list[LeaveAppRow])
def get_leave_app_register1_rows(
    from_date: date | None = None, to_date: date | None = None, user_id: int = 0,
    include_subordinate: bool = False, sanction_filter: str = "ALL",
    current_user: CurrentUser = Depends(get_current_user),
) -> list[LeaveAppRow]:
    where = ""
    params: list = []
    if from_date and to_date:
        # Was "FromDate >= start AND ToDate <= end" — requires the ENTIRE
        # leave to fall inside the selected window, so any leave that starts
        # before or ends after it (crossing into the next/previous month)
        # vanished from the report entirely. Confirmed live: 227 real leaves
        # span a month boundary and were invisible in either month's filtered
        # view for exactly this reason. Fixed to a proper overlap check —
        # show a leave if any part of it touches the selected window.
        where += " AND La.Fromdate < DATEADD(day, 1, CAST(? AS DATE)) AND La.Todate >= CAST(? AS DATE)"
        params += [to_date, from_date]
    if include_subordinate:
        where += " AND (um.userid = ? OR um.ParentEmpID = ?)"
        params += [user_id, current_user.user_id]
    elif user_id:
        where += " AND um.userid = ?"
        params.append(user_id)
    if sanction_filter == "Sanctioned":
        where += " AND (La.CEOSanctioned = 1 OR La.HoSanctioned = 1)"
    elif sanction_filter == "Pending":
        where += " AND (La.CEOsanctioned = 0 AND La.HoSanctioned = 0)"

    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT La.LeaveID, La.UserID, Um.Name, La.AppDate, La.FromDate, La.ToDate, La.ForDays, La.Reason, "
            f"La.HoSanctioned, La.HOSanctioneddate, La.CEOSanctioned, La.CEOSanctiondate, La.CancelledByApplicant, "
            f"La.CancelledbyHO, La.ArticleID FROM web_leaveapplication La INNER JOIN Usermaster um ON la.userid = um.userid "
            f"WHERE 1 = 1{where} ORDER BY La.leaveid DESC",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [_row(r) for r in rows]


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]
