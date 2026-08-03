"""New "Face In/Out" admin page (Accounts menu) — records a face-recognition
attendance device's per-day In/Break/Resume/Out punch times per person.
Backed by a new FaceInOut table (hand-created directly on the SQL Server
instance, confirmed live — no migration tooling exists in this repo):

- PK (FaceId, [Date]) — a composite natural key, not a surrogate identity
  column, matching this app's own convention elsewhere (RSPL_ExecScheduleCell,
  RSPL_ExecScheduleHolidayRule) — one row per person per day, and it's what
  makes "prevent duplicate records" enforceable directly by the database
  rather than by an extra application-level check.
- WeekDay is always derived server-side from Date (never trusted from the
  client/import file), so it can never drift out of sync with the actual
  date — same convention as admin_holiday.py's Day column.
- In/Break/Resume/Out are plain free-text (nvarchar) punch-time strings as
  the device/import file reports them (e.g. "09:15 AM") — not parsed/
  validated as a strict time type, since the device's own export format
  isn't specified and rejecting a legitimate-but-unusual value would be
  worse than storing it as-is.
- CreationUserID/CreationDate are audit-only, matching every other table
  added in this app; they're not part of the 8 functional columns the
  search/grid/import/export features operate on.

Excel import is parsed CLIENT-SIDE (mirrors executive-schedule.ts's own
Import from Excel — this app's established convention: the browser reads
the .xlsx file with the `xlsx` package and posts already-parsed JSON rows,
rather than uploading the raw file for the server to parse). This endpoint
only ever receives plain JSON, never a file.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/admin/face-inout", tags=["admin-face-inout"])

# %d-%m-%Y added per the real device-export format confirmed at
# D:\Maruti\format.xlsx — Date is stored there as plain text (not a real
# Excel date cell, data_type='s', number_format='@') like "01-03-2026", i.e.
# day-month-year with dashes, which none of the other accepted formats match.
_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y")


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class FaceInOutRow(BaseModel):
    face_id: int
    name: str
    date: str
    week_day: str
    in_time: str
    break_time: str
    resume_time: str
    out_time: str


# A device export accumulates roughly one row per person per day — thousands
# a year — so an unbounded "no filters = every row ever" search grows
# without limit as the table ages. This caps any single search response
# (filtered or not) at a safe size and tells the caller if more rows existed
# than were returned, rather than silently truncating.
_MAX_SEARCH_ROWS = 5000


class FaceInOutSearchResponse(BaseModel):
    rows: list[FaceInOutRow]
    capped: bool


class ImportRow(BaseModel):
    face_id: int | str
    name: str
    date: str
    in_time: str = ""
    break_time: str = ""
    resume_time: str = ""
    out_time: str = ""


class ImportRequest(BaseModel):
    rows: list[ImportRow]


class ImportResultRow(BaseModel):
    row_number: int
    reason: str


class ImportResponse(BaseModel):
    imported_count: int
    skipped: list[ImportResultRow]


def _row(r: dict) -> FaceInOutRow:
    return FaceInOutRow(
        face_id=r["FaceId"], name=r["Name"] or "",
        date=r["Date"].isoformat() if r["Date"] else "",
        week_day=r["WeekDay"] or "",
        in_time=r["In"] or "", break_time=r["Break"] or "",
        resume_time=r["Resume"] or "", out_time=r["Out"] or "",
    )


@router.get("", response_model=FaceInOutSearchResponse)
def get_face_inout_rows(
    name: str = "", date_from: date | None = None, date_to: date | None = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> FaceInOutSearchResponse:
    where = ""
    params: list = []
    if name.strip():
        where += " AND Name LIKE ?"
        params.append(f"%{name.strip()}%")
    if date_from:
        where += " AND [Date] >= ?"
        params.append(date_from)
    if date_to:
        where += " AND [Date] <= ?"
        params.append(date_to)
    with get_cursor() as cursor:
        # Fetches one row beyond the cap purely to detect whether there WAS
        # more (capped=True) without a separate COUNT(*) query.
        cursor.execute(
            f"SELECT TOP {_MAX_SEARCH_ROWS + 1} FaceId, Name, [Date], WeekDay, [In], [Break], Resume, [Out] FROM FaceInOut "
            f"WHERE 1 = 1{where} ORDER BY [Date] DESC, Name",
            *params,
        )
        rows = rows_to_dicts(cursor)
    capped = len(rows) > _MAX_SEARCH_ROWS
    return FaceInOutSearchResponse(rows=[_row(r) for r in rows[:_MAX_SEARCH_ROWS]], capped=capped)


@router.post("/import", response_model=ImportResponse)
def import_face_inout_rows(body: ImportRequest, current_user: CurrentUser = Depends(get_current_user)) -> ImportResponse:
    skipped: list[ImportResultRow] = []
    imported = 0
    seen_in_batch: set[tuple[int, date]] = set()

    with get_cursor() as cursor:
        for index, row in enumerate(body.rows, start=1):
            try:
                face_id = int(row.face_id)
            except (TypeError, ValueError):
                skipped.append(ImportResultRow(row_number=index, reason="Invalid FaceId"))
                continue
            name = row.name.strip()
            if not name:
                skipped.append(ImportResultRow(row_number=index, reason="Missing Name"))
                continue
            parsed_date = _parse_date(row.date)
            if parsed_date is None:
                skipped.append(ImportResultRow(row_number=index, reason=f"Invalid Date '{row.date}'"))
                continue

            key = (face_id, parsed_date)
            if key in seen_in_batch:
                skipped.append(ImportResultRow(row_number=index, reason="Duplicate FaceId+Date within this file"))
                continue

            cursor.execute("SELECT 1 FROM FaceInOut WHERE FaceId = ? AND [Date] = ?", face_id, parsed_date)
            if cursor.fetchone():
                skipped.append(ImportResultRow(row_number=index, reason="A record for this FaceId and Date already exists"))
                continue

            week_day = parsed_date.strftime("%A")
            cursor.execute(
                "INSERT INTO FaceInOut (FaceId, Name, [Date], WeekDay, [In], [Break], Resume, [Out], CreationUserID, CreationDate) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIME())",
                face_id, name, parsed_date, week_day,
                row.in_time.strip(), row.break_time.strip(), row.resume_time.strip(), row.out_time.strip(),
                current_user.user_id,
            )
            seen_in_batch.add(key)
            imported += 1

    return ImportResponse(imported_count=imported, skipped=skipped)
