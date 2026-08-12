"""Executive Schedule — a brand-new, database-backed feature with no legacy
ASP.NET/VB source page to mirror (unlike most of this migration). Replaces
the retired `support_schedule.py` (a read-only display of a Power Automate ->
Outlook -> Schedule.xlsx snapshot, itself standing in for a defunct Google
Sheets integration). That entire import pipeline has been decommissioned —
data now lives here and is edited directly in the Angular app.

Schema (hand-created directly on the SQL Server instance, confirmed live —
no migration tooling exists in this repo):

- RSPL_ExecScheduleTeam: fixed lookup, seeded with exactly two rows
  ('Jwellsoft', 'Retailware'), each tagged with the UserMaster.UserTypeID
  that defines its roster (8 = Jwellsoft, 9 = Retailware). No "add team"
  endpoint in v1.
- RSPL_ExecScheduleCell: normalized (ExecutiveId, CellDate) -> (text, color)
  store, PK (ExecutiveId, CellDate). Deliberately not one column per day
  (unbounded/unmaintainable) and deliberately not tied to Dim_Time (only
  populated 2004-2015, see general_schedule.py) - CellDate is a plain date.
  ExecutiveId here is UserMaster.UserID (the same identity CurrentUser.user_id
  and every other *ByUserId audit column in this app already uses — see
  auth.py's login endpoint, which puts UserMaster.UserID in the JWT `sub`).
  CellTextColor (varchar(9), nullable, added 2026-07-31) is CellColor's
  independent counterpart — CellColor is the box's own background,
  CellTextColor is the entered text/number's color, applied separately in
  the UI so a user can color the content without changing the box color.

There used to be a third table, RSPL_ExecScheduleExecutive, holding a
manually maintained roster (add/rename/delete/reorder). Per explicit request
that roster concept is retired outright: the executive list for each team is
now derived live from UserMaster (Enabled=1, UserTypeID matching the team),
so a user created/deactivated in UserMaster automatically appears/disappears
here with no manual step. That table and its FK from RSPL_ExecScheduleCell
have been dropped from the database (it was empty — no historical schedule
data existed to migrate).

- RSPL_ExecScheduleExecutiveMeta: the roster itself is read-only (from
  UserMaster), but things about an executive's presentation on a given
  team's grid are still genuinely user-editable and must persist: manual
  row order (drag/move up/down), a per-row background color (RowColor —
  the day cells' fallback background, see get_cells' cell.color-or-
  row.rowColor precedence on the frontend), and — added per explicit
  request — ColumnColor (varchar(9), nullable, 2026-07-31), an
  independent background color for just that executive's own Executive
  (name) column cell, deliberately separate from RowColor so a user can
  color the name column without it bleeding into that row's day cells,
  and vice versa. PK (TeamId, ExecutiveId) — deliberately scoped per-team,
  not just per executive, since "load sequence separately for Retailware
  and Jwellsoft" was explicit, and it means an executive who ever moved
  between teams (UserTypeID changed) doesn't drag stale order/colors
  across with them. A row with no meta entry yet (a brand-new UserMaster
  user, or one who's simply never been dragged/colored) sorts after every
  row that DOES have an explicit SortOrder, then alphabetically among
  itself — see _get_active_executives' ORDER BY. That's what makes "new
  executives appear at the end by default" true without any extra
  bookkeeping.

- RSPL_ExecScheduleUserState: one row per user (PK UserId), remembering
  which Team/Year/Month they last had open. Per explicit request that
  reopening the form — on any device, after any restart — must land back
  exactly where the user left off, not just "whichever team happened to be
  in this one browser's localStorage" (the previous mechanism, now
  replaced by this). Read on mount, written after every team/month/year
  change the user makes.

- RSPL_ExecScheduleTemplate: PK (TeamId, ExecutiveId, Day 1-31) — a fixed,
  reusable "Schedule Template" per explicit request, one-time snapshotted
  (2026-07-31) from Retailware's (TeamId 2) already-completed August 2026
  schedule (558 RSPL_ExecScheduleCell rows across 18 executives, copied
  verbatim: CellText/CellColor/CellTextColor keyed by day-of-month instead
  of an actual CellDate, so the same template row applies to "the 15th"
  regardless of which real year/month it's later copied into). No
  snapshot/regenerate endpoint exists — populating or refreshing this table
  is a deliberate one-off DB operation, not a user-facing action.
  copy_month below now sources from this table instead of from whichever
  month happened to be open when Save As was clicked — see that function's
  own docstring for why. Holiday/Alternate-day "H" values are deliberately
  NOT frozen into this template: they're excluded from what CellText
  normally contains unless a user explicitly typed over one (see get_cells),
  and _holiday_days_for_month below already recomputes the correct holidays
  fresh for whatever target month a copy lands on, so nothing extra is
  needed here for that to keep working.

- RSPL_ExecScheduleHolidayRule: PK ExecutiveId, one row per executive who has
  a standing Weekly-Off/Alternate-Saturday-style pattern (imported one-time
  from D:\\Maruti\\support.xlsx per explicit request — Executive Name matched
  to UserMaster.Name, with 4 near-identical spellings mapped by hand:
  "Sandip/Sandeep Patil", "Mahendra Valunj/Walunj", "Vidya Korde/Korade",
  "Kishor Ahvad/Avhad"). WeeklyOffDay is a weekday name every occurrence of
  which is a holiday every month (e.g. every Sunday). AlternateOccurrences
  ('1,3' or '2,4') + AlternateDay (a weekday name) together mean "the 1st and
  3rd [[or 2nd and 4th]] AlternateDay of the month is also a holiday" — see
  `_holiday_days_for_month` below. These computed "H" values win over
  whatever's saved in RSPL_ExecScheduleCell for that date UNLESS a manual
  override has been saved there — see the two updates below for how "manual
  override" is actually detected, and why.

  Update (2026-08-05): a manual-override exception was added so a saved,
  non-blank CellText would be left alone instead of being replaced by "H"
  (meant for an executive who actually worked a Holiday, e.g. "Office").
  **Reverted (2026-08-07)**: that exception meant ANY previously-saved text
  suppressed the Holiday marker, not just a deliberate override — and
  RSPL_ExecScheduleCell already held pre-existing, mostly-generic values
  (bare "1" shift counts, entered under the pre-08-05 "H always wins"
  policy, before anyone needed to think about a date's holiday status) on
  529 of 535 non-blank August cells for holiday-rule executives, confirmed
  live via LastEditedAt. The exception silently suppressed "H" almost
  everywhere it should have applied, which is a worse outcome than the
  original "silently overwritten Office entry" complaint it was built to
  fix. Rule-always-wins was restored as an intermediate step.

  Update (2026-08-07, later same day): manual override reinstated, per
  explicit request, with the 08-05 version's flaw fixed. The problem was
  never "an override exists" — it's that "any saved text" was too broad a
  definition of "override" and swept up hundreds of pre-existing rows that
  were never a deliberate choice to work through a holiday. This version
  narrows the definition: a cell counts as a deliberate override ONLY if
  its LastEditedAt is on/after _OVERRIDE_ELIGIBLE_FROM (this DB server's
  SYSDATETIME() at the moment this fix shipped, captured once as a fixed
  constant below — NOT "now" recomputed per-request, which would make
  every cell's eligibility silently drift forward on every page load).
  Every real save through save_cells() sets LastEditedAt = SYSDATETIME(),
  so any cell a user actually types into from this point forward is
  correctly detected as an override with zero ongoing maintenance; every
  one of the 535 legacy rows keeps its old LastEditedAt (all but 6 predate
  even the 08-05 attempt), so none of them can retroactively re-trigger
  this bug again. Scoped strictly per (ExecutiveId, CellDate) — saving one
  cell has no effect on any other cell's Holiday/override status, and nothing
  about _holiday_days_for_month, the rule data, or any other overlay changed.
  The Approved-Leave overlay further below is unaffected by any of this — it
  always unconditionally wins over whatever Holiday (override or not) left
  in place, before and after every update above.

Approved-Leave overlay (added per explicit request, no new table — reads
live from the existing web_leaveapplication table, the same one the Leave
Application Register itself uses): for any executive currently on this
team's active roster (only — a leave for someone not in _get_active_executives
is never looked at), an Approved (HOSanctioned=1 OR CEOSanctioned=1, matching
admin_leave.py's own Sanctioned filter) and not-cancelled leave date shows
its color-coded label text (originally a bare "L", see the two updates below
for how this evolved) with the exact same color the Leave Application
Register's own calendar would show for that entry (yellow #ffff00 for an
exact '0.5 (1st Half)' match on the leave's FromDate, red #ff0000 for '0.5
(2nd Half)', lime #00ff00 otherwise — see _get_approved_leave_overlay,
mirroring leave-app-register.ts's toEntry() color logic exactly so both
features agree on the same entry's color). Computed fresh on every get_cells
call, same as holidays — never written into RSPL_ExecScheduleCell — so
approving, modifying, or cancelling a leave is reflected the next time the
schedule loads with no separate sync step, and per explicit request takes
priority over the Holiday/Alternate-day overlay above when a date is both
(applied after it, so its text/color unconditionally win on any overlap).

Update (2026-08-12): a leave date that already carries genuine user-entered
text (e.g. a Customer Visit note) no longer has that text silently replaced
by the leave marker — both are combined, on separate lines, so a Half-Day
Leave and a Half-Day Customer Visit on the same date are both visible at
once. See the "Update (2026-08-12)" comment right above the overlay's
application in get_cells for the full detail on what counts as "genuine
text" (specifically excludes the auto-Holiday "H" marker, which still loses
to Leave outright, unchanged).

Update (2026-08-12, later same day): the overlay's text is no longer a bare
"L" — it now spells out "L (1st Half)"/"L (2nd Half)"/"L (Full Day)" (kept
compact — a longer "1st Half Leave" form was tried first, then shortened per
a follow-up request, since this grid's cells are narrow) so the half is
always readable directly from the cell's text, not just inferred from its
color (see _get_approved_leave_overlay). Determined strictly from the real
ForDays value on web_leaveapplication, same source as
before — never guessed or inferred from screen position.
"""

import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.rights import FORM_EXEC_SCHEDULE, require_access

router = APIRouter(prefix="/support/executive-schedule", tags=["support-executive-schedule"])

# See the "Update (2026-08-07, later same day)" note in the module docstring
# above — a cell only counts as a deliberate Holiday override if it was
# (re)saved at/after this fixed moment (this DB's own SYSDATETIME() when
# the fix shipped), not merely "has any saved text." A fixed constant, not
# datetime.now(): recomputing "now" on every request would make cells edited
# between requests silently flip in and out of eligibility.
_OVERRIDE_ELIGIBLE_FROM = datetime(2026, 8, 7, 11, 36, 49)


class TeamRow(BaseModel):
    team_id: int
    team_code: str
    team_name: str


class ExecutiveRow(BaseModel):
    executive_id: int
    team_id: int
    executive_name: str
    sort_order: int
    row_color: str | None
    column_color: str | None


class ReorderExecutivesRequest(BaseModel):
    team_id: int
    ordered_executive_ids: list[int]


class SetExecutiveColorRequest(BaseModel):
    team_id: int
    color: str | None


class UserStateResponse(BaseModel):
    team_id: int
    year: int
    month: int


class SaveUserStateRequest(BaseModel):
    team_id: int
    year: int
    month: int


class CellRow(BaseModel):
    executive_id: int
    day: int
    text: str
    color: str | None
    text_color: str | None


class CellsResponse(BaseModel):
    executives: list[ExecutiveRow]
    days: list[int]
    cells: list[CellRow]


class SaveCellInput(BaseModel):
    executive_id: int
    day: int
    text: str
    color: str | None
    text_color: str | None


class SaveCellsRequest(BaseModel):
    team_id: int
    year: int
    month: int
    cells: list[SaveCellInput]


class CopyMonthRequest(BaseModel):
    team_id: int
    source_year: int
    source_month: int
    target_year: int
    target_month: int


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class HolidayRule(BaseModel):
    executive_id: int
    weekly_off_day: str
    alternate_occurrences: str
    alternate_day: str


def _get_holiday_rules(executive_ids: list[int]) -> dict[int, HolidayRule]:
    if not executive_ids:
        return {}
    placeholders = ",".join("?" for _ in executive_ids)
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT ExecutiveId, WeeklyOffDay, AlternateOccurrences, AlternateDay "
            f"FROM RSPL_ExecScheduleHolidayRule WHERE ExecutiveId IN ({placeholders})",
            *executive_ids,
        )
        rows = rows_to_dicts(cursor)
    return {
        r["ExecutiveId"]: HolidayRule(
            executive_id=r["ExecutiveId"], weekly_off_day=r["WeeklyOffDay"],
            alternate_occurrences=r["AlternateOccurrences"], alternate_day=r["AlternateDay"],
        )
        for r in rows
    }


def _holiday_days_for_month(rule: HolidayRule, year: int, month: int, days_in_month: int) -> set[int]:
    """Every occurrence of WeeklyOffDay in the month, plus whichever occurrences
    (1st/3rd or 2nd/4th, per AlternateOccurrences) of AlternateDay the rule names."""
    weekly_off_idx = _WEEKDAY_INDEX.get(rule.weekly_off_day.strip().lower())
    alt_idx = _WEEKDAY_INDEX.get(rule.alternate_day.strip().lower())
    wanted_occurrences = {int(n) for n in rule.alternate_occurrences.split(",") if n.strip().isdigit()}

    holidays: set[int] = set()
    alt_occurrence = 0
    for day in range(1, days_in_month + 1):
        weekday = date(year, month, day).weekday()
        if weekly_off_idx is not None and weekday == weekly_off_idx:
            holidays.add(day)
        if alt_idx is not None and weekday == alt_idx:
            alt_occurrence += 1
            if alt_occurrence in wanted_occurrences:
                holidays.add(day)
    return holidays


def _get_approved_leave_overlay(
    executive_ids: list[int], year: int, month: int, days_in_month: int
) -> dict[tuple[int, int], tuple[str, str]]:
    """Approved-leave overlay for the Executive Schedule — see this module's
    top-of-file docstring for the full rationale. Returns {(executive_id, day):
    (label, color)}, computed fresh every call. `color` exactly mirrors
    leave-app-register.ts's toEntry() so an entry shows the same color here
    as it does in the Leave Application Register itself.

    Update (2026-08-12): `label` spells out "L (1st Half)"/"L (2nd Half)"/
    "L (Full Day)" — replaces the old bare single-letter "L" marker (which
    relied on color alone to distinguish 1st vs 2nd half), per explicit
    request that the half be readable from the cell's text itself, not
    inferred from color/position. Kept compact (not "1st Half Leave" etc,
    tried first and shortened per follow-up request — this grid's cells are
    narrow). Determined strictly from ForDays (the real stored leave data —
    see the "0.5 (1st Half)"/"0.5 (2nd Half)" checks below), never guessed."""
    if not executive_ids:
        return {}
    placeholders = ",".join("?" for _ in executive_ids)
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
            *executive_ids, last_day, first_day,
        )
        rows = rows_to_dicts(cursor)

    overlay: dict[tuple[int, int], tuple[str, str]] = {}
    for r in rows:
        executive_id = r["Userid"]
        from_date, to_date = r["FromDate"], r["ToDate"]
        for_days = r["ForDays"] or ""
        current = max(from_date, first_day)
        span_end = min(to_date, last_day)
        while current <= span_end:
            # The "0.5 (1st Half)"/"0.5 (2nd Half)" ForDays values only ever
            # describe a single-day leave in real data (from_date == to_date
            # — a half day can't span multiple calendar days) — the
            # is_from_day guard is defensive: it only matters for a
            # theoretical multi-day span, where every day past the first
            # falls through to Full Day rather than misreading a half-day
            # marker onto a date it was never about.
            #
            # Update (2026-08-12): shortened from "1st Half Leave"/"2nd Half
            # Leave"/"Full Day Leave" to "L (1st Half)"/"L (2nd Half)"/"L
            # (Full Day)" per explicit request — the longer form didn't fit
            # this grid's narrow cells well. "L" keeps this system's existing
            # single-letter Leave convention; the half is still always
            # stated in words, never left to color alone.
            #
            # Update (2026-08-12, later still): Full Day reverted to the
            # bare "L" (original convention, color alone signals it's a
            # full-day leave via lime #00ff00) per explicit follow-up
            # request — only the two HALF-day labels need to spell out which
            # half in words; a plain full-day leave doesn't need a
            # "(Full Day)" qualifier stated as text.
            is_from_day = current == from_date
            if is_from_day and for_days == "0.5 (1st Half)":
                color, label = "#ffff00", "L (1st Half)"
            elif is_from_day and for_days == "0.5 (2nd Half)":
                color, label = "#ff0000", "L (2nd Half)"
            else:
                color, label = "#00ff00", "L"
            overlay[(executive_id, current.day)] = (label, color)
            current += timedelta(days=1)
    return overlay


def _is_bare_number(text: str) -> bool:
    """Mirrors executive-schedule.ts's parseCellNumber — True if `text`, as a
    whole, parses as a plain number (the per-day installation/duty count
    convention this grid already uses for the Total/Installation Capacity
    columns). Used by get_cells to keep a duty count from ever being
    combined with a leave marker (see the call site's own comment) —
    intentionally the exact same "is this a number" definition the frontend
    Total calculation already uses, so nothing about what counts as a
    number for the totals is redefined here, only reused."""
    try:
        float(text.strip())
        return True
    except ValueError:
        return False


def _is_redundant_half_day_note(manual_text: str, label: str) -> bool:
    """True if `manual_text` (an existing manually-saved cell note) adds no
    real information beyond what the half-day leave `label` (e.g.
    "L (2nd Half)") already states. Used by get_cells to decide whether to
    drop a manual note instead of combining it, so a half-day leave doesn't
    show the same fact twice. Only meaningful for a half-day label —
    callers must check "Half" in label themselves.

    Covers three shapes, all confirmed to occur with real data:
    1. A bare "L" — some cells still carry this from before the automatic
       overlay existed, when a user typed "L" by hand to mean Leave.
    2. A note that's just restating "1st half"/"2nd half"/"half day" in
       different casing/spacing (e.g. "2nd half").
    3. The note IS the overlay's own label, byte-for-byte apart from
       whitespace (e.g. "L (2nd Half)\\n") — happens when a user
       double-clicks a leave cell (which shows the computed label in the
       textarea) and blurs after only adding trailing whitespace/a newline,
       which is enough for onCellBlur's `original !== cell.text` check to
       treat it as a real edit and persist it — confirmed live, see the
       2026-08-12 update in this module's docstring. Comparing against the
       label directly (not just a fixed phrase list) catches this and any
       future case shaped the same way."""
    normalized = re.sub(r"[^a-z0-9]+", " ", manual_text.strip().lower()).strip()
    if normalized in ("l", "half day", "half"):
        return True
    if normalized == re.sub(r"[^a-z0-9]+", " ", label.strip().lower()).strip():
        return True
    # The specific half this label names, e.g. "L (2nd Half)" -> "2nd half"
    # -> normalized "2nd half" — matches a note that just repeats it.
    match = re.search(r"\((1st|2nd) half\)", label, re.IGNORECASE)
    if match:
        half_phrase = f"{match.group(1)} half".lower()
        if normalized == half_phrase:
            return True
    return False


@router.get("/teams", response_model=list[TeamRow])
def get_teams(current_user: CurrentUser = Depends(get_current_user)) -> list[TeamRow]:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "view")
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TeamId, TeamCode, TeamName FROM RSPL_ExecScheduleTeam WHERE Enabled = 1 ORDER BY SortOrder"
        )
        rows = rows_to_dicts(cursor)
    return [TeamRow(team_id=r["TeamId"], team_code=r["TeamCode"], team_name=r["TeamName"]) for r in rows]


@router.get("/user-state", response_model=UserStateResponse | None)
def get_user_state(current_user: CurrentUser = Depends(get_current_user)) -> UserStateResponse | None:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "view")
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TeamId, SelectedYear, SelectedMonth FROM RSPL_ExecScheduleUserState WHERE UserId = ?",
            current_user.user_id,
        )
        row = cursor.fetchone()
    if not row:
        return None
    return UserStateResponse(team_id=row[0], year=row[1], month=row[2])


@router.put("/user-state")
def save_user_state(body: SaveUserStateRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    # Just-viewing users still get to have their last-open team/month
    # remembered — this isn't schedule data, so it only needs "view".
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "view")
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_ExecScheduleUserState SET TeamId = ?, SelectedYear = ?, SelectedMonth = ?, LastEditedAt = SYSDATETIME() "
            "WHERE UserId = ?",
            body.team_id, body.year, body.month, current_user.user_id,
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "INSERT INTO RSPL_ExecScheduleUserState (UserId, TeamId, SelectedYear, SelectedMonth, LastEditedAt) "
                "VALUES (?, ?, ?, ?, SYSDATETIME())",
                current_user.user_id, body.team_id, body.year, body.month,
            )
    return {"success": True}


def _get_active_executives(team_id: int) -> list[ExecutiveRow]:
    # Roster comes straight from UserMaster now, scoped to whichever
    # UserTypeID this team maps to (8 = Jwellsoft, 9 = Retailware) — no
    # separate executive master to maintain. A user appears here the moment
    # they exist in UserMaster with that UserTypeID and Enabled=1, and drops
    # out the moment they're disabled there; nothing in this feature needs
    # to be told about it separately.
    #
    # DISTINCT on Name is a defensive dedupe only (per explicit "ensure no
    # duplicate Executive names are displayed" requirement) — today's real
    # data has no same-team name collisions, but if two active UserMaster
    # rows of the same UserTypeID ever did share a Name, ROW_NUMBER keeps
    # just the lowest UserID of the two rather than showing both.
    #
    # SortOrder/RowColor come from the LEFT JOINed per-team meta row, which
    # may not exist yet for a given executive (brand new, or simply never
    # dragged/colored) — ORDER BY puts every row WITH an explicit SortOrder
    # first (in that order), then everyone else alphabetically after, which
    # is exactly "new executives appear at the end by default" with no
    # separate bookkeeping needed.
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name, SortOrder, RowColor, ColumnColor FROM ("
            "  SELECT u.UserID, u.Name, m.SortOrder, m.RowColor, m.ColumnColor,"
            "         ROW_NUMBER() OVER (PARTITION BY u.Name ORDER BY u.UserID) AS rn"
            "  FROM UserMaster u"
            "  LEFT JOIN RSPL_ExecScheduleExecutiveMeta m ON m.TeamId = ? AND m.ExecutiveId = u.UserID"
            "  WHERE u.Enabled = 1 AND u.UserTypeID = ("
            "    SELECT UserTypeID FROM RSPL_ExecScheduleTeam WHERE TeamId = ?"
            "  )"
            ") AS deduped"
            " WHERE rn = 1"
            " ORDER BY CASE WHEN SortOrder IS NULL THEN 1 ELSE 0 END, SortOrder, Name",
            team_id, team_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ExecutiveRow(
            executive_id=r["UserID"], team_id=team_id, executive_name=r["Name"] or "", sort_order=index,
            row_color=r["RowColor"], column_color=r["ColumnColor"],
        )
        for index, r in enumerate(rows, start=1)
    ]


@router.get("/executives", response_model=list[ExecutiveRow])
def get_executives(team_id: int, current_user: CurrentUser = Depends(get_current_user)) -> list[ExecutiveRow]:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "view")
    return _get_active_executives(team_id)


@router.post("/executives/reorder")
def reorder_executives(body: ReorderExecutivesRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "edit")
    current = _get_active_executives(body.team_id)
    current_ids = {e.executive_id for e in current}
    if set(body.ordered_executive_ids) != current_ids or len(body.ordered_executive_ids) != len(current_ids):
        raise HTTPException(status_code=400, detail="ordered_executive_ids must match this team's current active executive IDs exactly")

    with get_cursor() as cursor:
        for index, executive_id in enumerate(body.ordered_executive_ids, start=1):
            cursor.execute(
                "UPDATE RSPL_ExecScheduleExecutiveMeta SET SortOrder = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
                "WHERE TeamId = ? AND ExecutiveId = ?",
                index, current_user.user_id, body.team_id, executive_id,
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO RSPL_ExecScheduleExecutiveMeta (TeamId, ExecutiveId, SortOrder, LastEditedByUserId, LastEditedAt) "
                    "VALUES (?, ?, ?, ?, SYSDATETIME())",
                    body.team_id, executive_id, index, current_user.user_id,
                )
    return {"success": True}


@router.put("/executives/{executive_id}/color")
def set_executive_color(executive_id: int, body: SetExecutiveColorRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "edit")
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_ExecScheduleExecutiveMeta SET RowColor = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE TeamId = ? AND ExecutiveId = ?",
            body.color, current_user.user_id, body.team_id, executive_id,
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "INSERT INTO RSPL_ExecScheduleExecutiveMeta (TeamId, ExecutiveId, RowColor, LastEditedByUserId, LastEditedAt) "
                "VALUES (?, ?, ?, ?, SYSDATETIME())",
                body.team_id, executive_id, body.color, current_user.user_id,
            )
    return {"success": True}


# Mirrors set_executive_color above exactly, but writes ColumnColor instead
# of RowColor — an independent background just for this executive's own
# Executive (name) column cell, per explicit request that Row Color and
# Column Color work independently and never interfere with each other.
@router.put("/executives/{executive_id}/column-color")
def set_executive_column_color(executive_id: int, body: SetExecutiveColorRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "edit")
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_ExecScheduleExecutiveMeta SET ColumnColor = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE TeamId = ? AND ExecutiveId = ?",
            body.color, current_user.user_id, body.team_id, executive_id,
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "INSERT INTO RSPL_ExecScheduleExecutiveMeta (TeamId, ExecutiveId, ColumnColor, LastEditedByUserId, LastEditedAt) "
                "VALUES (?, ?, ?, ?, SYSDATETIME())",
                body.team_id, executive_id, body.color, current_user.user_id,
            )
    return {"success": True}


@router.get("/cells", response_model=CellsResponse)
def get_cells(team_id: int, year: int, month: int, current_user: CurrentUser = Depends(get_current_user)) -> CellsResponse:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "view")
    executives = _get_active_executives(team_id)
    days = list(range(1, _days_in_month(year, month) + 1))
    if not executives:
        return CellsResponse(executives=executives, days=days, cells=[])

    executive_ids = [e.executive_id for e in executives]
    placeholders = ",".join("?" for _ in executive_ids)
    first_day = date(year, month, 1)
    last_day = date(year, month, days[-1])

    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT ExecutiveId, CellDate, CellText, CellColor, CellTextColor, LastEditedAt FROM RSPL_ExecScheduleCell "
            f"WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *executive_ids, first_day, last_day,
        )
        rows = rows_to_dicts(cursor)

    cells_by_key: dict[tuple[int, int], CellRow] = {
        (r["ExecutiveId"], r["CellDate"].day): CellRow(
            executive_id=r["ExecutiveId"], day=r["CellDate"].day, text=r["CellText"] or "",
            color=r["CellColor"], text_color=r["CellTextColor"],
        )
        for r in rows
    }
    # A cell counts as a deliberate Holiday override only if it was actually
    # (re)saved at/after _OVERRIDE_ELIGIBLE_FROM — see that constant's
    # comment and the module docstring's 2026-08-07 update for why "has any
    # saved text" alone (the 08-05 version's rule) is NOT used here: it
    # swept up hundreds of pre-existing rows that were never a deliberate
    # choice to work through a holiday. Kept as a separate lookup (not a
    # CellRow field) since LastEditedAt is purely an internal eligibility
    # check, not part of this endpoint's public response shape.
    override_eligible = {
        (r["ExecutiveId"], r["CellDate"].day)
        for r in rows
        if r["LastEditedAt"] and r["LastEditedAt"] >= _OVERRIDE_ELIGIBLE_FROM
    }

    # Holiday/Alternate-day rules win over whatever's saved for that date —
    # UNLESS that specific cell is a deliberate override (in override_eligible
    # above). This only overrides the CellRow.text returned here, never the
    # underlying RSPL_ExecScheduleCell row itself, so no manual data is ever
    # destroyed, just visually superseded on load when no override applies.
    #
    # Box color: an "H" cell defaults to White rather than falling through to
    # the row's own background color — but only when no color has ever been
    # explicitly saved for that date (existing.color falsy). The moment a
    # user picks a color for that box via the color picker, it gets saved to
    # RSPL_ExecScheduleCell and existing.color is no longer falsy, so this
    # leaves their choice alone on every later load instead of resetting it
    # back to white each time.
    # Tracks which cells got their "H" from THIS loop (an automatic marker,
    # not something a user actually typed) — see the Approved-Leave overlay
    # below, which needs to tell "this cell's text is just the auto-Holiday
    # marker" (safe to fully replace, unchanged existing behavior) apart from
    # "this cell has a real user-entered note, e.g. a Customer Visit" (must
    # be preserved alongside Leave, not silently destroyed by it — see
    # below). Not added to when the override_eligible `continue` fires,
    # since that path deliberately leaves genuine saved text in place.
    holiday_auto_days: set[tuple[int, int]] = set()
    holiday_rules = _get_holiday_rules(executive_ids)
    for executive_id, rule in holiday_rules.items():
        for day in _holiday_days_for_month(rule, year, month, len(days)):
            key = (executive_id, day)
            existing = cells_by_key.get(key)
            if existing and existing.text and key in override_eligible:
                continue
            if existing:
                existing.text = "H"
                if not existing.color:
                    existing.color = "#ffffff"
            else:
                cells_by_key[key] = CellRow(executive_id=executive_id, day=day, text="H", color="#ffffff", text_color=None)
            holiday_auto_days.add(key)

    # Approved-Leave overlay — applied AFTER Holiday above, per explicit
    # request that Leave wins on any date that's both (this loop runs last).
    # Unlike Holiday's color (a neutral white default, only applied if no
    # color was ever explicitly saved), Leave's color IS the information
    # this feature exists to show, so it's set unconditionally — otherwise
    # a cell already colored for unrelated reasons would silently hide an
    # approved leave.
    #
    # Update (2026-08-12): per explicit request, a cell that ALSO carries
    # genuine user-entered text (most commonly a Customer Visit note typed
    # in by a support user) no longer has that text silently overwritten by
    # the leave marker — both are combined so a Half-Day Leave and a
    # Half-Day Customer Visit on the same date are both visible at once,
    # neither hiding the other. "Genuine user-entered text" specifically
    # excludes the auto-Holiday "H" marker just set above (holiday_auto_days)
    # — Leave still wins outright over an auto-Holiday day exactly as
    # before this change, since "H" isn't something a user actually wrote.
    # The Total/Installation Capacity calculation is unaffected either way:
    # it only ever counted a cell whose ENTIRE text was a bare number (see
    # parseCellNumber in executive-schedule.ts), so a combined
    # "L (1st Half)\n<note>" value contributes 0 exactly like the old
    # bare "L" already did — no behavior change there.
    #
    # Update (2026-08-12, later same day): the overlay's own text (`label`
    # below) now always spells out "L (1st Half)"/"L (2nd Half)"/"L (Full
    # Day)" — see _get_approved_leave_overlay's own docstring — instead of
    # the old single-letter "L" that relied on color alone to distinguish
    # which half. Applies uniformly whether or not the cell also
    # has a combined note, so the half is always readable from the text
    # itself, never left to be inferred from color/position.
    leave_overlay = _get_approved_leave_overlay(executive_ids, year, month, len(days))
    for (executive_id, day), (label, color) in leave_overlay.items():
        key = (executive_id, day)
        existing = cells_by_key.get(key)
        manual_text = (existing.text or "").strip() if existing and key not in holiday_auto_days else ""
        # Update (2026-08-12, later still): per explicit request, a bare
        # number (e.g. "1", "4" — the per-day installation/duty count used
        # only for the Total/Installation Capacity columns, see
        # parseCellNumber in executive-schedule.ts) is never combined with a
        # leave marker, for BOTH full-day and half-day leave. The Leave
        # Register never shows a duty count next to a leave entry, so this
        # grid shouldn't either — a cell with an approved leave should show
        # ONLY the leave entry (plus any genuine Customer Visit note, still
        # combined exactly as before — this only strips numbers, nothing
        # else). Applied before the half-day-specific check below since it's
        # unconditional across both leave types.
        if manual_text and _is_bare_number(manual_text):
            manual_text = ""
        # Update (2026-08-12, later still): per explicit request, scoped to
        # HALF-DAY leave only (label contains "Half") — pre-existing manual
        # text that adds no real information beyond what the overlay's own
        # "L (1st Half)"/"L (2nd Half)" label already states (a bare "L", or
        # a note that's just restating "1st half"/"2nd half"/"half day" in
        # different casing/spacing) is dropped instead of being appended as
        # a redundant-looking second line — see _is_redundant_half_day_note.
        # Full-day leave is deliberately left exactly as it already was —
        # including a bare "L" manual note, which still combines as
        # "L\nL" — per explicit instruction not to touch that logic.
        is_half_day = "Half" in label
        if is_half_day and manual_text and _is_redundant_half_day_note(manual_text, label):
            manual_text = ""
        if manual_text:
            existing.text = f"{label}\n{manual_text}"
            existing.color = color
        elif existing:
            existing.text = label
            existing.color = color
        else:
            cells_by_key[key] = CellRow(executive_id=executive_id, day=day, text=label, color=color, text_color=None)

    return CellsResponse(executives=executives, days=days, cells=list(cells_by_key.values()))


@router.post("/cells/save")
def save_cells(body: SaveCellsRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "edit")
    with get_cursor() as cursor:
        for cell in body.cells:
            cell_date = date(body.year, body.month, cell.day)
            cursor.execute(
                "UPDATE RSPL_ExecScheduleCell SET CellText = ?, CellColor = ?, CellTextColor = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
                "WHERE ExecutiveId = ? AND CellDate = ?",
                cell.text, cell.color, cell.text_color, current_user.user_id, cell.executive_id, cell_date,
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO RSPL_ExecScheduleCell (ExecutiveId, CellDate, CellText, CellColor, CellTextColor, LastEditedByUserId, LastEditedAt) "
                    "VALUES (?, ?, ?, ?, ?, ?, SYSDATETIME())",
                    cell.executive_id, cell_date, cell.text, cell.color, cell.text_color, current_user.user_id,
                )
    return {"success": True, "saved_count": len(body.cells)}


@router.post("/copy-month")
def copy_month(body: CopyMonthRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    """"Save As" / "Save as Copy for Selected Month" — per explicit request,
    this now always copies from the team's stored RSPL_ExecScheduleTemplate
    snapshot rather than from whichever month happened to be open in the
    grid when the button was clicked. The request shape, the same-month
    guard, and the target-month overwrite behavior (confirmed client-side
    via a confirm() dialog, then a full delete-then-insert here) are all
    otherwise unchanged from before — only the SELECT's source table
    changed, from RSPL_ExecScheduleCell (dynamic, whatever source_year/
    source_month were) to RSPL_ExecScheduleTemplate (fixed, per-team,
    keyed by day-of-month rather than an actual date). source_year/
    source_month are kept on the request purely for the equality guard
    below (still meaningful — it still stops "target == the month I'm
    currently viewing", now framed as "reapplying the template over the
    month I'm already on", not because source data comes from there)."""
    require_access(current_user.user_id, FORM_EXEC_SCHEDULE, "edit")
    if (body.source_year, body.source_month) == (body.target_year, body.target_month):
        raise HTTPException(status_code=400, detail="source and target month must differ")

    executives = _get_active_executives(body.team_id)
    if not executives:
        return {"success": True, "copied_count": 0}
    executive_ids = [e.executive_id for e in executives]
    placeholders = ",".join("?" for _ in executive_ids)

    target_days_count = _days_in_month(body.target_year, body.target_month)

    with get_cursor() as cursor:
        # Clear any existing target-month cells for this team first — Save
        # As is framed as an intentional "start this month from the
        # template" overwrite, confirmed via a confirm dialog client-side.
        target_first = date(body.target_year, body.target_month, 1)
        target_last = date(body.target_year, body.target_month, target_days_count)
        cursor.execute(
            f"DELETE FROM RSPL_ExecScheduleCell WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *executive_ids, target_first, target_last,
        )

        cursor.execute(
            f"SELECT ExecutiveId, Day, CellText, CellColor, CellTextColor FROM RSPL_ExecScheduleTemplate "
            f"WHERE TeamId = ? AND ExecutiveId IN ({placeholders})",
            body.team_id, *executive_ids,
        )
        template_rows = rows_to_dicts(cursor)

        copied = 0
        for r in template_rows:
            day = r["Day"]
            # A day that doesn't exist in the target month (e.g. the 31st
            # copied into a 30-day month) is skipped, not coerced.
            if day > target_days_count:
                continue
            target_date = date(body.target_year, body.target_month, day)
            cursor.execute(
                "INSERT INTO RSPL_ExecScheduleCell (ExecutiveId, CellDate, CellText, CellColor, CellTextColor, LastEditedByUserId, LastEditedAt) "
                "VALUES (?, ?, ?, ?, ?, ?, SYSDATETIME())",
                r["ExecutiveId"], target_date, r["CellText"], r["CellColor"], r["CellTextColor"], current_user.user_id,
            )
            copied += 1

    return {"success": True, "copied_count": copied}
