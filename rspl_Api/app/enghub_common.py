"""Engineering Hub - shared helpers, not a router itself.

EngHub_AssignmentHistory is polymorphic (EntityType, EntityId) and shared
across Feature/DevelopmentItem/Task — one small set of helpers here instead
of duplicating the same "close the old current row, insert a new one" logic
three times across enghub_features.py / enghub_devitems.py / enghub_tasks.py.
"""

from fastapi import HTTPException
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser


class AssignRequest(BaseModel):
    role_type: str
    user_id: int | None = None


class AssignmentHistoryRow(BaseModel):
    assignment_history_id: int
    role_type: str
    user_id: int
    user_name: str
    assigned_by_user_id: int
    assigned_by_name: str
    assigned_at: str
    unassigned_at: str | None = None
    comments: str | None = None


def assign_role(entity_type: str, entity_id: int, role_type: str, user_id: int | None, assigned_by: CurrentUser) -> None:
    """Closes out any current holder of (entity_type, entity_id, role_type) —
    "current" = the row with UnassignedAt IS NULL — and opens a new row.
    Never overwrites the old row, per the PRD's assignment-history
    requirement.

    user_id=None means "unassign": the current row is closed same as always,
    but no replacement row is inserted (EngHub_AssignmentHistory.UserId is a
    real FK into UserMaster, not nullable — there's no such thing as a
    history row for "nobody"). The role simply has no row with UnassignedAt
    IS NULL afterward, which every reader (get_features/get_tasks' LEFT JOIN
    ... AND UnassignedAt IS NULL) already treats as "unassigned" — the same
    state a role is in before it's ever been assigned for the first time.
    """
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE EngHub_AssignmentHistory SET UnassignedAt = SYSUTCDATETIME() "
            "WHERE EntityType = ? AND EntityId = ? AND RoleType = ? AND UnassignedAt IS NULL",
            entity_type, entity_id, role_type,
        )
        if user_id is not None:
            cursor.execute(
                "INSERT INTO EngHub_AssignmentHistory (EntityType, EntityId, RoleType, UserId, AssignedByUserId) "
                "VALUES (?, ?, ?, ?, ?)",
                entity_type, entity_id, role_type, user_id, assigned_by.user_id,
            )


def require_current_assignee(cursor, entity_type: str, entity_id: int, role_type: str, user: CurrentUser) -> None:
    """Raises 403 unless `user` currently holds role_type on (entity_type,
    entity_id) — e.g. only a Task's currently assigned Developer (UnassignedAt
    IS NULL) may log work or change its status via the All Tasks grid's Log
    Work column. Takes an open cursor rather than opening its own connection
    so callers can run it inside their existing get_cursor() block."""
    cursor.execute(
        "SELECT UserId FROM EngHub_AssignmentHistory WHERE EntityType = ? AND EntityId = ? "
        "AND RoleType = ? AND UnassignedAt IS NULL",
        entity_type, entity_id, role_type,
    )
    row = first_row_or_none(cursor)
    if row is None or row["UserId"] != user.user_id:
        raise HTTPException(status_code=403, detail=f"Only the assigned {role_type} can do this.")


def get_assignment_history(entity_type: str, entity_id: int) -> list[AssignmentHistoryRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ah.AssignmentHistoryId, ah.RoleType, ah.UserId, u.Name AS UserName, "
            "ah.AssignedByUserId, ab.Name AS AssignedByName, ah.AssignedAt, ah.UnassignedAt, ah.Comments "
            "FROM EngHub_AssignmentHistory ah "
            "JOIN UserMaster u ON u.UserID = ah.UserId "
            "JOIN UserMaster ab ON ab.UserID = ah.AssignedByUserId "
            "WHERE ah.EntityType = ? AND ah.EntityId = ? ORDER BY ah.AssignedAt DESC",
            entity_type, entity_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        AssignmentHistoryRow(
            assignment_history_id=r["AssignmentHistoryId"], role_type=r["RoleType"],
            user_id=r["UserId"], user_name=r["UserName"] or "",
            assigned_by_user_id=r["AssignedByUserId"], assigned_by_name=r["AssignedByName"] or "",
            assigned_at=r["AssignedAt"].isoformat() if r["AssignedAt"] else "",
            unassigned_at=r["UnassignedAt"].isoformat() if r["UnassignedAt"] else None,
            comments=r["Comments"],
        )
        for r in rows
    ]
