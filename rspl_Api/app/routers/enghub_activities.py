"""Engineering Hub - Activities (append-only: work logs, discussions,
interruptions, participants) and EngHub_UserCurrentTask.

This is the module's daily-use core per the PRD — "logging work should
normally take under 10 seconds" — so /activities/work-log is a single,
narrow endpoint (duration + optional status change) rather than a generic
"create any activity" form. Discussions get their own endpoint too, since
they carry participants; a plain Comment could go through either but is
exposed via /activities/comment for a minimal-typing single-field case.

No running timers anywhere (PRD explicitly excludes them) — every duration
is a fixed amount the caller already knows, never a started/stopped clock.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.enghub_common import require_current_assignee

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-activities"])


def _in_clause(where: list[str], params: list, column: str, values: list[int] | None) -> None:
    if not values:
        return
    where.append(f"{column} IN ({','.join('?' for _ in values)})")
    params.extend(values)


class ActivityRow(BaseModel):
    activity_id: int
    activity_type_id: int
    activity_type_name: str
    feature_id: int | None = None
    dev_item_id: int | None = None
    task_id: int | None = None
    description: str | None = None
    duration_minutes: int | None = None
    old_status_id: int | None = None
    old_status_name: str | None = None
    new_status_id: int | None = None
    new_status_name: str | None = None
    occurred_at: str
    logged_by_user_id: int
    logged_by_name: str


_ACTIVITY_SELECT = """
    SELECT a.ActivityId, a.ActivityTypeId, at.Name AS ActivityTypeName,
           a.FeatureId, a.DevItemId, a.TaskId, a.Description, a.DurationMinutes,
           a.OldStatusId, os.Name AS OldStatusName, a.NewStatusId, ns.Name AS NewStatusName,
           a.OccurredAt, a.LoggedByUserId, u.Name AS LoggedByName
    FROM EngHub_Activity a
    JOIN EngHub_ActivityType at ON at.ActivityTypeId = a.ActivityTypeId
    JOIN UserMaster u ON u.UserID = a.LoggedByUserId
    LEFT JOIN EngHub_Status os ON os.StatusId = a.OldStatusId
    LEFT JOIN EngHub_Status ns ON ns.StatusId = a.NewStatusId
"""


def _row_to_activity(r: dict) -> ActivityRow:
    return ActivityRow(
        activity_id=r["ActivityId"], activity_type_id=r["ActivityTypeId"], activity_type_name=r["ActivityTypeName"] or "",
        feature_id=r["FeatureId"], dev_item_id=r["DevItemId"], task_id=r["TaskId"],
        description=r["Description"], duration_minutes=r["DurationMinutes"],
        old_status_id=r["OldStatusId"], old_status_name=r["OldStatusName"],
        new_status_id=r["NewStatusId"], new_status_name=r["NewStatusName"],
        occurred_at=r["OccurredAt"].isoformat() if r["OccurredAt"] else "",
        logged_by_user_id=r["LoggedByUserId"], logged_by_name=r["LoggedByName"] or "",
    )


def _activity_type_id(cursor, name: str) -> int:
    cursor.execute("SELECT ActivityTypeId FROM EngHub_ActivityType WHERE Name = ?", name)
    row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail=f"Missing EngHub_ActivityType row for '{name}'")
    return row["ActivityTypeId"]


def _upsert_current_task(cursor, user_id: int, task_id: int) -> None:
    cursor.execute("UPDATE EngHub_UserCurrentTask SET TaskId = ?, SetAt = SYSUTCDATETIME() WHERE UserId = ?", task_id, user_id)
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO EngHub_UserCurrentTask (UserId, TaskId) VALUES (?, ?)", user_id, task_id)


# ---------------------------------------------------------------------------
# Timeline (rollup) — the Feature/DevItem/Task detail pages' Timeline panel.
# Exactly one scope param is required so this never becomes an unbounded
# "every Activity ever logged" query.
# ---------------------------------------------------------------------------

@router.get("/activities", response_model=list[ActivityRow])
def get_activities(feature_id: int | None = None, dev_item_id: int | None = None, task_id: int | None = None) -> list[ActivityRow]:
    if sum(x is not None for x in (feature_id, dev_item_id, task_id)) != 1:
        raise HTTPException(status_code=400, detail="Provide exactly one of feature_id, dev_item_id, task_id")

    if feature_id is not None:
        where = (
            "(a.FeatureId = ? "
            "OR a.DevItemId IN (SELECT DevItemId FROM EngHub_DevelopmentItem WHERE FeatureId = ?) "
            "OR a.TaskId IN (SELECT TaskId FROM EngHub_Task WHERE DevItemId IN "
            "(SELECT DevItemId FROM EngHub_DevelopmentItem WHERE FeatureId = ?)))"
        )
        params = [feature_id, feature_id, feature_id]
    elif dev_item_id is not None:
        where = "(a.DevItemId = ? OR a.TaskId IN (SELECT TaskId FROM EngHub_Task WHERE DevItemId = ?))"
        params = [dev_item_id, dev_item_id]
    else:
        where = "a.TaskId = ?"
        params = [task_id]

    with get_cursor() as cursor:
        cursor.execute(f"{_ACTIVITY_SELECT} WHERE {where} ORDER BY a.OccurredAt DESC", *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_activity(r) for r in rows]


# ---------------------------------------------------------------------------
# Browse — the standalone "every activity ever logged" list (top-level
# Activities nav item). Unlike the rollup above, no scope is required; capped
# TOP 500 + ordered newest-first per this app's established unbounded-query
# caution, with optional type/user/date filters to narrow it down.
# ---------------------------------------------------------------------------

class ActivityBrowseRow(ActivityRow):
    feature_name: str | None = None
    dev_item_title: str | None = None
    task_title: str | None = None


_ACTIVITY_BROWSE_SELECT = """
    SELECT a.ActivityId, a.ActivityTypeId, at.Name AS ActivityTypeName,
           a.FeatureId, f.Name AS FeatureName,
           a.DevItemId, di.Title AS DevItemTitle,
           a.TaskId, t.Title AS TaskTitle,
           a.Description, a.DurationMinutes,
           a.OldStatusId, os.Name AS OldStatusName, a.NewStatusId, ns.Name AS NewStatusName,
           a.OccurredAt, a.LoggedByUserId, u.Name AS LoggedByName
    FROM EngHub_Activity a
    JOIN EngHub_ActivityType at ON at.ActivityTypeId = a.ActivityTypeId
    JOIN UserMaster u ON u.UserID = a.LoggedByUserId
    LEFT JOIN EngHub_Status os ON os.StatusId = a.OldStatusId
    LEFT JOIN EngHub_Status ns ON ns.StatusId = a.NewStatusId
    LEFT JOIN EngHub_Feature f ON f.FeatureId = a.FeatureId
    LEFT JOIN EngHub_DevelopmentItem di ON di.DevItemId = a.DevItemId
    LEFT JOIN EngHub_Task t ON t.TaskId = a.TaskId
"""


def _row_to_browse_activity(r: dict) -> ActivityBrowseRow:
    base = _row_to_activity(r)
    return ActivityBrowseRow(**base.model_dump(), feature_name=r["FeatureName"], dev_item_title=r["DevItemTitle"], task_title=r["TaskTitle"])


# Resolves each Activity's owning Feature/Module/Product regardless of which
# of the three optional FKs (FeatureId/DevItemId/TaskId) it's actually
# attached at, plus the underlying Task's Type/Status/current Developer when
# attached at Task level — same COALESCE-over-three-parallel-paths trick as
# enghub_reports.py's _EFFORT_BASE_CTE (kept separate/inlined here rather
# than shared, matching this app's per-file convention for this pattern).
# Feature/DevItem/Task-level-only activities simply resolve TaskType/Status/
# Developer to NULL, so they're correctly excluded by those filters rather
# than matching everything.
_ACTIVITY_RESOLVED_CTE = """
    WITH ActivityResolved AS (
        SELECT
            a.ActivityId,
            COALESCE(fViaTask.FeatureId, fViaDevItem.FeatureId, fDirect.FeatureId) AS ResolvedFeatureId,
            COALESCE(t.DevItemId, a.DevItemId) AS ResolvedDevItemId,
            COALESCE(fViaTask.ModuleId, fViaDevItem.ModuleId, fDirect.ModuleId) AS ResolvedModuleId,
            COALESCE(mViaTask.ProductId, mViaDevItem.ProductId, mDirect.ProductId) AS ResolvedProductId,
            t.TaskTypeId AS ResolvedTaskTypeId,
            t.StatusId AS ResolvedTaskStatusId,
            dev.UserId AS ResolvedDeveloperUserId
        FROM EngHub_Activity a
        LEFT JOIN EngHub_Task t ON t.TaskId = a.TaskId
        LEFT JOIN EngHub_DevelopmentItem diViaTask ON diViaTask.DevItemId = t.DevItemId
        LEFT JOIN EngHub_Feature fViaTask ON fViaTask.FeatureId = diViaTask.FeatureId
        LEFT JOIN EngHub_Module mViaTask ON mViaTask.ModuleId = fViaTask.ModuleId
        LEFT JOIN EngHub_DevelopmentItem diViaDevItem ON diViaDevItem.DevItemId = a.DevItemId
        LEFT JOIN EngHub_Feature fViaDevItem ON fViaDevItem.FeatureId = diViaDevItem.FeatureId
        LEFT JOIN EngHub_Module mViaDevItem ON mViaDevItem.ModuleId = fViaDevItem.ModuleId
        LEFT JOIN EngHub_Feature fDirect ON fDirect.FeatureId = a.FeatureId
        LEFT JOIN EngHub_Module mDirect ON mDirect.ModuleId = fDirect.ModuleId
        LEFT JOIN EngHub_AssignmentHistory dev ON dev.EntityType = 'Task' AND dev.EntityId = a.TaskId
            AND dev.RoleType = 'Developer' AND dev.UnassignedAt IS NULL
    )
"""


@router.get("/activities/browse", response_model=list[ActivityBrowseRow])
def browse_activities(
    activity_type_ids: list[int] | None = Query(None),
    logged_by_user_ids: list[int] | None = Query(None),
    date_from: str | None = None,
    date_to: str | None = None,
    product_ids: list[int] | None = Query(None),
    module_ids: list[int] | None = Query(None),
    feature_ids: list[int] | None = Query(None),
    dev_item_ids: list[int] | None = Query(None),
    task_type_ids: list[int] | None = Query(None),
    status_ids: list[int] | None = Query(None),
    developer_user_ids: list[int] | None = Query(None),
) -> list[ActivityBrowseRow]:
    where = ["1=1"]
    params: list = []
    _in_clause(where, params, "a.ActivityTypeId", activity_type_ids)
    _in_clause(where, params, "a.LoggedByUserId", logged_by_user_ids)
    if date_from:
        # Plain "YYYY-MM-DD" calendar date, not an ISO instant — converting a
        # locally-picked date to UTC before sending shifts it onto the wrong
        # day for any user ahead of UTC (e.g. IST, +5:30) and silently
        # excludes same-day rows. Same fix as the Tasks page's date filter.
        where.append("a.OccurredAt >= CAST(? AS DATE)")
        params.append(date_from)
    if date_to:
        # < the day AFTER date_to (not <=) so the entire end day is included
        # regardless of what time of day an activity was logged.
        where.append("a.OccurredAt < DATEADD(day, 1, CAST(? AS DATE))")
        params.append(date_to)
    _in_clause(where, params, "ar.ResolvedProductId", product_ids)
    _in_clause(where, params, "ar.ResolvedModuleId", module_ids)
    _in_clause(where, params, "ar.ResolvedFeatureId", feature_ids)
    _in_clause(where, params, "ar.ResolvedDevItemId", dev_item_ids)
    _in_clause(where, params, "ar.ResolvedTaskTypeId", task_type_ids)
    _in_clause(where, params, "ar.ResolvedTaskStatusId", status_ids)
    _in_clause(where, params, "ar.ResolvedDeveloperUserId", developer_user_ids)

    sql = f"""
        {_ACTIVITY_RESOLVED_CTE}
        {_ACTIVITY_BROWSE_SELECT}
        JOIN ActivityResolved ar ON ar.ActivityId = a.ActivityId
        WHERE {' AND '.join(where)}
        ORDER BY a.OccurredAt DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_browse_activity(r) for r in rows]


# ---------------------------------------------------------------------------
# Quick work log — the <10-second daily-use core.
# ---------------------------------------------------------------------------

class WorkLogRequest(BaseModel):
    task_id: int
    duration_minutes: int
    new_status_id: int | None = None
    description: str | None = None


@router.post("/activities/work-log")
def log_work(body: WorkLogRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute("SELECT StatusId FROM EngHub_Task WHERE TaskId = ?", body.task_id)
        task = first_row_or_none(cursor)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        # Only the Task's currently assigned Developer may log work against
        # it — the All Tasks grid's Log Work column disables these controls
        # client-side for everyone else, but that's UI-only, so it's
        # re-checked here too (a direct API call must not bypass it).
        require_current_assignee(cursor, "Task", body.task_id, "Developer", user)
        old_status_id = task["StatusId"]
        new_status_id = body.new_status_id

        activity_type_id = _activity_type_id(cursor, "Work Logged")
        cursor.execute(
            "INSERT INTO EngHub_Activity (ActivityTypeId, TaskId, Description, DurationMinutes, "
            "OldStatusId, NewStatusId, LoggedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?); "
            "SELECT SCOPE_IDENTITY() AS Id",
            activity_type_id, body.task_id, body.description, body.duration_minutes,
            old_status_id if new_status_id else None, new_status_id, user.user_id,
        )
        new_id = int(first_row_or_none(cursor)["Id"])

        if new_status_id and new_status_id != old_status_id:
            cursor.execute("SELECT IsTerminal FROM EngHub_Status WHERE StatusId = ?", new_status_id)
            is_terminal = bool((first_row_or_none(cursor) or {}).get("IsTerminal"))
            if is_terminal:
                cursor.execute(
                    "UPDATE EngHub_Task SET StatusId = ?, ClosedAt = SYSUTCDATETIME() WHERE TaskId = ?",
                    new_status_id, body.task_id,
                )
            else:
                cursor.execute("UPDATE EngHub_Task SET StatusId = ? WHERE TaskId = ?", new_status_id, body.task_id)

        _upsert_current_task(cursor, user.user_id, body.task_id)
    return {"success": True, "activity_id": new_id}


# ---------------------------------------------------------------------------
# Discussion (with participants) / plain Comment
# ---------------------------------------------------------------------------

class DiscussionRequest(BaseModel):
    feature_id: int | None = None
    dev_item_id: int | None = None
    task_id: int | None = None
    duration_minutes: int | None = None
    description: str
    participant_user_ids: list[int] = []


def _create_scoped_activity(
    cursor, activity_type_name: str, feature_id: int | None, dev_item_id: int | None, task_id: int | None,
    description: str | None, duration_minutes: int | None, user: CurrentUser,
) -> int:
    if feature_id is None and dev_item_id is None and task_id is None:
        raise HTTPException(status_code=400, detail="Provide at least one of feature_id, dev_item_id, task_id")
    activity_type_id = _activity_type_id(cursor, activity_type_name)
    cursor.execute(
        "INSERT INTO EngHub_Activity (ActivityTypeId, FeatureId, DevItemId, TaskId, Description, "
        "DurationMinutes, LoggedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
        activity_type_id, feature_id, dev_item_id, task_id, description, duration_minutes, user.user_id,
    )
    return int(first_row_or_none(cursor)["Id"])


@router.post("/activities/discussion")
def log_discussion(body: DiscussionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        new_id = _create_scoped_activity(
            cursor, "Discussion", body.feature_id, body.dev_item_id, body.task_id,
            body.description, body.duration_minutes, user,
        )
        # The logger is always a participant, already credited; invited
        # participants start 'Invited' until they accept/reject (PRD:
        # "Participants may accept, reject or add themselves later").
        cursor.execute(
            "INSERT INTO EngHub_ActivityParticipant (ActivityId, UserId, ParticipationStatus, AddedByUserId, RespondedAt) "
            "VALUES (?, ?, 'Accepted', ?, SYSUTCDATETIME())",
            new_id, user.user_id, user.user_id,
        )
        for participant_id in body.participant_user_ids:
            if participant_id == user.user_id:
                continue
            cursor.execute(
                "INSERT INTO EngHub_ActivityParticipant (ActivityId, UserId, ParticipationStatus, AddedByUserId) "
                "VALUES (?, ?, 'Invited', ?)",
                new_id, participant_id, user.user_id,
            )
    return {"success": True, "activity_id": new_id}


class CommentRequest(BaseModel):
    feature_id: int | None = None
    dev_item_id: int | None = None
    task_id: int | None = None
    description: str


@router.post("/activities/comment")
def log_comment(body: CommentRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        new_id = _create_scoped_activity(
            cursor, "Comment", body.feature_id, body.dev_item_id, body.task_id, body.description, None, user,
        )
    return {"success": True, "activity_id": new_id}


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

class ParticipantRow(BaseModel):
    activity_participant_id: int
    user_id: int
    user_name: str
    participation_status: str
    added_by_user_id: int
    added_by_name: str
    responded_at: str | None = None


@router.get("/activities/{activity_id}/participants", response_model=list[ParticipantRow])
def get_participants(activity_id: int) -> list[ParticipantRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT p.ActivityParticipantId, p.UserId, u.Name AS UserName, p.ParticipationStatus, "
            "p.AddedByUserId, ab.Name AS AddedByName, p.RespondedAt "
            "FROM EngHub_ActivityParticipant p "
            "JOIN UserMaster u ON u.UserID = p.UserId "
            "JOIN UserMaster ab ON ab.UserID = p.AddedByUserId "
            "WHERE p.ActivityId = ? ORDER BY p.ActivityParticipantId",
            activity_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ParticipantRow(
            activity_participant_id=r["ActivityParticipantId"], user_id=r["UserId"], user_name=r["UserName"] or "",
            participation_status=r["ParticipationStatus"], added_by_user_id=r["AddedByUserId"],
            added_by_name=r["AddedByName"] or "", responded_at=r["RespondedAt"].isoformat() if r["RespondedAt"] else None,
        )
        for r in rows
    ]


class ParticipantResponseRequest(BaseModel):
    status: str  # 'Accepted' | 'Rejected'


@router.post("/activities/{activity_id}/participants/respond")
def respond_to_participation(activity_id: int, body: ParticipantResponseRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE EngHub_ActivityParticipant SET ParticipationStatus = ?, RespondedAt = SYSUTCDATETIME() "
            "WHERE ActivityId = ? AND UserId = ?",
            body.status, activity_id, user.user_id,
        )
    return {"success": True}


@router.post("/activities/{activity_id}/participants/self-add")
def self_add_participant(activity_id: int, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM EngHub_ActivityParticipant WHERE ActivityId = ? AND UserId = ?) "
            "INSERT INTO EngHub_ActivityParticipant (ActivityId, UserId, ParticipationStatus, AddedByUserId, RespondedAt) "
            "VALUES (?, ?, 'SelfAdded', ?, SYSUTCDATETIME())",
            activity_id, user.user_id, activity_id, user.user_id, user.user_id,
        )
    return {"success": True}


# ---------------------------------------------------------------------------
# Interruptions + EngHub_UserCurrentTask
# ---------------------------------------------------------------------------

class CurrentTaskResponse(BaseModel):
    task_id: int | None = None
    task_title: str | None = None


@router.get("/user-current-task", response_model=CurrentTaskResponse)
def get_current_task(user: CurrentUser = Depends(get_current_user)) -> CurrentTaskResponse:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT t.TaskId, t.Title FROM EngHub_UserCurrentTask c "
            "JOIN EngHub_Task t ON t.TaskId = c.TaskId WHERE c.UserId = ?",
            user.user_id,
        )
        row = first_row_or_none(cursor)
    if row is None:
        return CurrentTaskResponse()
    return CurrentTaskResponse(task_id=row["TaskId"], task_title=row["Title"])


class SetCurrentTaskRequest(BaseModel):
    task_id: int


@router.put("/user-current-task")
def set_current_task(body: SetCurrentTaskRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        _upsert_current_task(cursor, user.user_id, body.task_id)
    return {"success": True}


class InterruptionRequest(BaseModel):
    new_task_id: int
    interruption_reason_id: int
    requested_by_user_id: int
    comments: str | None = None
    duration_minutes: int | None = None


@router.post("/activities/interruption")
def log_interruption(body: InterruptionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute("SELECT TaskId FROM EngHub_UserCurrentTask WHERE UserId = ?", user.user_id)
        current = first_row_or_none(cursor)
        interrupted_task_id = current["TaskId"] if current else None

        activity_type_id = _activity_type_id(cursor, "Interruption")
        cursor.execute(
            "INSERT INTO EngHub_Activity (ActivityTypeId, TaskId, Description, DurationMinutes, LoggedByUserId) "
            "VALUES (?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
            activity_type_id, body.new_task_id, body.comments, body.duration_minutes, user.user_id,
        )
        new_id = int(first_row_or_none(cursor)["Id"])

        if interrupted_task_id is not None and interrupted_task_id != body.new_task_id:
            cursor.execute(
                "INSERT INTO EngHub_Interruption (ActivityId, InterruptedTaskId, NewTaskId, InterruptionReasonId, "
                "RequestedByUserId, Comments) VALUES (?, ?, ?, ?, ?, ?)",
                new_id, interrupted_task_id, body.new_task_id, body.interruption_reason_id,
                body.requested_by_user_id, body.comments,
            )

        _upsert_current_task(cursor, user.user_id, body.new_task_id)
    return {"success": True, "activity_id": new_id}
