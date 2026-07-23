"""New "Holiday Master" admin page, added under the Accounts menu group at
the user's request. Backed by a new RSPL_HolidayMaster table (HolidayId PK,
HolidayDate, Day, Event, Enabled) -- created specifically for this feature.
The two pre-existing holiday tables in this DB (HolidayList, PR_HolidayMaster)
are stale relics (no rows past 2021-22) with no admin page maintaining them,
so this is a fresh table rather than reusing either.

Day is always derived server-side from HolidayDate on insert/update rather
than trusted from the client, so it can never drift out of sync with the
actual date. Delete is a soft-delete (Enabled=0), matching this app's
established convention elsewhere (Article, PR_HolidayMaster's own Disable
flag, etc.) instead of physically removing rows.
"""

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/admin/holidays", tags=["admin-holiday"])


class HolidayRow(BaseModel):
    holiday_id: int
    holiday_date: str
    day: str
    event: str
    enabled: bool


class HolidayRequest(BaseModel):
    holiday_date: date
    event: str


class HolidayStatusRequest(BaseModel):
    enabled: bool


class HolidayListTitle(BaseModel):
    name: str


def _row(r: dict) -> HolidayRow:
    return HolidayRow(
        holiday_id=r["HolidayId"],
        holiday_date=r["HolidayDate"].strftime("%Y-%m-%d"),
        day=r["Day"] or "",
        event=r["Event"] or "",
        enabled=bool(r["Enabled"]),
    )


@router.get("", response_model=list[HolidayRow])
def get_holidays() -> list[HolidayRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT HolidayId, HolidayDate, Day, Event, Enabled FROM RSPL_HolidayMaster ORDER BY HolidayDate")
        rows = rows_to_dicts(cursor)
    return [_row(r) for r in rows]


@router.post("", response_model=HolidayRow)
def add_holiday(body: HolidayRequest) -> HolidayRow:
    day_name = body.holiday_date.strftime("%A")
    with get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO RSPL_HolidayMaster (HolidayDate, Day, Event, Enabled) OUTPUT INSERTED.HolidayId "
            "VALUES (?, ?, ?, 1)",
            body.holiday_date, day_name, body.event.strip(),
        )
        new_id = cursor.fetchone()[0]
        cursor.execute("SELECT HolidayId, HolidayDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", new_id)
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
def get_holiday_list_title() -> HolidayListTitle:
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM RSPL_HolidayMasterSettings WHERE Id = 1")
        row = cursor.fetchone()
    return HolidayListTitle(name=row[0] if row else "Holiday List")


@router.put("/title", response_model=HolidayListTitle)
def set_holiday_list_title(body: HolidayListTitle) -> HolidayListTitle:
    name = body.name.strip() or "Holiday List"
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_HolidayMasterSettings SET Name = ? WHERE Id = 1; "
            "IF @@ROWCOUNT = 0 INSERT INTO RSPL_HolidayMasterSettings (Id, Name) VALUES (1, ?)",
            name, name,
        )
    return HolidayListTitle(name=name)


@router.put("/{holiday_id}", response_model=HolidayRow)
def update_holiday(holiday_id: int, body: HolidayRequest) -> HolidayRow:
    day_name = body.holiday_date.strftime("%A")
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_HolidayMaster SET HolidayDate = ?, Day = ?, Event = ? WHERE HolidayId = ?",
            body.holiday_date, day_name, body.event.strip(), holiday_id,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
        cursor.execute("SELECT HolidayId, HolidayDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", holiday_id)
        row = rows_to_dicts(cursor)[0]
    return _row(row)


@router.delete("/{holiday_id}")
def delete_holiday(holiday_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("UPDATE RSPL_HolidayMaster SET Enabled = 0 WHERE HolidayId = ?", holiday_id)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
    return {"success": True}


# Grid-level Active/Inactive toggle -- lets a holiday deactivated via the
# delete button (or here) be switched back on again, since delete_holiday
# above is one-way (Enabled 1 -> 0 only).
@router.put("/{holiday_id}/status", response_model=HolidayRow)
def set_holiday_status(holiday_id: int, body: HolidayStatusRequest) -> HolidayRow:
    with get_cursor() as cursor:
        cursor.execute("UPDATE RSPL_HolidayMaster SET Enabled = ? WHERE HolidayId = ?", body.enabled, holiday_id)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
        cursor.execute("SELECT HolidayId, HolidayDate, Day, Event, Enabled FROM RSPL_HolidayMaster WHERE HolidayId = ?", holiday_id)
        row = rows_to_dicts(cursor)[0]
    return _row(row)
