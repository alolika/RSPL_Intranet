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
  `_holiday_days_for_month` below. Per explicit request, these computed "H"
  values always win over whatever's saved in RSPL_ExecScheduleCell for that
  date — get_cells overlays them on top of the saved text rather than only
  filling gaps, so a holiday/alternate date always shows "H" regardless of
  any manual entry, without touching the underlying saved CellText.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.rights import FORM_EXEC_SCHEDULE, require_access

router = APIRouter(prefix="/support/executive-schedule", tags=["support-executive-schedule"])


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
            f"SELECT ExecutiveId, CellDate, CellText, CellColor, CellTextColor FROM RSPL_ExecScheduleCell "
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

    # Holiday/Alternate-day rules always win over whatever's saved for that
    # date (per explicit request) — this only overrides the CellRow.text
    # returned here, never the underlying RSPL_ExecScheduleCell row itself,
    # so no manual data is destroyed, just visually superseded on load.
    #
    # Box color: an "H" cell defaults to White rather than falling through to
    # the row's own background color — but only when no color has ever been
    # explicitly saved for that date (existing.color falsy). The moment a
    # user picks a color for that box via the color picker, it gets saved to
    # RSPL_ExecScheduleCell and existing.color is no longer falsy, so this
    # leaves their choice alone on every later load instead of resetting it
    # back to white each time.
    holiday_rules = _get_holiday_rules(executive_ids)
    for executive_id, rule in holiday_rules.items():
        for day in _holiday_days_for_month(rule, year, month, len(days)):
            key = (executive_id, day)
            existing = cells_by_key.get(key)
            if existing:
                existing.text = "H"
                if not existing.color:
                    existing.color = "#ffffff"
            else:
                cells_by_key[key] = CellRow(executive_id=executive_id, day=day, text="H", color="#ffffff", text_color=None)

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
