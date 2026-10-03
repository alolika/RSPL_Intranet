"""Engineering Hub - per-developer dashboard.

The Dashboard page is user-wise: by default it shows the logged-in user's own
numbers; only a user granted the DASHBOARD_ALL_USERS right (Menu Rights page,
"Dashboard: View All Developers") may pass `user_id` to view any developer's. Everything is scoped to one user + a date range:

Summary
  - hours worked, days worked, avg hours per worked day, tasks worked on
  - hours by date / by module / by development-item type / by task type
  - task switches (interruptions): how many, how long, by reason
  - open tasks assigned now, overdue tasks, tasks closed in the range
Detailed
  - every task switch, estimate-vs-actual per task, open-task list, work log

Effort = SUM(EngHub_Activity.DurationMinutes) logged BY the user — the same
"any activity with a duration counts" rule as enghub_reports.py, reusing its
_EFFORT_BASE_CTE so effort resolves to Feature/Module/DevItem identically.

Dates: OccurredAt/ClosedAt are UTC (SYSUTCDATETIME). Grouping and the
date_from/date_to filter use the DB server's LOCAL calendar date (IST here),
via the server's own UTC offset, so "worked on 3-Oct" means the user's 3-Oct.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.enghub_common import utc_iso
from app.routers.enghub_menu_rights import has_menu
from app.routers.enghub_reports import _EFFORT_BASE_CTE

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-developer-dashboard"])


class NamedMinutes(BaseModel):
    name: str
    minutes: int


class DateMinutes(BaseModel):
    date: str  # "YYYY-MM-DD", local calendar date
    minutes: int


class ModuleMinutes(BaseModel):
    product_name: str
    module_name: str
    minutes: int


class SwitchReason(BaseModel):
    reason: str
    count: int
    minutes: int


class SwitchRow(BaseModel):
    occurred_at: str
    from_task: str
    to_task: str
    reason: str
    requested_by: str
    minutes: int | None = None
    comments: str | None = None


class OpenTaskRow(BaseModel):
    task_id: int
    title: str
    dev_item_title: str
    status_name: str
    estimated_hours: float | None = None
    spent_minutes: int
    expected_deadline: str | None = None
    overdue_days: int | None = None  # None = no deadline, 0 = not overdue


class EstimateRow(BaseModel):
    task_id: int
    title: str
    status_name: str
    is_terminal: bool
    estimated_hours: float | None = None
    minutes_in_range: int  # this user's time on it within the range
    total_minutes: int  # all-time, everyone


class WorkLogRow(BaseModel):
    activity_id: int
    occurred_at: str
    activity_type: str
    task_title: str | None = None
    dev_item_title: str | None = None
    module_name: str | None = None
    minutes: int | None = None
    description: str | None = None


class DeveloperDashboard(BaseModel):
    user_id: int
    user_name: str
    date_from: str
    date_to: str
    total_minutes: int
    days_worked: int
    tasks_worked: int
    open_tasks: int
    overdue_tasks: int
    tasks_closed: int
    switch_count: int
    switch_minutes: int
    by_date: list[DateMinutes]
    by_module: list[ModuleMinutes]
    by_dev_item_type: list[NamedMinutes]
    by_task_type: list[NamedMinutes]
    switch_reasons: list[SwitchReason]
    switches: list[SwitchRow]
    open_task_list: list[OpenTaskRow]
    estimates: list[EstimateRow]
    work_log: list[WorkLogRow]


def _parse_date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a date (YYYY-MM-DD)")


@router.get("/developer-dashboard", response_model=DeveloperDashboard)
def get_developer_dashboard(
    user_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> DeveloperDashboard:
    target = user_id or user.user_id
    if target != user.user_id and not has_menu(user.user_id, "DASHBOARD_ALL_USERS"):
        raise HTTPException(status_code=403, detail="You can only view your own dashboard.")

    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DATEDIFF(minute, SYSUTCDATETIME(), SYSDATETIME()) AS OffsetMin, CAST(SYSDATETIME() AS DATE) AS Today"
        )
        r = rows_to_dicts(cursor)[0]
        off, today = int(r["OffsetMin"]), r["Today"]
        d_to = _parse_date(date_to, "date_to") or today
        d_from = _parse_date(date_from, "date_from") or d_to.replace(day=1)
        if d_from > d_to:
            raise HTTPException(status_code=400, detail="From date is after To date")

        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ?", target)
        u = rows_to_dicts(cursor)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        user_name = u[0]["Name"] or ""

        # Local calendar date of a UTC column.
        def local(col: str) -> str:
            return f"CAST(DATEADD(minute, {off}, {col}) AS DATE)"

        # Effort rows logged by the target user inside the range.
        eff_where = f"WHERE LoggedByUserId = ? AND {local('OccurredAt')} BETWEEN ? AND ?"
        eff_params = [target, d_from, d_to]

        cursor.execute(
            _EFFORT_BASE_CTE
            + f"""
            SELECT ISNULL(SUM(DurationMinutes), 0) AS Minutes,
                   COUNT(DISTINCT {local('OccurredAt')}) AS Days,
                   COUNT(DISTINCT ResolvedTaskId) AS Tasks
            FROM EffortBase {eff_where}
            """,
            *eff_params,
        )
        k = rows_to_dicts(cursor)[0]

        cursor.execute(
            _EFFORT_BASE_CTE
            + f"""
            SELECT {local('OccurredAt')} AS D, SUM(DurationMinutes) AS Minutes
            FROM EffortBase {eff_where}
            GROUP BY {local('OccurredAt')} ORDER BY D
            """,
            *eff_params,
        )
        by_date = [DateMinutes(date=str(x["D"])[:10], minutes=x["Minutes"] or 0) for x in rows_to_dicts(cursor)]

        cursor.execute(
            _EFFORT_BASE_CTE
            + f"""
            SELECT ISNULL(p.Name, '') AS ProductName, ISNULL(m.Name, '(No module)') AS ModuleName,
                   SUM(e.DurationMinutes) AS Minutes
            FROM EffortBase e
            LEFT JOIN EngHub_Module m ON m.ModuleId = e.ResolvedModuleId
            LEFT JOIN EngHub_Product p ON p.ProductId = m.ProductId
            {eff_where.replace('WHERE ', 'WHERE e.')}
            GROUP BY p.Name, m.Name ORDER BY Minutes DESC
            """,
            *eff_params,
        )
        by_module = [
            ModuleMinutes(product_name=x["ProductName"], module_name=x["ModuleName"], minutes=x["Minutes"] or 0)
            for x in rows_to_dicts(cursor)
        ]

        cursor.execute(
            _EFFORT_BASE_CTE
            + f"""
            SELECT ISNULL(dt.Name, '(Feature-level work)') AS Name, SUM(e.DurationMinutes) AS Minutes
            FROM EffortBase e
            LEFT JOIN EngHub_DevelopmentItem d ON d.DevItemId = e.ResolvedDevItemId
            LEFT JOIN EngHub_DevItemType dt ON dt.DevItemTypeId = d.DevItemTypeId
            {eff_where.replace('WHERE ', 'WHERE e.')}
            GROUP BY dt.Name ORDER BY Minutes DESC
            """,
            *eff_params,
        )
        by_dev_item_type = [NamedMinutes(name=x["Name"], minutes=x["Minutes"] or 0) for x in rows_to_dicts(cursor)]

        cursor.execute(
            _EFFORT_BASE_CTE
            + f"""
            SELECT ISNULL(TaskTypeName, '(Not on a task)') AS Name, SUM(DurationMinutes) AS Minutes
            FROM EffortBase {eff_where}
            GROUP BY TaskTypeName ORDER BY Minutes DESC
            """,
            *eff_params,
        )
        by_task_type = [NamedMinutes(name=x["Name"], minutes=x["Minutes"] or 0) for x in rows_to_dicts(cursor)]

        # Task switches = Interruption activities logged by the user.
        sw_where = f"WHERE a.LoggedByUserId = ? AND {local('a.OccurredAt')} BETWEEN ? AND ?"
        cursor.execute(
            f"""
            SELECT a.OccurredAt, ot.Title AS FromTask, nt.Title AS ToTask, ir.Name AS Reason,
                   ru.Name AS RequestedBy, a.DurationMinutes, i.Comments
            FROM EngHub_Interruption i
            JOIN EngHub_Activity a ON a.ActivityId = i.ActivityId
            LEFT JOIN EngHub_Task ot ON ot.TaskId = i.InterruptedTaskId
            LEFT JOIN EngHub_Task nt ON nt.TaskId = i.NewTaskId
            LEFT JOIN EngHub_InterruptionReason ir ON ir.InterruptionReasonId = i.InterruptionReasonId
            LEFT JOIN UserMaster ru ON ru.UserID = i.RequestedByUserId
            {sw_where}
            ORDER BY a.OccurredAt DESC
            """,
            *eff_params,
        )
        switches = [
            SwitchRow(
                occurred_at=utc_iso(x["OccurredAt"]), from_task=x["FromTask"] or "", to_task=x["ToTask"] or "",
                reason=x["Reason"] or "", requested_by=x["RequestedBy"] or "", minutes=x["DurationMinutes"],
                comments=x["Comments"],
            )
            for x in rows_to_dicts(cursor, limit=500)
        ]
        reasons: dict[str, SwitchReason] = {}
        for s in switches:
            agg = reasons.setdefault(s.reason, SwitchReason(reason=s.reason, count=0, minutes=0))
            agg.count += 1
            agg.minutes += s.minutes or 0
        switch_reasons = sorted(reasons.values(), key=lambda x: (-x.count, -x.minutes))

        # Open tasks currently assigned to the user (as Developer).
        cursor.execute(
            """
            SELECT t.TaskId, t.Title, d.Title AS DevItemTitle, s.Name AS StatusName, t.EstimatedHours,
                   t.ExpectedDeadline,
                   CASE WHEN t.ExpectedDeadline IS NULL THEN NULL
                        WHEN t.ExpectedDeadline >= CAST(SYSDATETIME() AS DATE) THEN 0
                        ELSE DATEDIFF(day, t.ExpectedDeadline, CAST(SYSDATETIME() AS DATE)) END AS OverdueDays,
                   ISNULL((SELECT SUM(x.DurationMinutes) FROM EngHub_Activity x WHERE x.TaskId = t.TaskId), 0) AS Spent
            FROM EngHub_Task t
            JOIN EngHub_Status s ON s.StatusId = t.StatusId
            JOIN EngHub_DevelopmentItem d ON d.DevItemId = t.DevItemId
            JOIN EngHub_AssignmentHistory ah ON ah.EntityType = 'Task' AND ah.EntityId = t.TaskId
                 AND ah.RoleType = 'Developer' AND ah.UnassignedAt IS NULL
            WHERE ah.UserId = ? AND s.IsTerminal = 0
            ORDER BY CASE WHEN t.ExpectedDeadline IS NULL THEN 1 ELSE 0 END, t.ExpectedDeadline, t.TaskId
            """,
            target,
        )
        open_task_list = [
            OpenTaskRow(
                task_id=x["TaskId"], title=x["Title"] or "", dev_item_title=x["DevItemTitle"] or "",
                status_name=x["StatusName"] or "",
                estimated_hours=float(x["EstimatedHours"]) if x["EstimatedHours"] is not None else None,
                spent_minutes=x["Spent"] or 0,
                expected_deadline=str(x["ExpectedDeadline"])[:10] if x["ExpectedDeadline"] else None,
                overdue_days=x["OverdueDays"],
            )
            for x in rows_to_dicts(cursor, limit=200)
        ]

        # Tasks assigned to the user that were closed inside the range.
        cursor.execute(
            f"""
            SELECT COUNT(*) AS N
            FROM EngHub_Task t
            JOIN EngHub_AssignmentHistory ah ON ah.EntityType = 'Task' AND ah.EntityId = t.TaskId
                 AND ah.RoleType = 'Developer' AND ah.UnassignedAt IS NULL
            WHERE ah.UserId = ? AND t.ClosedAt IS NOT NULL AND {local('t.ClosedAt')} BETWEEN ? AND ?
            """,
            *eff_params,
        )
        tasks_closed = rows_to_dicts(cursor)[0]["N"] or 0

        # Estimate vs actual for every task the user logged time on in the range.
        cursor.execute(
            f"""
            SELECT t.TaskId, t.Title, s.Name AS StatusName, s.IsTerminal, t.EstimatedHours,
                   SUM(a.DurationMinutes) AS InRange,
                   (SELECT ISNULL(SUM(x.DurationMinutes), 0) FROM EngHub_Activity x WHERE x.TaskId = t.TaskId) AS Total
            FROM EngHub_Activity a
            JOIN EngHub_Task t ON t.TaskId = a.TaskId
            JOIN EngHub_Status s ON s.StatusId = t.StatusId
            WHERE a.LoggedByUserId = ? AND a.DurationMinutes IS NOT NULL
              AND {local('a.OccurredAt')} BETWEEN ? AND ?
            GROUP BY t.TaskId, t.Title, s.Name, s.IsTerminal, t.EstimatedHours
            ORDER BY InRange DESC
            """,
            *eff_params,
        )
        estimates = [
            EstimateRow(
                task_id=x["TaskId"], title=x["Title"] or "", status_name=x["StatusName"] or "",
                is_terminal=bool(x["IsTerminal"]),
                estimated_hours=float(x["EstimatedHours"]) if x["EstimatedHours"] is not None else None,
                minutes_in_range=x["InRange"] or 0, total_minutes=x["Total"] or 0,
            )
            for x in rows_to_dicts(cursor, limit=200)
        ]

        # Detailed work log — every activity the user logged in the range.
        cursor.execute(
            f"""
            SELECT a.ActivityId, a.OccurredAt, at.Name AS ActivityType, t.Title AS TaskTitle,
                   COALESCE(dt.Title, dd.Title) AS DevItemTitle,
                   COALESCE(mt.Name, md.Name, mf.Name) AS ModuleName,
                   a.DurationMinutes, a.Description
            FROM EngHub_Activity a
            JOIN EngHub_ActivityType at ON at.ActivityTypeId = a.ActivityTypeId
            LEFT JOIN EngHub_Task t ON t.TaskId = a.TaskId
            LEFT JOIN EngHub_DevelopmentItem dt ON dt.DevItemId = t.DevItemId
            LEFT JOIN EngHub_DevelopmentItem dd ON dd.DevItemId = a.DevItemId
            LEFT JOIN EngHub_Feature ft ON ft.FeatureId = dt.FeatureId
            LEFT JOIN EngHub_Feature fd ON fd.FeatureId = dd.FeatureId
            LEFT JOIN EngHub_Feature ff ON ff.FeatureId = a.FeatureId
            LEFT JOIN EngHub_Module mt ON mt.ModuleId = ft.ModuleId
            LEFT JOIN EngHub_Module md ON md.ModuleId = fd.ModuleId
            LEFT JOIN EngHub_Module mf ON mf.ModuleId = ff.ModuleId
            {sw_where}
            ORDER BY a.OccurredAt DESC
            """,
            *eff_params,
        )
        work_log = [
            WorkLogRow(
                activity_id=x["ActivityId"], occurred_at=utc_iso(x["OccurredAt"]), activity_type=x["ActivityType"] or "",
                task_title=x["TaskTitle"], dev_item_title=x["DevItemTitle"], module_name=x["ModuleName"],
                minutes=x["DurationMinutes"], description=x["Description"],
            )
            for x in rows_to_dicts(cursor, limit=1000)
        ]

    return DeveloperDashboard(
        user_id=target, user_name=user_name, date_from=d_from.isoformat(), date_to=d_to.isoformat(),
        total_minutes=k["Minutes"] or 0, days_worked=k["Days"] or 0, tasks_worked=k["Tasks"] or 0,
        open_tasks=len(open_task_list),
        overdue_tasks=sum(1 for t in open_task_list if (t.overdue_days or 0) > 0),
        tasks_closed=tasks_closed,
        switch_count=len(switches), switch_minutes=sum(s.minutes or 0 for s in switches),
        by_date=by_date, by_module=by_module, by_dev_item_type=by_dev_item_type, by_task_type=by_task_type,
        switch_reasons=switch_reasons, switches=switches,
        open_task_list=open_task_list, estimates=estimates, work_log=work_log,
    )
