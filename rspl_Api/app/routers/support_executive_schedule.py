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
  UserMaster), but two things about an executive's presentation on a given
  team's grid are still genuinely user-editable and must persist: manual
  row order (drag/move up/down) and a per-row background color. PK
  (TeamId, ExecutiveId) — deliberately scoped per-team, not just per
  executive, since "load sequence separately for Retailware and Jwellsoft"
  was explicit, and it means an executive who ever moved between teams
  (UserTypeID changed) doesn't drag a stale order/color across with them.
  A row with no meta entry yet (a brand-new UserMaster user, or one who's
  simply never been dragged/colored) sorts after every row that DOES have
  an explicit SortOrder, then alphabetically among itself — see
  _get_active_executives' ORDER BY. That's what makes "new executives
  appear at the end by default" true without any extra bookkeeping.

- RSPL_ExecScheduleUserState: one row per user (PK UserId), remembering
  which Team/Year/Month they last had open. Per explicit request that
  reopening the form — on any device, after any restart — must land back
  exactly where the user left off, not just "whichever team happened to be
  in this one browser's localStorage" (the previous mechanism, now
  replaced by this). Read on mount, written after every team/month/year
  change the user makes.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

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


@router.get("/teams", response_model=list[TeamRow])
def get_teams() -> list[TeamRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TeamId, TeamCode, TeamName FROM RSPL_ExecScheduleTeam WHERE Enabled = 1 ORDER BY SortOrder"
        )
        rows = rows_to_dicts(cursor)
    return [TeamRow(team_id=r["TeamId"], team_code=r["TeamCode"], team_name=r["TeamName"]) for r in rows]


@router.get("/user-state", response_model=UserStateResponse | None)
def get_user_state(current_user: CurrentUser = Depends(get_current_user)) -> UserStateResponse | None:
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
            "SELECT UserID, Name, SortOrder, RowColor FROM ("
            "  SELECT u.UserID, u.Name, m.SortOrder, m.RowColor,"
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
            executive_id=r["UserID"], team_id=team_id, executive_name=r["Name"] or "", sort_order=index, row_color=r["RowColor"]
        )
        for index, r in enumerate(rows, start=1)
    ]


@router.get("/executives", response_model=list[ExecutiveRow])
def get_executives(team_id: int) -> list[ExecutiveRow]:
    return _get_active_executives(team_id)


@router.post("/executives/reorder")
def reorder_executives(body: ReorderExecutivesRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
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


@router.get("/cells", response_model=CellsResponse)
def get_cells(team_id: int, year: int, month: int) -> CellsResponse:
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

    cells = [
        CellRow(
            executive_id=r["ExecutiveId"], day=r["CellDate"].day, text=r["CellText"] or "",
            color=r["CellColor"], text_color=r["CellTextColor"],
        )
        for r in rows
    ]
    return CellsResponse(executives=executives, days=days, cells=cells)


@router.post("/cells/save")
def save_cells(body: SaveCellsRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
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
    if (body.source_year, body.source_month) == (body.target_year, body.target_month):
        raise HTTPException(status_code=400, detail="source and target month must differ")

    executives = _get_active_executives(body.team_id)
    if not executives:
        return {"success": True, "copied_count": 0}
    executive_ids = [e.executive_id for e in executives]
    placeholders = ",".join("?" for _ in executive_ids)

    source_days = _days_in_month(body.source_year, body.source_month)
    target_days_count = _days_in_month(body.target_year, body.target_month)
    source_first = date(body.source_year, body.source_month, 1)
    source_last = date(body.source_year, body.source_month, source_days)

    with get_cursor() as cursor:
        # Clear any existing target-month cells for this team first — Save
        # As is framed as an intentional "start this month from last month"
        # overwrite, confirmed via a confirm dialog client-side.
        target_first = date(body.target_year, body.target_month, 1)
        target_last = date(body.target_year, body.target_month, target_days_count)
        cursor.execute(
            f"DELETE FROM RSPL_ExecScheduleCell WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *executive_ids, target_first, target_last,
        )

        cursor.execute(
            f"SELECT ExecutiveId, CellDate, CellText, CellColor, CellTextColor FROM RSPL_ExecScheduleCell "
            f"WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *executive_ids, source_first, source_last,
        )
        source_rows = rows_to_dicts(cursor)

        copied = 0
        for r in source_rows:
            day = r["CellDate"].day
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
