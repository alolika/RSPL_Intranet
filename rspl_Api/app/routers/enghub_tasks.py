"""Engineering Hub - Tasks (Development Item > Task).

A Task's current Developer/Tester is tracked via EngHub_AssignmentHistory
(RoleType='Developer'/'Tester'), not a static column on EngHub_Task itself —
the same never-overwritten assignment model used for Feature Owner/Technical
Owner (see enghub_common.py). "My Tasks" filters on the caller's current
AssignmentHistory row rather than a static AssignedTo column.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.enghub_common import AssignmentHistoryRow, AssignRequest, assign_role, get_assignment_history, require_current_assignee

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-tasks"])


class LookupOption(BaseModel):
    label: str
    value: int


class TaskRow(BaseModel):
    task_id: int
    dev_item_id: int
    dev_item_title: str
    feature_id: int
    feature_name: str
    module_id: int
    product_id: int
    task_type_id: int
    task_type_name: str
    title: str
    description: str
    status_id: int
    status_name: str
    is_terminal: bool
    estimated_hours: float | None = None
    actual_hours: float = 0.0
    actual_minutes: int = 0
    developer_user_id: int | None = None
    developer_name: str | None = None
    closed_at: str | None = None


# Actual time spent = SUM(DurationMinutes) across every EngHub_Activity logged
# against the task, same "any activity with a duration counts as effort"
# convention already established by enghub_reports.py's EffortBase CTE (Work
# Logged, Discussion, Interruption entries can all carry a duration) — not
# narrowed to just "Work Logged" entries, for consistency with how effort is
# counted everywhere else in this module.
_TASK_SELECT = """
    SELECT t.TaskId, t.DevItemId, d.Title AS DevItemTitle, d.FeatureId, f.Name AS FeatureName,
           f.ModuleId, p.ProductId,
           t.TaskTypeId, tt.Name AS TaskTypeName, t.Title, t.Description,
           t.StatusId, s.Name AS StatusName, s.IsTerminal, t.EstimatedHours, t.ClosedAt,
           dev.UserId AS DeveloperUserId, devu.Name AS DeveloperName,
           act.TotalMinutes AS ActualMinutes
    FROM EngHub_Task t
    JOIN EngHub_DevelopmentItem d ON d.DevItemId = t.DevItemId
    JOIN EngHub_Feature f ON f.FeatureId = d.FeatureId
    JOIN EngHub_Module m ON m.ModuleId = f.ModuleId
    JOIN EngHub_Product p ON p.ProductId = m.ProductId
    JOIN EngHub_TaskType tt ON tt.TaskTypeId = t.TaskTypeId
    JOIN EngHub_Status s ON s.StatusId = t.StatusId
    LEFT JOIN EngHub_AssignmentHistory dev ON dev.EntityType = 'Task' AND dev.EntityId = t.TaskId
        AND dev.RoleType = 'Developer' AND dev.UnassignedAt IS NULL
    LEFT JOIN UserMaster devu ON devu.UserID = dev.UserId
    LEFT JOIN (
        SELECT TaskId, SUM(DurationMinutes) AS TotalMinutes
        FROM EngHub_Activity
        WHERE TaskId IS NOT NULL AND DurationMinutes IS NOT NULL
        GROUP BY TaskId
    ) act ON act.TaskId = t.TaskId
"""


def _in_clause(where: list[str], params: list, column: str, values: list[int] | None) -> None:
    if not values:
        return
    where.append(f"{column} IN ({','.join('?' for _ in values)})")
    params.extend(values)


def _row_to_task(r: dict) -> TaskRow:
    return TaskRow(
        task_id=r["TaskId"], dev_item_id=r["DevItemId"], dev_item_title=r["DevItemTitle"] or "",
        feature_id=r["FeatureId"], feature_name=r["FeatureName"] or "",
        module_id=r["ModuleId"], product_id=r["ProductId"],
        task_type_id=r["TaskTypeId"], task_type_name=r["TaskTypeName"] or "",
        title=r["Title"] or "", description=r["Description"] or "",
        status_id=r["StatusId"], status_name=r["StatusName"] or "", is_terminal=bool(r["IsTerminal"]),
        estimated_hours=float(r["EstimatedHours"]) if r["EstimatedHours"] is not None else None,
        actual_hours=float(r["ActualMinutes"]) / 60.0 if r["ActualMinutes"] is not None else 0.0,
        actual_minutes=int(r["ActualMinutes"]) if r["ActualMinutes"] is not None else 0,
        developer_user_id=r["DeveloperUserId"], developer_name=r["DeveloperName"],
        closed_at=r["ClosedAt"].isoformat() if r["ClosedAt"] else None,
    )


@router.get("/tasks", response_model=list[TaskRow])
def get_tasks(
    dev_item_id: int | None = None,
    assigned_to_user_id: int | None = None,
    include_closed: bool = False,
    product_ids: list[int] | None = Query(None),
    module_ids: list[int] | None = Query(None),
    feature_ids: list[int] | None = Query(None),
    dev_item_ids: list[int] | None = Query(None),
    task_type_ids: list[int] | None = Query(None),
    status_ids: list[int] | None = Query(None),
    developer_user_ids: list[int] | None = Query(None),
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[TaskRow]:
    where: list[str] = []
    params: list = []
    if dev_item_id is not None:
        where.append("t.DevItemId = ?")
        params.append(dev_item_id)
    if assigned_to_user_id is not None:
        where.append("dev.UserId = ?")
        params.append(assigned_to_user_id)
    _in_clause(where, params, "p.ProductId", product_ids)
    _in_clause(where, params, "f.ModuleId", module_ids)
    _in_clause(where, params, "d.FeatureId", feature_ids)
    _in_clause(where, params, "t.DevItemId", dev_item_ids)
    _in_clause(where, params, "t.TaskTypeId", task_type_ids)
    _in_clause(where, params, "dev.UserId", developer_user_ids)
    if date_from:
        # date_from/date_to are plain "YYYY-MM-DD" calendar dates (no time,
        # no timezone) — the frontend deliberately doesn't send an ISO
        # instant here, since converting a locally-picked date to UTC before
        # sending shifts it onto the wrong calendar day for any user ahead
        # of UTC (e.g. IST, +5:30) and silently excludes same-day rows.
        where.append("t.CreatedAt >= CAST(? AS DATE)")
        params.append(date_from)
    if date_to:
        # < the day AFTER date_to (not <= date_to) so the entire end day is
        # included regardless of what time of day a task was created.
        where.append("t.CreatedAt < DATEADD(day, 1, CAST(? AS DATE))")
        params.append(date_to)
    if status_ids:
        # An explicit Status filter fully determines which statuses show,
        # overriding include_closed rather than fighting it (picking a
        # closed status with include_closed still false would otherwise
        # silently return nothing).
        _in_clause(where, params, "t.StatusId", status_ids)
    elif not include_closed:
        where.append("s.IsTerminal = 0")
    sql = _TASK_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    # In Progress tasks surface first by default (what the user is actively
    # working on right now is the most actionable thing to see); everything
    # else keeps the existing newest-first order, both within In Progress
    # and within the rest.
    sql += " ORDER BY CASE WHEN s.Name = 'In Progress' THEN 0 ELSE 1 END, t.TaskId DESC"
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_task(r) for r in rows]


@router.get("/tasks/lookup", response_model=list[LookupOption])
def get_tasks_lookup(dev_item_id: int | None = None) -> list[LookupOption]:
    # dev_item_id omitted -> every task across all development items, used by
    # report filter dropdowns (Testing Effort / Support-Driven Work), not a
    # cascading picker there; capped per this app's established
    # unbounded-query caution.
    with get_cursor() as cursor:
        if dev_item_id is not None:
            cursor.execute("SELECT TaskId, Title FROM EngHub_Task WHERE DevItemId = ? ORDER BY TaskId DESC", dev_item_id)
            rows = rows_to_dicts(cursor)
        else:
            cursor.execute("SELECT TaskId, Title FROM EngHub_Task ORDER BY TaskId DESC")
            rows = rows_to_dicts(cursor, limit=500)
    return [LookupOption(label=r["Title"] or "", value=r["TaskId"]) for r in rows]


@router.get("/tasks/{task_id}", response_model=TaskRow)
def get_task(task_id: int) -> TaskRow:
    with get_cursor() as cursor:
        cursor.execute(_TASK_SELECT + " WHERE t.TaskId = ?", task_id)
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _row_to_task(row)


class TaskForm(BaseModel):
    task_id: int
    dev_item_id: int
    task_type_id: int
    title: str
    description: str
    status_id: int
    estimated_hours: float | None = None


@router.post("/tasks")
def save_task(row: TaskForm, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.task_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Task (DevItemId, TaskTypeId, Title, Description, StatusId, "
                "EstimatedHours, CreatedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?); "
                "SELECT SCOPE_IDENTITY() AS Id",
                row.dev_item_id, row.task_type_id, row.title, row.description, row.status_id,
                row.estimated_hours, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
        else:
            # Editing an EXISTING task is restricted to its currently assigned
            # Developer (same Log-Work-column permission as log_work/
            # set_task_status) — creating a brand-new task (task_id == 0,
            # above) stays open to everyone, it has no assignee yet.
            require_current_assignee(cursor, "Task", row.task_id, "Developer", user)
            cursor.execute(
                "UPDATE EngHub_Task SET DevItemId=?, TaskTypeId=?, Title=?, Description=?, StatusId=?, "
                "EstimatedHours=?, LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE TaskId=?",
                row.dev_item_id, row.task_type_id, row.title, row.description, row.status_id,
                row.estimated_hours, user.user_id, row.task_id,
            )
            new_id = row.task_id
    return {"success": True, "task_id": new_id}


@router.post("/tasks/{task_id}/assign")
def assign_task_role(task_id: int, body: AssignRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    assign_role("Task", task_id, body.role_type, body.user_id, user)
    return {"success": True}


class SetTaskStatusRequest(BaseModel):
    status_id: int


@router.put("/tasks/{task_id}/status")
def set_task_status(task_id: int, body: SetTaskStatusRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Status-only change from the grid's inline dropdown — distinct from
    log_work's new_status_id, which only applies alongside a work-log entry.
    Doesn't touch EngHub_Activity; this is a plain field update, same
    terminal/ClosedAt handling as log_work's status branch."""
    with get_cursor() as cursor:
        cursor.execute("SELECT TaskId FROM EngHub_Task WHERE TaskId = ?", task_id)
        if first_row_or_none(cursor) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        # Same Log-Work-column permission as log_work() in enghub_activities.py
        # — this inline status dropdown lives in the same grid column and
        # must not let a non-assigned user bypass it by skipping duration.
        require_current_assignee(cursor, "Task", task_id, "Developer", user)

        cursor.execute("SELECT IsTerminal FROM EngHub_Status WHERE StatusId = ?", body.status_id)
        is_terminal = bool((first_row_or_none(cursor) or {}).get("IsTerminal"))
        if is_terminal:
            cursor.execute(
                "UPDATE EngHub_Task SET StatusId = ?, LastEditedByUserId = ?, LastEditedAt = SYSUTCDATETIME(), "
                "ClosedAt = SYSUTCDATETIME() WHERE TaskId = ?",
                body.status_id, user.user_id, task_id,
            )
        else:
            cursor.execute(
                "UPDATE EngHub_Task SET StatusId = ?, LastEditedByUserId = ?, LastEditedAt = SYSUTCDATETIME() "
                "WHERE TaskId = ?",
                body.status_id, user.user_id, task_id,
            )
    return {"success": True}


@router.get("/tasks/{task_id}/assignment-history", response_model=list[AssignmentHistoryRow])
def get_task_assignment_history(task_id: int) -> list[AssignmentHistoryRow]:
    return get_assignment_history("Task", task_id)


# ---------------------------------------------------------------------------
# Trouble Ticket linking — exactly one real Trouble Ticket per Task at a
# time, same single-value model as enghub_features.py's Feature ticket link
# (EngHub_TaskTicket, TicketVoucherNo FK's to TTMaster.VoucherNo). Search
# reuses the same TTMaster-backed /tickets endpoint (enghub_devitems.py).
# ---------------------------------------------------------------------------

class TaskTicketRow(BaseModel):
    task_ticket_id: int
    ticket_voucher_no: int
    ticket_date: str | None = None
    ticket_customer_name: str | None = None
    added_by_user_id: int
    added_by_name: str
    added_at: str


@router.get("/tasks/{task_id}/ticket", response_model=TaskTicketRow | None)
def get_task_ticket(task_id: int) -> TaskTicketRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 1 tt.TaskTicketId, tt.TicketVoucherNo, tm.Date AS TicketDate, "
            "LTRIM(RTRIM(cm.DisplayName)) AS CustomerName, tt.AddedByUserId, u.Name AS AddedByName, tt.AddedAt "
            "FROM EngHub_TaskTicket tt "
            "LEFT JOIN TTMaster tm ON tm.VoucherNo = tt.TicketVoucherNo "
            "LEFT JOIN CustomerMaster cm ON cm.CustID = tm.CustID "
            "JOIN UserMaster u ON u.UserID = tt.AddedByUserId "
            "WHERE tt.TaskId = ? ORDER BY tt.AddedAt DESC",
            task_id,
        )
        row = first_row_or_none(cursor)
    if row is None:
        return None
    return TaskTicketRow(
        task_ticket_id=row["TaskTicketId"], ticket_voucher_no=row["TicketVoucherNo"],
        ticket_date=str(row["TicketDate"]) if row["TicketDate"] else None, ticket_customer_name=row["CustomerName"],
        added_by_user_id=row["AddedByUserId"], added_by_name=row["AddedByName"] or "",
        added_at=row["AddedAt"].isoformat() if row["AddedAt"] else "",
    )


class SetTaskTicketRequest(BaseModel):
    ticket_voucher_no: int | None = None


@router.put("/tasks/{task_id}/ticket")
def set_task_ticket(task_id: int, body: SetTaskTicketRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if body.ticket_voucher_no is not None:
            cursor.execute("SELECT 1 FROM TTMaster WHERE VoucherNo = ?", body.ticket_voucher_no)
            if first_row_or_none(cursor) is None:
                raise HTTPException(status_code=404, detail="Ticket not found")
        # Always clear any existing link first — this endpoint sets THE one
        # ticket for the Task, it doesn't add to a list.
        cursor.execute("DELETE FROM EngHub_TaskTicket WHERE TaskId = ?", task_id)
        if body.ticket_voucher_no is not None:
            cursor.execute(
                "INSERT INTO EngHub_TaskTicket (TaskId, TicketVoucherNo, AddedByUserId) VALUES (?, ?, ?)",
                task_id, body.ticket_voucher_no, user.user_id,
            )
    return {"success": True}
