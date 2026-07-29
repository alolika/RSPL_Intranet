"""Executive Schedule — a brand-new, database-backed feature with no legacy
ASP.NET/VB source page to mirror (unlike most of this migration). Replaces
the retired `support_schedule.py` (a read-only display of a Power Automate ->
Outlook -> Schedule.xlsx snapshot, itself standing in for a defunct Google
Sheets integration). That entire import pipeline has been decommissioned —
data now lives here and is edited directly in the Angular app.

Schema (hand-created directly on the SQL Server instance, confirmed live —
no migration tooling exists in this repo):

- RSPL_ExecScheduleTeam: fixed lookup, seeded with exactly two rows
  ('Jwellsoft', 'Retailware'). No "add team" endpoint in v1.
- RSPL_ExecScheduleExecutive: one row per executive, scoped to a team,
  ordered by SortOrder. Delete is a soft-delete (Enabled=0) since historical
  RSPL_ExecScheduleCell rows reference ExecutiveId and a hard delete would
  orphan/lose past months' data for someone who left the team.
- RSPL_ExecScheduleCell: normalized (ExecutiveId, CellDate) -> (text, color)
  store, PK (ExecutiveId, CellDate). Deliberately not one column per day
  (unbounded/unmaintainable) and deliberately not tied to Dim_Time (only
  populated 2004-2015, see general_schedule.py) - CellDate is a plain date.
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


class AddExecutiveRequest(BaseModel):
    team_id: int
    executive_name: str


class RenameExecutiveRequest(BaseModel):
    executive_name: str


class ReorderExecutivesRequest(BaseModel):
    team_id: int
    ordered_executive_ids: list[int]


class CellRow(BaseModel):
    executive_id: int
    day: int
    text: str
    color: str | None


class CellsResponse(BaseModel):
    executives: list[ExecutiveRow]
    days: list[int]
    cells: list[CellRow]


class SaveCellInput(BaseModel):
    executive_id: int
    day: int
    text: str
    color: str | None


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


def _get_active_executives(team_id: int) -> list[ExecutiveRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ExecutiveId, TeamId, ExecutiveName, SortOrder FROM RSPL_ExecScheduleExecutive "
            "WHERE TeamId = ? AND Enabled = 1 ORDER BY SortOrder",
            team_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ExecutiveRow(executive_id=r["ExecutiveId"], team_id=r["TeamId"], executive_name=r["ExecutiveName"], sort_order=r["SortOrder"])
        for r in rows
    ]


@router.get("/executives", response_model=list[ExecutiveRow])
def get_executives(team_id: int) -> list[ExecutiveRow]:
    return _get_active_executives(team_id)


def _find_duplicate_active_name(cursor, team_id: int, name: str, exclude_executive_id: int | None = None) -> bool:
    # Case-insensitive, same-team, active-only match — soft-deleted
    # (Enabled=0) executives don't block reuse of their old name.
    if exclude_executive_id is None:
        cursor.execute(
            "SELECT COUNT(*) FROM RSPL_ExecScheduleExecutive WHERE TeamId = ? AND Enabled = 1 AND LOWER(ExecutiveName) = LOWER(?)",
            team_id, name,
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM RSPL_ExecScheduleExecutive WHERE TeamId = ? AND Enabled = 1 AND ExecutiveId <> ? AND LOWER(ExecutiveName) = LOWER(?)",
            team_id, exclude_executive_id, name,
        )
    return cursor.fetchone()[0] > 0


@router.post("/executives", response_model=ExecutiveRow)
def add_executive(body: AddExecutiveRequest, current_user: CurrentUser = Depends(get_current_user)) -> ExecutiveRow:
    name = body.executive_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Executive name is required")
    with get_cursor() as cursor:
        if _find_duplicate_active_name(cursor, body.team_id, name):
            raise HTTPException(status_code=409, detail=f"An executive named '{name}' already exists in this team")
        cursor.execute(
            "SELECT ISNULL(MAX(SortOrder), 0) + 1 FROM RSPL_ExecScheduleExecutive WHERE TeamId = ?", body.team_id
        )
        next_sort_order = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO RSPL_ExecScheduleExecutive (TeamId, ExecutiveName, SortOrder, Enabled, CreatedByUserId, CreatedAt) "
            "OUTPUT INSERTED.ExecutiveId VALUES (?, ?, ?, 1, ?, SYSDATETIME())",
            body.team_id, name, next_sort_order, current_user.user_id,
        )
        new_id = cursor.fetchone()[0]
    return ExecutiveRow(executive_id=new_id, team_id=body.team_id, executive_name=name, sort_order=next_sort_order)


@router.put("/executives/{executive_id}")
def rename_executive(executive_id: int, body: RenameExecutiveRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    name = body.executive_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Executive name is required")
    with get_cursor() as cursor:
        cursor.execute("SELECT TeamId FROM RSPL_ExecScheduleExecutive WHERE ExecutiveId = ?", executive_id)
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Executive not found")
        team_id = row[0]
        if _find_duplicate_active_name(cursor, team_id, name, exclude_executive_id=executive_id):
            raise HTTPException(status_code=409, detail=f"An executive named '{name}' already exists in this team")
        cursor.execute(
            "UPDATE RSPL_ExecScheduleExecutive SET ExecutiveName = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE ExecutiveId = ?",
            name, current_user.user_id, executive_id,
        )
    return {"success": True}


@router.delete("/executives/{executive_id}")
def delete_executive(executive_id: int, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_ExecScheduleExecutive SET Enabled = 0, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE ExecutiveId = ?",
            current_user.user_id, executive_id,
        )
    return {"success": True}


@router.post("/executives/{executive_id}/restore")
def restore_executive(executive_id: int, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    # Undo of a delete — re-enables the same soft-deleted row (and therefore
    # its historical cells) rather than creating a new executive.
    with get_cursor() as cursor:
        cursor.execute("SELECT TeamId, ExecutiveName FROM RSPL_ExecScheduleExecutive WHERE ExecutiveId = ?", executive_id)
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Executive not found")
        team_id, name = row
        # A newer active executive may have since taken this same name in
        # this team — restoring would otherwise create a second active
        # row with an identical name.
        if _find_duplicate_active_name(cursor, team_id, name, exclude_executive_id=executive_id):
            raise HTTPException(status_code=409, detail=f"Cannot restore — an executive named '{name}' already exists in this team")
        cursor.execute(
            "UPDATE RSPL_ExecScheduleExecutive SET Enabled = 1, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE ExecutiveId = ?",
            current_user.user_id, executive_id,
        )
    return {"success": True}


@router.post("/executives/reorder")
def reorder_executives(body: ReorderExecutivesRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    current = _get_active_executives(body.team_id)
    current_ids = {e.executive_id for e in current}
    if set(body.ordered_executive_ids) != current_ids or len(body.ordered_executive_ids) != len(current_ids):
        raise HTTPException(status_code=400, detail="ordered_executive_ids must match this team's current active executive IDs exactly")

    with get_cursor() as cursor:
        for index, executive_id in enumerate(body.ordered_executive_ids, start=1):
            cursor.execute(
                "UPDATE RSPL_ExecScheduleExecutive SET SortOrder = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
                "WHERE ExecutiveId = ?",
                index, current_user.user_id, executive_id,
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
            f"SELECT ExecutiveId, CellDate, CellText, CellColor FROM RSPL_ExecScheduleCell "
            f"WHERE ExecutiveId IN ({placeholders}) AND CellDate BETWEEN ? AND ?",
            *executive_ids, first_day, last_day,
        )
        rows = rows_to_dicts(cursor)

    cells = [
        CellRow(executive_id=r["ExecutiveId"], day=r["CellDate"].day, text=r["CellText"] or "", color=r["CellColor"])
        for r in rows
    ]
    return CellsResponse(executives=executives, days=days, cells=cells)


@router.post("/cells/save")
def save_cells(body: SaveCellsRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        for cell in body.cells:
            cell_date = date(body.year, body.month, cell.day)
            cursor.execute(
                "UPDATE RSPL_ExecScheduleCell SET CellText = ?, CellColor = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
                "WHERE ExecutiveId = ? AND CellDate = ?",
                cell.text, cell.color, current_user.user_id, cell.executive_id, cell_date,
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO RSPL_ExecScheduleCell (ExecutiveId, CellDate, CellText, CellColor, LastEditedByUserId, LastEditedAt) "
                    "VALUES (?, ?, ?, ?, ?, SYSDATETIME())",
                    cell.executive_id, cell_date, cell.text, cell.color, current_user.user_id,
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
            f"SELECT ExecutiveId, CellDate, CellText, CellColor FROM RSPL_ExecScheduleCell "
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
                "INSERT INTO RSPL_ExecScheduleCell (ExecutiveId, CellDate, CellText, CellColor, LastEditedByUserId, LastEditedAt) "
                "VALUES (?, ?, ?, ?, ?, SYSDATETIME())",
                r["ExecutiveId"], target_date, r["CellText"], r["CellColor"], current_user.user_id,
            )
            copied += 1

    return {"success": True, "copied_count": copied}
