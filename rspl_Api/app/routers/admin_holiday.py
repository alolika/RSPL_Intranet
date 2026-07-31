"""New "Holiday Master" admin page, added under the Accounts menu group at
the user's request. Backed by a new RSPL_HolidayMaster table (HolidayId PK,
HolidayDate, ToDate, Day, Event, Enabled) -- created specifically for this
feature. The two pre-existing holiday tables in this DB (HolidayList,
PR_HolidayMaster) are stale relics (no rows past 2021-22) with no admin page
maintaining them, so this is a fresh table rather than reusing either.

HolidayDate/ToDate is a From/To date RANGE per holiday entry (added later,
per explicit request, to support multi-day holidays like a Diwali break as
one entry instead of one row per day) -- HolidayDate is the "From Date",
ToDate the "To Date". Every pre-existing single-day row was backfilled with
ToDate = HolidayDate, so a single-day holiday is just a range where both
ends are the same date; the frontend grid only shows a "From - To" range
when they differ, a single date otherwise. Confirmed no other consumer of
this table does exact-date-equality lookups against HolidayDate (only
general_dashboards.py's holiday-list tile, which just lists/orders rows) --
if a "is this specific date on my calendar a holiday" check is ever added
elsewhere, it must range-check BETWEEN HolidayDate AND ToDate, not equality.

Day is always derived server-side from HolidayDate (the From Date) on
insert/update rather than trusted from the client, so it can never drift
out of sync with the actual date. Delete is a soft-delete (Enabled=0),
matching this app's established convention elsewhere (Article,
PR_HolidayMaster's own Disable flag, etc.) instead of physically removing
rows.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.rights import FORM_HOLIDAY_MASTER, require_access

router = APIRouter(prefix="/admin/holidays", tags=["admin-holiday"])


class HolidayRow(BaseModel):
    holiday_id: int
    holiday_date: str
    to_date: str
    day: str
    event: str
    enabled: bool


class HolidayRequest(BaseModel):
    holiday_date: date
    to_date: date
    event: str


class HolidayStatusRequest(BaseModel):
    enabled: bool


class HolidayListTitle(BaseModel):
    name: str


def _row(r: dict) -> HolidayRow:
    return HolidayRow(
        holiday_id=r["HolidayId"],
        holiday_date=r["HolidayDate"].strftime("%Y-%m-%d"),
        to_date=r["ToDate"].strftime("%Y-%m-%d"),
        day=r["Day"] or "",
        event=r["Event"] or "",
        enabled=bool(r["Enabled"]),
    )


def _validate_range(body: HolidayRequest) -> None:
    if body.to_date < body.holiday_date:
        raise HTTPException(status_code=400, detail="To Date cannot be earlier than From Date")


@router.get("", response_model=list[HolidayRow])
def get_holidays(current_user: CurrentUser = Depends(get_current_user)) -> list[HolidayRow]:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "view")
    with get_cursor() as cursor:
        cursor.execute("SELECT HolidayId, HolidayDate, ToDate, Day, Event, Enabled FROM RSPL_HolidayMaster ORDER BY HolidayDate")
        rows = rows_to_dicts(cursor)
    return [_row(r) for r in rows]


@router.post("", response_model=HolidayRow)
def add_holiday(body: HolidayRequest, current_user: CurrentUser = Depends(get_current_user)) -> HolidayRow:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "edit")
    _validate_range(body)
    day_name = body.holiday_date.strftime("%A")
    with get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO RSPL_HolidayMaster (HolidayDate, ToDate, Day, Event, Enabled) OUTPUT INSERTED.HolidayId "
            "VALUES (?, ?, ?, ?, 1)",
            body.holiday_date, body.to_date, day_name, body.event.strip(),
        )
        new_id = cursor.fetchone()[0]
        cursor.execute("SELECT HolidayId, HolidayDate, ToDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", new_id)
        row = rows_to_dicts(cursor)[0]
    return _row(row)


# The dashboard's "Holiday List 24-25" card title was hardcoded in the
# Angular component. Replaced with this single-row settings table
# (RSPL_HolidayMasterSettings, seeded with the previous hardcoded text) so
# it's editable from this same admin page instead of requiring a code change
# every holiday year. Registered before the "/{holiday_id}" routes below --
# FastAPI/Starlette matches routes in registration order and does not use a
# path param's Python type hint (int) as a URL-matching constraint, so
# "/title" must be declared first or it gets swallowed by "/{holiday_id}"
# (confirmed live: PUT /title 422'd trying to parse "title" as an int until
# this was reordered).
@router.get("/title", response_model=HolidayListTitle)
def get_holiday_list_title(current_user: CurrentUser = Depends(get_current_user)) -> HolidayListTitle:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "view")
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM RSPL_HolidayMasterSettings WHERE Id = 1")
        row = cursor.fetchone()
    return HolidayListTitle(name=row[0] if row else "Holiday List")


@router.put("/title", response_model=HolidayListTitle)
def set_holiday_list_title(body: HolidayListTitle, current_user: CurrentUser = Depends(get_current_user)) -> HolidayListTitle:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "edit")
    name = body.name.strip() or "Holiday List"
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_HolidayMasterSettings SET Name = ? WHERE Id = 1; "
            "IF @@ROWCOUNT = 0 INSERT INTO RSPL_HolidayMasterSettings (Id, Name) VALUES (1, ?)",
            name, name,
        )
    return HolidayListTitle(name=name)


@router.put("/{holiday_id}", response_model=HolidayRow)
def update_holiday(holiday_id: int, body: HolidayRequest, current_user: CurrentUser = Depends(get_current_user)) -> HolidayRow:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "edit")
    _validate_range(body)
    day_name = body.holiday_date.strftime("%A")
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_HolidayMaster SET HolidayDate = ?, ToDate = ?, Day = ?, Event = ? WHERE HolidayId = ?",
            body.holiday_date, body.to_date, day_name, body.event.strip(), holiday_id,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
        cursor.execute("SELECT HolidayId, HolidayDate, ToDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", holiday_id)
        row = rows_to_dicts(cursor)[0]
    return _row(row)


@router.delete("/{holiday_id}")
def delete_holiday(holiday_id: int, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "edit")
    with get_cursor() as cursor:
        cursor.execute("UPDATE RSPL_HolidayMaster SET Enabled = 0 WHERE HolidayId = ?", holiday_id)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
    return {"success": True}


# Grid-level Active/Inactive toggle -- lets a holiday deactivated via the
# delete button (or here) be switched back on again, since delete_holiday
# above is one-way (Enabled 1 -> 0 only).
@router.put("/{holiday_id}/status", response_model=HolidayRow)
def set_holiday_status(holiday_id: int, body: HolidayStatusRequest, current_user: CurrentUser = Depends(get_current_user)) -> HolidayRow:
    require_access(current_user.user_id, FORM_HOLIDAY_MASTER, "edit")
    with get_cursor() as cursor:
        cursor.execute("UPDATE RSPL_HolidayMaster SET Enabled = ? WHERE HolidayId = ?", body.enabled, holiday_id)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
        cursor.execute("SELECT HolidayId, HolidayDate, ToDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", holiday_id)
        row = rows_to_dicts(cursor)[0]
    return _row(row)
