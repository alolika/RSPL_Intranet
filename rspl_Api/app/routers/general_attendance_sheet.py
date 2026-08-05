"""New, independent "Employee Attendance Sheet" module — greenfield, per
explicit request NOT to modify or reuse the existing Attendance module
(general.py's GET /general/attendance, a read-only report backed by a
legacy stored proc webproc_getuserschedule, with no persistence layer of
its own at all). This is a completely separate table/router/page: a real,
editable month-wise grid, architected after this app's own Executive
Schedule precedent (composite natural key, sparse per-cell storage, one
flat GET returning employees+days+cells, autosave-per-edit with a manual
Save button as backup) — NOT after Executive Schedule's own UI, per the
explicit "fresh layout, don't copy the existing form" request (which was
about not copying the file the user pointed at directly; borrowing a
proven, already-battle-tested SAVE/GRID-DATA architecture is a separate,
purely technical decision, not a UI copy).

Backed by RSPL_EmployeeAttendanceSheet (hand-created directly on the SQL
Server instance, confirmed live — no migration tooling exists in this
repo):
- PK (EmployeeId, AttendanceDate) — composite natural key, no surrogate
  identity column, matching this app's established convention elsewhere
  (RSPL_ExecScheduleCell, FaceInOut) — sparse storage: a cell with no
  status set has no row at all, not a row with a blank Status.
- WeekDay is always derived server-side from AttendanceDate, never trusted
  from the client or an import file — same convention as every other table
  in this app that carries a derived weekday column.
- Status was validated on write against a small allow-list
  (Present/Absent/Half Day/Leave/etc.) back when this table had a write
  path — see the 2026-08-05 read-only update below; the allow-list was
  removed along with the endpoints that used it, since GET /cells is the
  only remaining consumer and it only ever writes overlay values it already
  controls (never a stray/unvalidated string).
- Remark exists on the table (more future-enhancement headroom) but has no
  UI or endpoint wiring yet beyond the column existing.
- CreationUserID/CreationDate are set once on insert; LastEditedByUserId/
  LastEditedAt update on every subsequent save — mirrors
  RSPL_ExecScheduleCell's own audit-column split for a row that's expected
  to be edited repeatedly after creation, not just written once.

Employee lookup deliberately reuses the EXISTING GET /general/users
endpoint (general.py, untouched by this module) instead of adding a
duplicate — that endpoint is a generic "active employees" lookup already
shared by several unrelated features, not part of the existing Attendance
module this request says not to touch.

Update (2026-08-05): converted to READ-ONLY, per explicit request — manual
editing turned out to be unwanted; the sheet should show only what the
Holiday/Present/Approved-Leave overlays below compute automatically. The
POST .../cells/save (real-time autosave per cell blur) and POST .../import
(client-side-parsed Excel upsert) endpoints described in the two paragraphs
above were REMOVED entirely (not just hidden client-side) — this router now
only has the GET /cells endpoint. RSPL_EmployeeAttendanceSheet, its
composite (EmployeeId, AttendanceDate) key, and LastEditedByUserId/
LastEditedAt columns are unchanged and still read by GET /cells (a manually
stored row is still the lowest-priority layer the overlays can win over,
per the FINAL PRIORITY ORDER below) — there's just no way to write a new
one anymore through this app. If manual entry is ever needed again, the
removed endpoints are recoverable from source control rather than being
rebuilt from scratch.

Update (2026-08-05): Holiday/Alternate-day overlay, per explicit request —
reuses RSPL_ExecScheduleHolidayRule and its exact computation logic
(_get_holiday_rules/_holiday_days_for_month), imported directly from
support_executive_schedule.py rather than duplicated (same precedent as
vote.py importing LookupOption from developer.py — a deliberate, if
uncommon in this codebase, cross-router reuse rather than copy-pasting
already-validated logic that could drift out of sync). Matched by
ExecutiveId = this module's own EmployeeId (same UserMaster.UserID
identity everywhere in this app), exactly as requested. Computed fresh on
every GET /cells call and merged into the response only — NEVER written
into RSPL_EmployeeAttendanceSheet — so editing/removing an executive's
holiday rule is reflected immediately with no migration step, and the
underlying saved data is never silently rewritten by this overlay. Per the
same precedent this logic is reused from, a computed "H" wins over
whatever status happens to already be stored for that date (a holiday is a
fact about the calendar, not something that should coexist with a
conflicting manual entry) — this only affects what THIS endpoint returns,
never the stored row itself.

Update (2026-08-05, later same day): Approved-Leave overlay, per explicit
request — reads live from web_leaveapplication (the Leave Application
Register's own table; no separate copy/sync), same
Approved-and-not-Cancelled WHERE clause as Executive Schedule's own
_get_approved_leave_overlay (HOSanctioned=1 OR CEOSanctioned=1, both
Cancelled flags = 0 — a Pending row has neither sanction flag set and a
Rejected row has HORejected=1/HOSanctioned=0, so both are already excluded
by requiring HOSanctioned=1 OR CEOSanctioned=1 without needing a separate
"not rejected" check). Written as this module's own small
_get_approved_leave_status_overlay rather than reusing
_get_approved_leave_overlay verbatim, since that function always returns
text="L" (plus a color this module has no concept of) — this one maps
ForDays to this module's own two-status vocabulary instead: "L" for a
full-day leave, "HD" for a half-day one (ForDays == '0.5 (1st Half)' or
'0.5 (2nd Half)', matching the exact same leave-day span logic/half-day
day-1 special case as the function it's modeled on). Computed fresh on
every GET /cells call, never written to RSPL_EmployeeAttendanceSheet — so a
leave that's later approved/rejected/cancelled or has its dates edited is
reflected the next time the sheet loads, no separate sync step needed.
Applied AFTER the Holiday overlay above (same order/rationale as Executive
Schedule: Leave wins over Holiday on any date that's both), and — like
Holiday — wins over whatever status is actually stored for that date.

Update (2026-08-05, later still): Present (P) overlay from Face In/Out
device records, per explicit request — checks BOTH FaceInOut (Pune Team)
and FaceInOut_Nagar (Nagar Team) for a genuine punch (non-blank [In] or
[Out]) on that (employee, date); a match in EITHER table counts, since this
module doesn't track which team an employee belongs to. A row that exists
but has both [In] and [Out] blank (e.g. a roster-only placeholder row from
Face In/Out's own import — see admin_face_inout.py) is NOT treated as
present, per explicit "do not mark P if no valid attendance record exists."

FINAL PRIORITY ORDER (each overlay below is applied after — and so wins
over — the one before it, same "later overlay wins" mechanism used
throughout this endpoint):
  Absent default (A)  <  stored/manual entry  <  Holiday/Alternate-day (H) + Public Holiday (PH) [same tier]  <  Present (P) [Face In/Out + Executive Schedule, same tier]  <  Approved Leave (L/HD)  <  Sandwich Leave conversion (a run of one or more consecutive H/PH days bounded by L on both sides -> L, see the 2026-08-05 update notes further below)
Absent (A) sits at the very bottom, per the 2026-08-05 (Absent default)
update further below — it only ever fills a date that every other source
above left completely blank, and only for a date already in the past (a
day still in progress may yet pick up real evidence later that same day).
Rationale for Present sitting between Holiday and Leave (a judgment call —
no prior precedent fixed this specific 3-way order, unlike Leave-over-
Holiday which Executive Schedule already established): real evidence the
person was doing something that day — a genuine device punch, or a
non-blank Executive Schedule entry — should show instead of the calendar's
default Holiday assumption; but a formally HO/CEO-approved Leave decision
stays authoritative even over either signal (e.g. someone badged in
briefly, or still had an old Executive Schedule entry, on an approved
leave day) — an HR decision should not be silently overridden by
operational data. Face In/Out and Executive Schedule are peers at the same
tier (their two overlay functions are simply both applied before Leave,
in either order — whichever finds evidence sets "P", and if only one of
them does, that's still enough). All overlays are computed fresh on every
call and never written back into RSPL_EmployeeAttendanceSheet.

Update (2026-08-05, later still — real bug fix): the Present overlay above
originally matched FaceInOut.FaceId/FaceInOut_Nagar.[Employee ID] directly
against UserMaster.UserID, the same identity convention every other overlay
in this module uses. Confirmed live this assumption is WRONG for Face
In/Out specifically — those device-assigned IDs are the device's own
independent enrollment numbering, unrelated to UserMaster.UserID. Concrete
confirmed example (the reported bug): FaceInOut_Nagar Employee ID 121 is
enrolled on the device as "manoj hogale", but UserMaster's real Manoj
Hogale is UserID 165 — meanwhile the real Vijay Chaudhari (UserID 121) had
zero rows under ID 121 in either table; his actual 14 July punches were
sitting under FaceId 99 in FaceInOut and Employee ID 73 in FaceInOut_Nagar
(enrolled there as "Vijay Choudhari", a spelling variant). Measured live:
0 of 97 distinct Pune FaceIds and 0 of 34 distinct Nagar Employee IDs
matched their same-numbered UserMaster name. This wasn't just a
missed-records bug — for any device ID coincidentally shared with a
DIFFERENT real employee's UserID, it would silently mark the WRONG person
present. First fixed by matching on normalized device-enrolled NAME
instead — a reasonable stopgap that resolved the large majority of
identities, plus a small hand-confirmed alias list for a few genuine
spelling variants (same pattern already used for
RSPL_ExecScheduleHolidayRule's own import) — but see the next update, which
supersedes this with the actual authoritative mapping.

Update (2026-08-05, later still — the actual authoritative fix): per
explicit request/discovery, UserMaster has two dedicated mapping columns
that were missed until now — FaceID (that employee's ID in FaceInOut,
Pune Team) and EmployeeID (that employee's ID in FaceInOut_Nagar, Nagar
Team). Confirmed live these are genuine, intentional per-employee mappings,
not a coincidence: UserID 121 (Vijay Chaudhari) has EmployeeID=73, exactly
FaceInOut_Nagar's real Employee ID 73 for him; UserID 165 (Manoj Hogale)
has EmployeeID=121 — confirming FaceInOut_Nagar's own Employee ID 121 row
was never wrong/orphaned data, it's Manoj's own correct ID all along, and
both the original UserID-based matching AND the name-based workaround that
replaced it were reading the wrong identity. The Present overlay
(_get_present_day_overlay) now uses FaceID/EmployeeID directly instead of
name-matching — the two ID spaces are independent (the same raw number can
mean a different person in each), so FaceInOut and FaceInOut_Nagar are
queried and mapped back to an employee separately, never merged into one
lookup keyed by a shared number. _FACE_NAME_ALIASES/_normalize_person_name
were removed as no longer needed. Existing stored data that was corrupted
by the earlier UserID-matching bug (auto-saved "P" values attributing one
person's real punches to a different, coincidentally-same-ID employee) was
already identified and cleaned up directly in the database as part of this
fix — see the conversation history, not something this code needs to redo.

Update (2026-08-05, later still): Executive Schedule overlay, per explicit
request — reads RSPL_ExecScheduleCell directly (matched by ExecutiveId =
this module's own EmployeeId, same identity everywhere) and treats any
EFFECTIVE cell value other than blank/H/L as Present. "Effective" matters
here: Executive Schedule's own H (Holiday) and L (Leave) are themselves
computed overlays, almost never actually stored as literal text in
RSPL_ExecScheduleCell (see support_executive_schedule.py's own docstring —
"Holiday/Alternate-day H values are deliberately NOT frozen into this
template"), so reading the raw stored CellText alone would essentially
never see an H or L to ignore in the first place, and would also miss the
fact that a date IS a holiday/leave from Executive Schedule's point of
view. This overlay therefore reuses Executive Schedule's own two overlay
functions first (_get_holiday_rules/_holiday_days_for_month, already
imported above for this module's own Holiday overlay; and
_get_approved_leave_overlay, imported fresh below — Executive Schedule's
own version, which always returns "L" regardless of half/full day, unlike
this module's separate L/HD-split Approved-Leave overlay) to reconstruct
the exact same effective text Executive Schedule's own grid would show,
THEN applies the ignore-H/L rule to that.

Sits at the SAME priority tier as the Face In/Out Present overlay above
(both are "real evidence the person was doing something that day," just
from different sources) — applied together, before Approved Leave, so a
formal HR-approved leave decision still wins over either. See the
module-level priority summary further up for the full chain.

Update (2026-08-05, later still): Public Holiday (PH) overlay, per explicit
request — reads the real company-wide holiday calendar (RSPL_HolidayMaster,
admin_holiday.py's own Holiday Master admin page: Independence Day, Diwali,
Republic Day, etc., each a HolidayDate-to-ToDate date RANGE, Enabled=1 rows
only) rather than RSPL_ExecScheduleHolidayRule's per-executive weekly-off/
alternate-day RULE (the pre-existing "H" overlay above, left completely
unchanged). Given a different status code and color from "H" specifically
because the two mean different things — H is "this person's own alternate
weekly-off pattern," PH is "the whole company is off today" — collapsing
them into one code/color would have erased that distinction the two source
tables already keep separate. Applies identically to every employee (a
public holiday isn't a per-person rule), so _get_holiday_master_days below
returns a flat set of days rather than a per-employee dict. Sits at the same
priority tier as "H" — applied right after it, before Present — so real
attendance evidence still overrides an official holiday exactly the same
way it already overrides the alternate-day "H" (e.g. someone who actually
came in on Independence Day shows P, not PH), per the same rationale
already established above for Present-over-Holiday.

Update (2026-08-05, later still): Sandwich Leave conversion, per explicit
request — a lone Holiday (H or PH) with a full-day Approved-Leave (L) on
BOTH the day immediately before and the day immediately after, for the
same employee, is converted to L too (see _approved_leave_status_for_date
and the conversion loop in get_cells, applied after every other overlay
including Leave itself, so it sees each neighbor's final status).
Deliberately requires "L" specifically on both sides, not "HD" — a
half-day leave doesn't fully satisfy "Approved Leave day" on that side.
Handles the two month-boundary cases (a Holiday on the 1st needs the
previous month's last day; one on the last day needs the next month's 1st)
via two small single-date queries rather than leaving them unhandled.
Every other overlay (Holiday/PH, Face In/Out, Executive Schedule,
Absent-default) is untouched by this change.

Update (2026-08-05, later still — generalized to multi-day Holiday runs):
the version above only checked each Holiday day's own immediate two
neighbors, which is correct for a single sandwiched Holiday but silently
does NOT convert a run of 2+ CONSECUTIVE Holiday days between two Leave
dates — e.g. UserID 142 with L on 07-Aug, H/H on 08-09-Aug, L on 10-Aug:
checking 08-Aug alone sees 07-Aug=L but 09-Aug=H (not L yet), and checking
09-Aug alone sees 08-Aug=H (not L) and 10-Aug=L, so neither day's own
immediate neighbors are both L even though the whole 08-09 run should
convert. Fixed by grouping each employee's Holiday (H/PH) days into
contiguous runs first (via itertools.groupby on the sorted day list) and
checking only the run's own two OUTER boundary days against Leave, then
converting every day in the run at once if both hold — correct for a run
of any length, including the original length-1 case. See the conversion
loop in get_cells for the actual grouping logic.
"""

from calendar import monthrange
from datetime import date, timedelta
from itertools import groupby

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.routers.support_executive_schedule import _get_approved_leave_overlay, _get_holiday_rules, _holiday_days_for_month
from app.rights import FORM_EMPLOYEE_ATTENDANCE_SHEET, require_access

router = APIRouter(prefix="/general/attendance-sheet", tags=["general-attendance-sheet"])


def _get_holiday_master_days(year: int, month: int, days_in_month: int) -> set[int]:
    """Public Holiday (PH) overlay for the Employee Attendance Sheet — see
    this module's 2026-08-05 (Public Holiday) update note. Reads
    RSPL_HolidayMaster (admin_holiday.py's real, admin-maintained company
    holiday calendar — HolidayDate/ToDate is a date RANGE, Enabled=0 rows
    are soft-deleted and excluded). Applies identically to every employee,
    so this returns a flat set of days rather than a per-employee dict."""
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)
    with get_cursor() as cursor:
        # CAST to DATE: HolidayDate is a DATETIME column, ToDate a DATE
        # column (confirmed live) — casting both the same way avoids
        # comparing/iterating a mismatched datetime-vs-date pair below.
        cursor.execute(
            "SELECT CAST(HolidayDate AS DATE) AS HolidayDate, CAST(ToDate AS DATE) AS ToDate "
            "FROM RSPL_HolidayMaster WHERE Enabled = 1 AND HolidayDate <= ? AND ToDate >= ?",
            last_day, first_day,
        )
        rows = rows_to_dicts(cursor)

    days: set[int] = set()
    for r in rows:
        current = max(r["HolidayDate"], first_day)
        span_end = min(r["ToDate"], last_day)
        while current <= span_end:
            days.add(current.day)
            current += timedelta(days=1)
    return days


def _get_approved_leave_status_overlay(employee_ids: list[int], year: int, month: int, days_in_month: int) -> dict[tuple[int, int], str]:
    """Approved-Leave overlay for the Employee Attendance Sheet — see this
    module's 2026-08-05 update note. Returns {(employee_id, day): status},
    "L" for a full-day leave or "HD" for a half-day one, reading live from
    web_leaveapplication (the Leave Application Register's own table)."""
    if not employee_ids:
        return {}
    placeholders = ",".join("?" for _ in employee_ids)
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT Userid, CAST(FromDate AS DATE) AS FromDate, CAST(ToDate AS DATE) AS ToDate, ForDays "
            f"FROM web_leaveapplication "
            f"WHERE Userid IN ({placeholders}) "
            f"AND (HOSanctioned = 1 OR CEOSanctioned = 1) "
            f"AND ISNULL(CancelledByApplicant, 0) = 0 AND ISNULL(CancelledbyHO, 0) = 0 "
            f"AND FromDate <= ? AND ToDate >= ?",
            *employee_ids, last_day, first_day,
        )
        rows = rows_to_dicts(cursor)

    overlay: dict[tuple[int, int], str] = {}
    for r in rows:
        employee_id = r["Userid"]
        from_date, to_date = r["FromDate"], r["ToDate"]
        for_days = r["ForDays"] or ""
        current = max(from_date, first_day)
        span_end = min(to_date, last_day)
        while current <= span_end:
            is_from_day = current == from_date
            is_half_day = is_from_day and for_days in ("0.5 (1st Half)", "0.5 (2nd Half)")
            overlay[(employee_id, current.day)] = "HD" if is_half_day else "L"
            current += timedelta(days=1)
    return overlay


def _approved_leave_status_for_date(employee_ids: list[int], target_date: date) -> dict[int, str]:
    """Returns {employee_id: 'L'|'HD'} for employees with an Approved-Leave
    record covering target_date — same HOSanctioned/CEOSanctioned/
    not-cancelled/half-day rule as _get_approved_leave_status_overlay
    above, just for a single date instead of a full month. Used only to
    check the day immediately before the 1st or after the last day of the
    requested month for the Holiday-sandwiched-between-Leave conversion
    below (see this module's 2026-08-05, later still, update note) — the
    month-range overlay's own (employee_id, day-of-month) keys can't
    represent a date outside that month, and a real neighboring-day check
    at a month boundary needs one."""
    if not employee_ids:
        return {}
    placeholders = ",".join("?" for _ in employee_ids)
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT Userid, CAST(FromDate AS DATE) AS FromDate, ForDays "
            f"FROM web_leaveapplication "
            f"WHERE Userid IN ({placeholders}) "
            f"AND (HOSanctioned = 1 OR CEOSanctioned = 1) "
            f"AND ISNULL(CancelledByApplicant, 0) = 0 AND ISNULL(CancelledbyHO, 0) = 0 "
            f"AND FromDate <= ? AND ToDate >= ?",
            *employee_ids, target_date, target_date,
        )
        rows = rows_to_dicts(cursor)

    result: dict[int, str] = {}
    for r in rows:
        is_half_day = r["FromDate"] == target_date and (r["ForDays"] or "") in ("0.5 (1st Half)", "0.5 (2nd Half)")
        result[r["Userid"]] = "HD" if is_half_day else "L"
    return result


def _get_present_day_overlay(employees: list["EmployeeOption"], year: int, month: int, days_in_month: int) -> dict[tuple[int, int], str]:
    """Present (P) overlay from Face In/Out device records — see this
    module's 2026-08-05 update note. A genuine punch (non-blank [In] or
    [Out]) counts; a row that exists but has both blank (e.g. a
    roster-only placeholder — see admin_face_inout.py) does NOT.

    Matched via UserMaster's own dedicated mapping columns — FaceID (Pune
    Team -> FaceInOut.FaceId) and EmployeeID (Nagar Team ->
    FaceInOut_Nagar.[Employee ID]) — confirmed live these are genuine,
    intentional per-employee device IDs, NOT the same identity space as
    each other or as UserMaster.UserID: e.g. UserID 121 (Vijay Chaudhari)
    has EmployeeID=73, which is exactly FaceInOut_Nagar's real Employee ID
    73 for him; UserID 165 (Manoj Hogale) has EmployeeID=121 — confirming
    FaceInOut_Nagar's own Employee ID 121 row was never wrong data, it's
    Manoj's correct ID. A value of 0 means "not enrolled on that device" —
    that table is simply skipped for that employee. FaceID and EmployeeID
    are independent numberings (the same raw number can mean a different
    person in each), so FaceInOut and FaceInOut_Nagar are queried and
    mapped back to an employee_id SEPARATELY below, never merged into one
    lookup keyed by a shared number.

    (An earlier revision of this overlay matched on raw FaceId/[Employee
    ID] directly against UserMaster.UserID, then a later revision matched
    by device-enrolled NAME instead once that was found to be wrong too —
    both were reasonable given what was known at the time, but this
    FaceID/EmployeeID mapping is the actual, authoritative one and
    supersedes both.)
    """
    if not employees:
        return {}
    employee_ids = [e.employee_id for e in employees]
    placeholders = ",".join("?" for _ in employee_ids)
    with get_cursor() as cursor:
        cursor.execute(f"SELECT UserID, FaceID, EmployeeID FROM UserMaster WHERE UserID IN ({placeholders})", *employee_ids)
        mapping_rows = rows_to_dicts(cursor)

    face_id_to_employee = {r["FaceID"]: r["UserID"] for r in mapping_rows if r["FaceID"]}
    nagar_id_to_employee = {r["EmployeeID"]: r["UserID"] for r in mapping_rows if r["EmployeeID"]}
    if not face_id_to_employee and not nagar_id_to_employee:
        return {}

    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)
    punch_filter = "(LTRIM(RTRIM(ISNULL([In], ''))) <> '' OR LTRIM(RTRIM(ISNULL([Out], ''))) <> '')"

    overlay: dict[tuple[int, int], str] = {}

    if face_id_to_employee:
        face_placeholders = ",".join("?" for _ in face_id_to_employee)
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT FaceId, [Date] FROM FaceInOut "
                f"WHERE FaceId IN ({face_placeholders}) AND [Date] BETWEEN ? AND ? AND {punch_filter}",
                *face_id_to_employee.keys(), first_day, last_day,
            )
            for r in rows_to_dicts(cursor):
                overlay[(face_id_to_employee[r["FaceId"]], r["Date"].day)] = "P"

    if nagar_id_to_employee:
        nagar_placeholders = ",".join("?" for _ in nagar_id_to_employee)
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT [Employee ID], [Date] FROM FaceInOut_Nagar "
                f"WHERE [Employee ID] IN ({nagar_placeholders}) AND [Date] BETWEEN ? AND ? AND {punch_filter}",
                *nagar_id_to_employee.keys(), first_day, last_day,
            )
            for r in rows_to_dicts(cursor):
                overlay[(nagar_id_to_employee[r["Employee ID"]], r["Date"].day)] = "P"

    return overlay


def _get_exec_schedule_present_overlay(employee_ids: list[int], year: int, month: int, days_in_month: int) -> dict[tuple[int, int], str]:
    """Present (P) overlay from Executive Schedule — see this module's
    2026-08-05 (Executive Schedule) update note. Reconstructs the same
    EFFECTIVE cell text Executive Schedule's own grid would show (raw
    RSPL_ExecScheduleCell.CellText, then Executive Schedule's own Holiday
    overlay, then Executive Schedule's own Approved-Leave overlay — same
    two functions this module's own Holiday/Leave overlays already reuse,
    Leave applied last so it wins, exactly mirroring
    support_executive_schedule.py's own get_cells order), then marks a
    date Present if that effective text is anything other than blank, "H",
    or "L" (case-insensitive)."""
    if not employee_ids:
        return {}
    placeholders = ",".join("?" for _ in employee_ids)
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)

    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT ExecutiveId, CellDate, CellText FROM RSPL_ExecScheduleCell "
            f"WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *employee_ids, first_day, last_day,
        )
        stored_rows = rows_to_dicts(cursor)

    effective_text: dict[tuple[int, int], str] = {
        (r["ExecutiveId"], r["CellDate"].day): (r["CellText"] or "").strip() for r in stored_rows
    }

    holiday_rules = _get_holiday_rules(employee_ids)
    for executive_id, rule in holiday_rules.items():
        for day in _holiday_days_for_month(rule, year, month, days_in_month):
            effective_text[(executive_id, day)] = "H"

    leave_overlay = _get_approved_leave_overlay(employee_ids, year, month, days_in_month)
    for (executive_id, day), (text, _color) in leave_overlay.items():
        effective_text[(executive_id, day)] = text

    overlay: dict[tuple[int, int], str] = {}
    for (executive_id, day), text in effective_text.items():
        normalized = text.strip().upper()
        if normalized and normalized not in ("H", "L"):
            overlay[(executive_id, day)] = "P"
    return overlay


class EmployeeOption(BaseModel):
    employee_id: int
    name: str


class AttendanceCell(BaseModel):
    employee_id: int
    day: int
    status: str


class AttendanceCellsResponse(BaseModel):
    employees: list[EmployeeOption]
    days: list[int]
    cells: list[AttendanceCell]


@router.get("/cells", response_model=AttendanceCellsResponse)
def get_attendance_cells(
    year: int, month: int, current_user: CurrentUser = Depends(get_current_user)
) -> AttendanceCellsResponse:
    require_access(current_user.user_id, FORM_EMPLOYEE_ATTENDANCE_SHEET, "view")
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        employees = [EmployeeOption(employee_id=r["UserID"], name=r["Name"] or "") for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT EmployeeId, DAY(AttendanceDate) AS Day, Status FROM RSPL_EmployeeAttendanceSheet "
            "WHERE YEAR(AttendanceDate) = ? AND MONTH(AttendanceDate) = ?",
            year, month,
        )
        stored_rows = rows_to_dicts(cursor)

    days = list(range(1, monthrange(year, month)[1] + 1))
    cells_by_key: dict[tuple[int, int], str] = {(r["EmployeeId"], r["Day"]): r["Status"] or "" for r in stored_rows}

    # Holiday/Alternate-day overlay — see this module's 2026-08-05 update
    # note. Computed fresh every call, never written to
    # RSPL_EmployeeAttendanceSheet, and wins over whatever's actually
    # stored for that date (matching the exact precedent this logic is
    # reused from).
    employee_ids = [e.employee_id for e in employees]
    holiday_rules = _get_holiday_rules(employee_ids)
    for employee_id, rule in holiday_rules.items():
        for day in _holiday_days_for_month(rule, year, month, len(days)):
            cells_by_key[(employee_id, day)] = "H"

    # Public Holiday (PH) overlay — see this module's 2026-08-05 (Public
    # Holiday) update note. Same priority tier as Holiday/Alternate-day (H)
    # above (applied right after it, before Present below) but a distinct
    # status/color, since RSPL_HolidayMaster (company-wide calendar) and
    # RSPL_ExecScheduleHolidayRule (per-executive weekly-off rule) are
    # different concepts that happen to share a priority tier, not the same
    # thing computed two ways.
    holiday_master_days = _get_holiday_master_days(year, month, len(days))
    for employee_id in employee_ids:
        for day in holiday_master_days:
            cells_by_key[(employee_id, day)] = "PH"

    # Present (P) overlay — Face In/Out (Pune + Nagar) and Executive
    # Schedule are peers at the same priority tier (both applied here,
    # before Approved Leave below); see this module's 2026-08-05 update
    # notes for the full priority-order rationale.
    present_overlay = _get_present_day_overlay(employees, year, month, len(days))
    for (employee_id, day), status in present_overlay.items():
        cells_by_key[(employee_id, day)] = status

    exec_schedule_overlay = _get_exec_schedule_present_overlay(employee_ids, year, month, len(days))
    for (employee_id, day), status in exec_schedule_overlay.items():
        cells_by_key[(employee_id, day)] = status

    # Approved-Leave overlay — applied LAST, so Leave wins over both Holiday
    # and Present-via-punch/Executive-Schedule on any date that's more than
    # one of these (same order/rationale as Executive Schedule's own
    # overlay — see this module's 2026-08-05 update note).
    leave_overlay = _get_approved_leave_status_overlay(employee_ids, year, month, len(days))
    for (employee_id, day), status in leave_overlay.items():
        cells_by_key[(employee_id, day)] = status

    # Sandwich Leave conversion — per explicit request: ANY run of one or
    # more CONSECUTIVE Holiday days (H, the per-executive alternate-day
    # rule, and/or PH, the company-wide RSPL_HolidayMaster calendar — a run
    # can mix both) immediately bounded on both sides by a full-day
    # Approved-Leave (L) date, for the SAME employee, is converted to Leave
    # too — a common HR/payroll convention (a holiday span inside a leave
    # period doesn't "reset"/interrupt the leave). Example from the actual
    # request: UserID 142 has L on 07-Aug, H/H on 08-09-Aug, L on 10-Aug ->
    # both 08-Aug and 09-Aug convert to L. Runs after every other overlay
    # above (including Leave itself) so it sees each day's FINAL status,
    # not an intermediate one. Deliberately requires "L" specifically on
    # both bounding days, not "HD" — the request says "Approved Leave
    # dates," which a half-day doesn't fully satisfy either side of.
    #
    # Grouped into contiguous runs (not checked one day at a time) because
    # a single-day-adjacent check does NOT generalize to a 2+ day holiday
    # run: for 08-09-Aug above, checking 08-Aug's immediate neighbors alone
    # sees 07-Aug=L but 09-Aug=H (not L yet), and checking 09-Aug alone sees
    # 08-Aug=H (not L) and 10-Aug=L — neither day's two immediate neighbors
    # are both L, so neither would convert under a naive per-day check even
    # though the whole run should. Grouping into a run first and checking
    # only the run's own two outer boundary days fixes this for any run
    # length.
    #
    # Boundary dates (a run touching day 1 needs the previous month's last
    # day; a run touching the last day needs the next month's 1st) fall
    # outside this month's own data, so _approved_leave_status_for_date is
    # used for just those two specific dates — two small single-date
    # queries total for the whole month, not one per employee or per run.
    first_day = date(year, month, 1)
    last_day_date = date(year, month, len(days))
    leave_before_month = _approved_leave_status_for_date(employee_ids, first_day - timedelta(days=1))
    leave_after_month = _approved_leave_status_for_date(employee_ids, last_day_date + timedelta(days=1))

    for employee_id in employee_ids:
        holiday_days = sorted(day for day in days if cells_by_key.get((employee_id, day)) in ("H", "PH"))
        for _, group in groupby(enumerate(holiday_days), key=lambda pair: pair[1] - pair[0]):
            run = [day for _, day in group]
            run_start, run_end = run[0], run[-1]
            prev_status = cells_by_key.get((employee_id, run_start - 1)) if run_start > 1 else leave_before_month.get(employee_id)
            next_status = cells_by_key.get((employee_id, run_end + 1)) if run_end < len(days) else leave_after_month.get(employee_id)
            if prev_status == "L" and next_status == "L":
                for day in run:
                    cells_by_key[(employee_id, day)] = "L"

    # Absent (A) default — per explicit request, the base of the priority
    # stack below everything above: Leave/Holiday/Present are each checked
    # first (in that order of precedence, unchanged from above), and only a
    # date that matched NONE of those three sources — no approved leave, no
    # holiday/alternate-day rule, no Face In/Out punch, no effective
    # Executive Schedule entry — falls through to here. Restricted to dates
    # strictly BEFORE today: a day that hasn't finished yet may still get a
    # punch later (e.g. checking at 10 AM, before that day's Face In/Out
    # sync), so marking it Absent this early would be a false positive that
    # flips back to P once real evidence arrives later the same day; a past
    # date that still has no evidence in any source genuinely has none
    # coming. Pure in-memory dict work over already-fetched data (no extra
    # DB round-trip), so this stays O(employees x days) regardless of table
    # size.
    today = date.today()
    for employee_id in employee_ids:
        for day in days:
            if date(year, month, day) >= today:
                continue
            key = (employee_id, day)
            if not cells_by_key.get(key):
                cells_by_key[key] = "A"

    cells = [AttendanceCell(employee_id=k[0], day=k[1], status=v) for k, v in cells_by_key.items()]
    return AttendanceCellsResponse(employees=employees, days=days, cells=cells)
