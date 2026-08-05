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

Update (2026-08-04): extended for a separate Nagar Team, per explicit
request — a new FaceInOut_Nagar table (hand-created the same way as
FaceInOut), mirroring the real device-export shape confirmed live from
D:\\Maruti\\ATT.xls (33 columns via xlrd — openpyxl can't read legacy .xls —
Employee ID/Name/Date/In/Break/Resume/Out plus ~25 richer attendance-system
columns like Work/Overtime/sumhr/leavetype/Remark/reason_*). Column names
are bracket-quoted to preserve the Excel header text exactly where it isn't
a valid bare SQL identifier ([No.], [Employee ID], [Card ID], [Day Type],
[Diff.OT], [Short Minutes]) — same "same column names as the source" intent
as everywhere else in this app that mirrors an external shape 1:1. PK is
([Employee ID], [Date]), same composite-natural-key convention as FaceInOut
itself; confirmed live that (Employee ID, Date) is genuinely unique across
all 528 rows of the reference file, and every Employee ID is a clean integer
once trimmed — so it reuses the exact same `face_id`/FaceId naming as the
Pune table rather than inventing a parallel "employee_id" concept.

Both search (GET) and import (POST) below now take a `team` parameter
("pune" | "nagar", defaulting to "pune" for backward compatibility with the
existing frontend) and branch which table they read/write — Pune's own
logic, table, and response shape are completely untouched. The search
response stays the same compact FaceInOutRow shape (FaceId/Name/Date/
WeekDay/In/Break/Resume/Out) for BOTH teams, so the existing grid/export/
filter UI works unchanged for Nagar too — Nagar's ~25 extra columns are
captured in the database (per the explicit "same structure as the Excel
sheet" requirement) but aren't surfaced in this shared grid, since nothing
in the request asked for 25 extra visible columns. ImportRow grew one new
optional `extra: dict[str, str]` field (Excel header name -> raw string
value) carrying those extra columns for Nagar imports only; Pune imports
never populate it. Weekday is still always server-derived from Date for
both teams (never trusted from the file), same reasoning as before.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/admin/face-inout", tags=["admin-face-inout"])

# %d-%m-%Y added per the real device-export format confirmed at
# D:\Maruti\format.xlsx — Date is stored there as plain text (not a real
# Excel date cell, data_type='s', number_format='@') like "01-03-2026", i.e.
# day-month-year with dashes, which none of the other accepted formats match.
#
# %d-%b-%y (2-digit year) added per the real Nagar import (D:\Maruti\ATT.xls,
# 2026-08-04): confirmed live that this file's Date column IS a genuine
# Excel date cell (xlrd ctype=3, number format 'dd-mmm-yy'), so cellDates
# should apply — but the frontend's `dateNF: 'yyyy-mm-dd'` override didn't
# take effect for this specific file, and every row instead arrived here as
# "01-Jul-26" (the cell's OWN dd-mmm-yy format, 2-digit year) — which
# %d-%b-%Y (4-digit year) genuinely can't parse (confirmed: Python's
# strptime rejects a 2-digit year against %Y outright, it doesn't just
# mis-parse the century). Rather than debug SheetJS's dateNF behavior
# further (not reproducible outside a real browser), the backend now simply
# also accepts the 2-digit-year form directly — same pragmatic "accept the
# format actually being sent" fix already applied twice before in this file.
_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y")


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


Team = Literal["pune", "nagar"]

# Nagar's ~25 device-export-only columns (see the module docstring's
# 2026-08-04 update) — everything in FaceInOut_Nagar beyond the 7 core
# fields (Employee ID/Name/Date/In/Break/Resume/Out) it shares with the Pune
# FaceInOut table, plus the server-derived Weekday. Keyed by the exact Excel
# header text (also the exact bracket-quoted SQL column name), carried
# through ImportRow.extra as plain strings and parsed here by type.
_NAGAR_TEXT_EXTRA_FIELDS = (
    "empno", "Card ID", "Group", "Department", "Section", "Day Type", "Schedule",
    "OT", "Done", "leavetype", "Remark", "reason_l", "reason_e", "reason_m",
)
_NAGAR_FLOAT_EXTRA_FIELDS = (
    "Work", "Overtime", "Diff.OT", "Short Minutes", "sumhr", "sumot", "sumdiff",
    "sumshort", "leavehour", "leaveday",
)


def _parse_extra_float(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_extra_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
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
    # Nagar-only: the ~25 device-export columns beyond the 7 core fields
    # above, keyed by exact Excel header text — see
    # _NAGAR_TEXT_EXTRA_FIELDS/_NAGAR_FLOAT_EXTRA_FIELDS and the module
    # docstring's 2026-08-04 update. Always empty for Pune imports.
    extra: dict[str, str] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    team: Team = "pune"
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


def _row_nagar(r: dict) -> FaceInOutRow:
    # Same FaceInOutRow shape as _row() above, mapped from
    # FaceInOut_Nagar's differently-named core columns (Employee ID/Weekday
    # instead of FaceId/WeekDay) — see the module docstring's 2026-08-04
    # update for why the response shape itself stays identical across teams.
    return FaceInOutRow(
        face_id=r["Employee ID"], name=r["Name"] or "",
        date=r["Date"].isoformat() if r["Date"] else "",
        week_day=r["Weekday"] or "",
        in_time=r["In"] or "", break_time=r["Break"] or "",
        resume_time=r["Resume"] or "", out_time=r["Out"] or "",
    )


@router.get("", response_model=FaceInOutSearchResponse)
def get_face_inout_rows(
    team: Team = "pune", name: str = "", date_from: date | None = None, date_to: date | None = None,
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

    if team == "nagar":
        table_sql = "FaceInOut_Nagar"
        select_sql = "[Employee ID], Name, [Date], Weekday, [In], [Break], Resume, [Out]"
        map_row = _row_nagar
    else:
        table_sql = "FaceInOut"
        select_sql = "FaceId, Name, [Date], WeekDay, [In], [Break], Resume, [Out]"
        map_row = _row

    with get_cursor() as cursor:
        # Fetches one row beyond the cap purely to detect whether there WAS
        # more (capped=True) without a separate COUNT(*) query.
        cursor.execute(
            f"SELECT TOP {_MAX_SEARCH_ROWS + 1} {select_sql} FROM {table_sql} "
            f"WHERE 1 = 1{where} ORDER BY [Date] DESC, Name",
            *params,
        )
        rows = rows_to_dicts(cursor)
    capped = len(rows) > _MAX_SEARCH_ROWS
    return FaceInOutSearchResponse(rows=[map_row(r) for r in rows[:_MAX_SEARCH_ROWS]], capped=capped)


@router.post("/import", response_model=ImportResponse)
def import_face_inout_rows(body: ImportRequest, current_user: CurrentUser = Depends(get_current_user)) -> ImportResponse:
    skipped: list[ImportResultRow] = []
    imported = 0
    seen_in_batch: set[tuple[int, date]] = set()

    # Only the table/ID-column and (for Nagar) the extra device-export
    # columns differ between teams — every validation rule (invalid ID,
    # missing name, invalid date, duplicate within this file) is identical,
    # so it stays common to both branches below rather than being duplicated
    # per team. A row that already exists for this (FaceId, Date) is
    # upserted (see below), not skipped.
    table_sql = "FaceInOut_Nagar" if body.team == "nagar" else "FaceInOut"
    id_column_sql = "[Employee ID]" if body.team == "nagar" else "FaceId"

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

            cursor.execute(f"SELECT 1 FROM {table_sql} WHERE {id_column_sql} = ? AND [Date] = ?", face_id, parsed_date)
            row_exists = cursor.fetchone() is not None

            week_day = parsed_date.strftime("%A")

            if body.team == "nagar":
                no_value = _parse_extra_int(row.extra.get("No."))
                text_columns_sql = ", ".join(f"[{f}]" for f in _NAGAR_TEXT_EXTRA_FIELDS)
                float_columns_sql = ", ".join(f"[{f}]" for f in _NAGAR_FLOAT_EXTRA_FIELDS)
                if row_exists:
                    # Upsert, not skip: a row commonly already exists here from
                    # an earlier roster-only import (Pune's own placeholder
                    # convention, see parsePuneRows) or a prior partial import
                    # — that earlier row has this same (FaceId, Date) key but
                    # blank punch/attendance data. Silently skipping a later,
                    # real attendance import for that same person/day (as this
                    # endpoint used to do) left the blank placeholder in place
                    # forever and discarded the real data. Updating in place
                    # is what "ready to be filled in by a later real
                    # attendance import" (see module docstring) actually
                    # requires.
                    set_extra_sql = ", ".join(f"[{f}] = ?" for f in _NAGAR_TEXT_EXTRA_FIELDS)
                    set_extra_float_sql = ", ".join(f"[{f}] = ?" for f in _NAGAR_FLOAT_EXTRA_FIELDS)
                    cursor.execute(
                        f"UPDATE FaceInOut_Nagar SET [No.] = ?, Name = ?, Weekday = ?, [In] = ?, [Break] = ?, Resume = ?, [Out] = ?, "
                        f"{set_extra_sql}, {set_extra_float_sql} WHERE [Employee ID] = ? AND [Date] = ?",
                        no_value, name, week_day,
                        row.in_time.strip(), row.break_time.strip(), row.resume_time.strip(), row.out_time.strip(),
                        *(row.extra.get(f, "").strip() for f in _NAGAR_TEXT_EXTRA_FIELDS),
                        *(_parse_extra_float(row.extra.get(f)) for f in _NAGAR_FLOAT_EXTRA_FIELDS),
                        face_id, parsed_date,
                    )
                else:
                    text_placeholders = ", ".join("?" for _ in _NAGAR_TEXT_EXTRA_FIELDS)
                    float_placeholders = ", ".join("?" for _ in _NAGAR_FLOAT_EXTRA_FIELDS)
                    cursor.execute(
                        f"INSERT INTO FaceInOut_Nagar ([No.], [Employee ID], Name, [Date], Weekday, [In], [Break], Resume, [Out], "
                        f"{text_columns_sql}, {float_columns_sql}, CreationUserID, CreationDate) "
                        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {text_placeholders}, {float_placeholders}, ?, SYSDATETIME())",
                        no_value, face_id, name, parsed_date, week_day,
                        row.in_time.strip(), row.break_time.strip(), row.resume_time.strip(), row.out_time.strip(),
                        *(row.extra.get(f, "").strip() for f in _NAGAR_TEXT_EXTRA_FIELDS),
                        *(_parse_extra_float(row.extra.get(f)) for f in _NAGAR_FLOAT_EXTRA_FIELDS),
                        current_user.user_id,
                    )
            else:
                if row_exists:
                    # Same upsert reasoning as the Nagar branch above.
                    cursor.execute(
                        "UPDATE FaceInOut SET Name = ?, WeekDay = ?, [In] = ?, [Break] = ?, Resume = ?, [Out] = ? "
                        "WHERE FaceId = ? AND [Date] = ?",
                        name, week_day,
                        row.in_time.strip(), row.break_time.strip(), row.resume_time.strip(), row.out_time.strip(),
                        face_id, parsed_date,
                    )
                else:
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
