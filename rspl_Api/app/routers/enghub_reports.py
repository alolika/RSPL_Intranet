"""Engineering Hub - Dashboard and Reports.

The 8 PRD reports (Engineering effort, Module effort, Feature effort,
Interruptions, Testing effort, Pending ageing, Release progress,
Support-driven work) plus a dashboard-summary endpoint. Every list-shaped
report is capped (TOP 500) per this app's established unbounded-query
caution; grouped/aggregate reports are naturally bounded by how many
distinct groups exist (masters, releases) so don't need a separate cap.

Effort reports (1/2/3/5) share one base CTE that resolves whichever level an
Activity is actually attached to (Feature directly, via a Development Item,
or via a Task) up to its owning Feature/Module — since EngHub_Feature.ModuleId
is NOT NULL and EngHub_DevelopmentItem.FeatureId is NOT NULL, every Activity
resolves to exactly one Module this way, regardless of which of the three
optional FKs on EngHub_Activity is actually populated.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-reports"])


_EFFORT_BASE_CTE = """
    WITH EffortBase AS (
        SELECT
            a.ActivityId, a.DurationMinutes, a.OccurredAt, a.LoggedByUserId,
            at.Name AS ActivityTypeName,
            tt.Name AS TaskTypeName,
            a.TaskId AS ResolvedTaskId,
            t.Title AS ResolvedTaskTitle,
            COALESCE(diViaTask.DevItemId, diViaDevItem.DevItemId) AS ResolvedDevItemId,
            COALESCE(fViaTask.FeatureId, fViaDevItem.FeatureId, fDirect.FeatureId) AS ResolvedFeatureId,
            COALESCE(fViaTask.Name, fViaDevItem.Name, fDirect.Name) AS ResolvedFeatureName,
            COALESCE(fViaTask.ModuleId, fViaDevItem.ModuleId, fDirect.ModuleId) AS ResolvedModuleId,
            mResolved.ProductId AS ResolvedProductId,
            u.Name AS LoggedByName
        FROM EngHub_Activity a
        JOIN EngHub_ActivityType at ON at.ActivityTypeId = a.ActivityTypeId
        LEFT JOIN EngHub_Task t ON t.TaskId = a.TaskId
        LEFT JOIN EngHub_TaskType tt ON tt.TaskTypeId = t.TaskTypeId
        LEFT JOIN EngHub_DevelopmentItem diViaTask ON diViaTask.DevItemId = t.DevItemId
        LEFT JOIN EngHub_Feature fViaTask ON fViaTask.FeatureId = diViaTask.FeatureId
        LEFT JOIN EngHub_DevelopmentItem diViaDevItem ON diViaDevItem.DevItemId = a.DevItemId
        LEFT JOIN EngHub_Feature fViaDevItem ON fViaDevItem.FeatureId = diViaDevItem.FeatureId
        LEFT JOIN EngHub_Feature fDirect ON fDirect.FeatureId = a.FeatureId
        LEFT JOIN EngHub_Module mResolved ON mResolved.ModuleId =
            COALESCE(fViaTask.ModuleId, fViaDevItem.ModuleId, fDirect.ModuleId)
        LEFT JOIN UserMaster u ON u.UserID = a.LoggedByUserId
        WHERE a.DurationMinutes IS NOT NULL
    )
"""


def _date_filter(date_from: str | None, date_to: str | None, params: list) -> str:
    clauses = []
    if date_from:
        clauses.append("OccurredAt >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("OccurredAt <= ?")
        params.append(date_to)
    return (" AND " + " AND ".join(clauses)) if clauses else ""


# ---------------------------------------------------------------------------
# 1. Engineering effort — sliced by Module / TaskType / ActivityType
# ---------------------------------------------------------------------------

class EngineeringEffortRow(BaseModel):
    module_name: str | None = None
    task_type_name: str | None = None
    activity_type_name: str
    total_minutes: int
    entry_count: int


@router.get("/reports/engineering-effort", response_model=list[EngineeringEffortRow])
def get_engineering_effort(
    product_id: int | None = None, module_id: int | None = None, date_from: str | None = None, date_to: str | None = None
) -> list[EngineeringEffortRow]:
    params: list = []
    where = "WHERE 1=1" + _date_filter(date_from, date_to, params)
    if product_id is not None:
        where += " AND ResolvedProductId = ?"
        params.append(product_id)
    if module_id is not None:
        where += " AND ResolvedModuleId = ?"
        params.append(module_id)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT m.Name AS ModuleName, EffortBase.TaskTypeName, EffortBase.ActivityTypeName,
               SUM(DurationMinutes) AS TotalMinutes, COUNT(*) AS EntryCount
        FROM EffortBase
        LEFT JOIN EngHub_Module m ON m.ModuleId = EffortBase.ResolvedModuleId
        {where}
        GROUP BY m.Name, EffortBase.TaskTypeName, EffortBase.ActivityTypeName
        ORDER BY TotalMinutes DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        EngineeringEffortRow(
            module_name=r["ModuleName"], task_type_name=r["TaskTypeName"], activity_type_name=r["ActivityTypeName"] or "",
            total_minutes=r["TotalMinutes"] or 0, entry_count=r["EntryCount"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 2. Module effort — which modules need the most engineering effort
# ---------------------------------------------------------------------------

class ModuleEffortRow(BaseModel):
    module_id: int | None = None
    module_name: str | None = None
    product_name: str | None = None
    total_minutes: int
    entry_count: int


@router.get("/reports/module-effort", response_model=list[ModuleEffortRow])
def get_module_effort(product_id: int | None = None, date_from: str | None = None, date_to: str | None = None) -> list[ModuleEffortRow]:
    params: list = []
    where = "WHERE 1=1" + _date_filter(date_from, date_to, params)
    if product_id is not None:
        where += " AND p.ProductId = ?"
        params.append(product_id)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT m.ModuleId, m.Name AS ModuleName, p.Name AS ProductName,
               SUM(DurationMinutes) AS TotalMinutes, COUNT(*) AS EntryCount
        FROM EffortBase
        LEFT JOIN EngHub_Module m ON m.ModuleId = EffortBase.ResolvedModuleId
        LEFT JOIN EngHub_Product p ON p.ProductId = m.ProductId
        {where}
        GROUP BY m.ModuleId, m.Name, p.Name
        ORDER BY TotalMinutes DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        ModuleEffortRow(
            module_id=r["ModuleId"], module_name=r["ModuleName"], product_name=r["ProductName"],
            total_minutes=r["TotalMinutes"] or 0, entry_count=r["EntryCount"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 3. Feature effort
# ---------------------------------------------------------------------------

class FeatureEffortRow(BaseModel):
    feature_id: int | None = None
    feature_name: str | None = None
    module_name: str | None = None
    total_minutes: int
    entry_count: int


@router.get("/reports/feature-effort", response_model=list[FeatureEffortRow])
def get_feature_effort(
    product_id: int | None = None,
    module_id: int | None = None,
    feature_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[FeatureEffortRow]:
    params: list = []
    where = "WHERE 1=1" + _date_filter(date_from, date_to, params)
    if product_id is not None:
        where += " AND ResolvedProductId = ?"
        params.append(product_id)
    if module_id is not None:
        where += " AND ResolvedModuleId = ?"
        params.append(module_id)
    if feature_id is not None:
        where += " AND ResolvedFeatureId = ?"
        params.append(feature_id)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT ResolvedFeatureId, ResolvedFeatureName, m.Name AS ModuleName,
               SUM(DurationMinutes) AS TotalMinutes, COUNT(*) AS EntryCount
        FROM EffortBase
        LEFT JOIN EngHub_Module m ON m.ModuleId = EffortBase.ResolvedModuleId
        {where}
        GROUP BY ResolvedFeatureId, ResolvedFeatureName, m.Name
        ORDER BY TotalMinutes DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        FeatureEffortRow(
            feature_id=r["ResolvedFeatureId"], feature_name=r["ResolvedFeatureName"], module_name=r["ModuleName"],
            total_minutes=r["TotalMinutes"] or 0, entry_count=r["EntryCount"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 3b. Feature effort — entries drill-down (row-level Activity entries behind
# one Feature Effort row's Entries count, not a top-level report of its own)
# ---------------------------------------------------------------------------

class FeatureEffortEntryRow(BaseModel):
    activity_id: int
    occurred_at: str
    task_title: str | None = None
    activity_type_name: str
    duration_minutes: int
    logged_by_name: str | None = None


@router.get("/reports/feature-effort/{feature_id}/entries", response_model=list[FeatureEffortEntryRow])
def get_feature_effort_entries(
    feature_id: int, date_from: str | None = None, date_to: str | None = None
) -> list[FeatureEffortEntryRow]:
    params: list = [feature_id]
    where = "WHERE ResolvedFeatureId = ?" + _date_filter(date_from, date_to, params)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT EffortBase.ActivityId, EffortBase.OccurredAt, EffortBase.ResolvedTaskTitle,
               EffortBase.ActivityTypeName, EffortBase.DurationMinutes, EffortBase.LoggedByName
        FROM EffortBase
        {where}
        ORDER BY EffortBase.OccurredAt DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [
        FeatureEffortEntryRow(
            activity_id=r["ActivityId"], occurred_at=r["OccurredAt"].isoformat() if r["OccurredAt"] else "",
            task_title=r["ResolvedTaskTitle"], activity_type_name=r["ActivityTypeName"] or "",
            duration_minutes=r["DurationMinutes"] or 0, logged_by_name=r["LoggedByName"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 4. Interruptions
# ---------------------------------------------------------------------------

class InterruptionRow(BaseModel):
    activity_id: int
    occurred_at: str
    reason_name: str
    interrupted_task_title: str
    new_task_title: str
    requested_by_name: str
    comments: str | None = None
    duration_minutes: int | None = None


@router.get("/reports/interruptions", response_model=list[InterruptionRow])
def get_interruptions(date_from: str | None = None, date_to: str | None = None) -> list[InterruptionRow]:
    params: list = []
    where = "WHERE 1=1" + _date_filter(date_from, date_to, params)
    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT a.ActivityId, a.OccurredAt, r.Name AS ReasonName,
                   ti.Title AS InterruptedTaskTitle, tn.Title AS NewTaskTitle,
                   u.Name AS RequestedByName, i.Comments, a.DurationMinutes
            FROM EngHub_Interruption i
            JOIN EngHub_Activity a ON a.ActivityId = i.ActivityId
            JOIN EngHub_InterruptionReason r ON r.InterruptionReasonId = i.InterruptionReasonId
            JOIN EngHub_Task ti ON ti.TaskId = i.InterruptedTaskId
            JOIN EngHub_Task tn ON tn.TaskId = i.NewTaskId
            JOIN UserMaster u ON u.UserID = i.RequestedByUserId
            {where}
            ORDER BY a.OccurredAt DESC
            """,
            *params,
        )
        rows = rows_to_dicts(cursor, limit=500)
    return [
        InterruptionRow(
            activity_id=r["ActivityId"], occurred_at=r["OccurredAt"].isoformat() if r["OccurredAt"] else "",
            reason_name=r["ReasonName"] or "", interrupted_task_title=r["InterruptedTaskTitle"] or "",
            new_task_title=r["NewTaskTitle"] or "", requested_by_name=r["RequestedByName"] or "", comments=r["Comments"],
            duration_minutes=r["DurationMinutes"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 5. Testing effort — same effort base, filtered to TaskType = 'Testing'
# ---------------------------------------------------------------------------

@router.get("/reports/testing-effort", response_model=list[FeatureEffortRow])
def get_testing_effort(
    product_id: int | None = None,
    module_id: int | None = None,
    feature_id: int | None = None,
    dev_item_id: int | None = None,
    task_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[FeatureEffortRow]:
    params: list = []
    where = "WHERE TaskTypeName = 'Testing'" + _date_filter(date_from, date_to, params)
    if product_id is not None:
        where += " AND ResolvedProductId = ?"
        params.append(product_id)
    if module_id is not None:
        where += " AND ResolvedModuleId = ?"
        params.append(module_id)
    if feature_id is not None:
        where += " AND ResolvedFeatureId = ?"
        params.append(feature_id)
    if dev_item_id is not None:
        where += " AND ResolvedDevItemId = ?"
        params.append(dev_item_id)
    if task_id is not None:
        where += " AND ResolvedTaskId = ?"
        params.append(task_id)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT ResolvedFeatureId, ResolvedFeatureName, m.Name AS ModuleName,
               SUM(DurationMinutes) AS TotalMinutes, COUNT(*) AS EntryCount
        FROM EffortBase
        LEFT JOIN EngHub_Module m ON m.ModuleId = EffortBase.ResolvedModuleId
        {where}
        GROUP BY ResolvedFeatureId, ResolvedFeatureName, m.Name
        ORDER BY TotalMinutes DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        FeatureEffortRow(
            feature_id=r["ResolvedFeatureId"], feature_name=r["ResolvedFeatureName"], module_name=r["ModuleName"],
            total_minutes=r["TotalMinutes"] or 0, entry_count=r["EntryCount"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 5b. Testing effort — entries drill-down (row-level Activity entries behind
# one Testing Effort row's Entries count, not a top-level report of its own).
# Accepts the same filters as get_testing_effort (minus feature_id, which is
# fixed to the clicked row) so the returned entries always match whatever
# combination of filters produced that row's entry_count.
# ---------------------------------------------------------------------------

@router.get("/reports/testing-effort/{feature_id}/entries", response_model=list[FeatureEffortEntryRow])
def get_testing_effort_entries(
    feature_id: int,
    product_id: int | None = None,
    module_id: int | None = None,
    dev_item_id: int | None = None,
    task_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[FeatureEffortEntryRow]:
    params: list = [feature_id]
    where = "WHERE TaskTypeName = 'Testing' AND ResolvedFeatureId = ?" + _date_filter(date_from, date_to, params)
    if product_id is not None:
        where += " AND ResolvedProductId = ?"
        params.append(product_id)
    if module_id is not None:
        where += " AND ResolvedModuleId = ?"
        params.append(module_id)
    if dev_item_id is not None:
        where += " AND ResolvedDevItemId = ?"
        params.append(dev_item_id)
    if task_id is not None:
        where += " AND ResolvedTaskId = ?"
        params.append(task_id)
    sql = f"""
        {_EFFORT_BASE_CTE}
        SELECT EffortBase.ActivityId, EffortBase.OccurredAt, EffortBase.ResolvedTaskTitle,
               EffortBase.ActivityTypeName, EffortBase.DurationMinutes, EffortBase.LoggedByName
        FROM EffortBase
        {where}
        ORDER BY EffortBase.OccurredAt DESC
    """
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [
        FeatureEffortEntryRow(
            activity_id=r["ActivityId"], occurred_at=r["OccurredAt"].isoformat() if r["OccurredAt"] else "",
            task_title=r["ResolvedTaskTitle"], activity_type_name=r["ActivityTypeName"] or "",
            duration_minutes=r["DurationMinutes"] or 0, logged_by_name=r["LoggedByName"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 6. Pending ageing — non-terminal Development Items, oldest first
# ---------------------------------------------------------------------------

class PendingAgeingRow(BaseModel):
    dev_item_id: int
    title: str
    feature_name: str
    status_name: str
    age_days: int
    created_at: str


@router.get("/reports/pending-ageing", response_model=list[PendingAgeingRow])
def get_pending_ageing(
    product_id: int | None = None,
    module_id: int | None = None,
    feature_id: int | None = None,
    dev_item_id: int | None = None,
    age_days_gt: int | None = None,
) -> list[PendingAgeingRow]:
    params: list = []
    where = "WHERE s.IsTerminal = 0"
    if product_id is not None:
        where += " AND mo.ProductId = ?"
        params.append(product_id)
    if module_id is not None:
        where += " AND f.ModuleId = ?"
        params.append(module_id)
    if feature_id is not None:
        where += " AND d.FeatureId = ?"
        params.append(feature_id)
    if dev_item_id is not None:
        where += " AND d.DevItemId = ?"
        params.append(dev_item_id)
    if age_days_gt is not None:
        where += " AND DATEDIFF(day, d.CreatedAt, SYSUTCDATETIME()) > ?"
        params.append(age_days_gt)
    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT d.DevItemId, d.Title, f.Name AS FeatureName, s.Name AS StatusName,
                   DATEDIFF(day, d.CreatedAt, SYSUTCDATETIME()) AS AgeDays, d.CreatedAt
            FROM EngHub_DevelopmentItem d
            JOIN EngHub_Feature f ON f.FeatureId = d.FeatureId
            JOIN EngHub_Module mo ON mo.ModuleId = f.ModuleId
            JOIN EngHub_Status s ON s.StatusId = d.StatusId
            {where}
            ORDER BY AgeDays DESC
            """,
            *params,
        )
        rows = rows_to_dicts(cursor, limit=500)
    return [
        PendingAgeingRow(
            dev_item_id=r["DevItemId"], title=r["Title"] or "", feature_name=r["FeatureName"] or "",
            status_name=r["StatusName"] or "", age_days=r["AgeDays"] or 0,
            created_at=r["CreatedAt"].isoformat() if r["CreatedAt"] else "",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 7. Release progress
# ---------------------------------------------------------------------------

class ReleaseProgressRow(BaseModel):
    release_id: int
    release_name: str
    status_name: str
    mapped_feature_count: int
    mapped_dev_item_count: int
    dev_items_done_count: int
    dev_items_pending_count: int


@router.get("/reports/release-progress", response_model=list[ReleaseProgressRow])
def get_release_progress(product_id: int | None = None) -> list[ReleaseProgressRow]:
    params: list = []
    where = "WHERE 1=1"
    if product_id is not None:
        where += " AND r.ProductId = ?"
        params.append(product_id)
    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT r.ReleaseId, r.Name AS ReleaseName, s.Name AS StatusName,
                   SUM(CASE WHEN m.FeatureId IS NOT NULL THEN 1 ELSE 0 END) AS MappedFeatureCount,
                   SUM(CASE WHEN m.DevItemId IS NOT NULL THEN 1 ELSE 0 END) AS MappedDevItemCount,
                   SUM(CASE WHEN m.DevItemId IS NOT NULL AND ds.IsTerminal = 1 THEN 1 ELSE 0 END) AS DevItemsDone,
                   SUM(CASE WHEN m.DevItemId IS NOT NULL AND ds.IsTerminal = 0 THEN 1 ELSE 0 END) AS DevItemsPending
            FROM EngHub_Release r
            JOIN EngHub_Status s ON s.StatusId = r.StatusId
            LEFT JOIN EngHub_ReleaseMapping m ON m.ReleaseId = r.ReleaseId
            LEFT JOIN EngHub_DevelopmentItem d ON d.DevItemId = m.DevItemId
            LEFT JOIN EngHub_Status ds ON ds.StatusId = d.StatusId
            {where}
            GROUP BY r.ReleaseId, r.Name, s.Name
            ORDER BY r.ReleaseId DESC
            """,
            *params,
        )
        rows = rows_to_dicts(cursor, limit=500)
    return [
        ReleaseProgressRow(
            release_id=r["ReleaseId"], release_name=r["ReleaseName"] or "", status_name=r["StatusName"] or "",
            mapped_feature_count=r["MappedFeatureCount"] or 0, mapped_dev_item_count=r["MappedDevItemCount"] or 0,
            dev_items_done_count=r["DevItemsDone"] or 0, dev_items_pending_count=r["DevItemsPending"] or 0,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 8. Support-driven work — Development Items that originated from Support
# ---------------------------------------------------------------------------

class SupportDrivenWorkRow(BaseModel):
    dev_item_id: int
    title: str
    feature_name: str
    ticket_voucher_no: int | None = None
    customer_name: str | None = None
    status_name: str
    total_minutes: int


@router.get("/reports/support-driven-work", response_model=list[SupportDrivenWorkRow])
def get_support_driven_work(
    product_id: int | None = None,
    feature_id: int | None = None,
    dev_item_id: int | None = None,
    task_id: int | None = None,
    tt_no: int | None = None,
    user_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[SupportDrivenWorkRow]:
    params: list = []
    where = "WHERE ot.Name = 'Support'"
    if product_id is not None:
        where += " AND mo.ProductId = ?"
        params.append(product_id)
    if feature_id is not None:
        where += " AND d.FeatureId = ?"
        params.append(feature_id)
    if dev_item_id is not None:
        where += " AND d.DevItemId = ?"
        params.append(dev_item_id)
    if task_id is not None:
        where += " AND EXISTS (SELECT 1 FROM EngHub_Task tk WHERE tk.DevItemId = d.DevItemId AND tk.TaskId = ?)"
        params.append(task_id)
    if tt_no is not None:
        where += " AND d.OriginTicketVoucherNo = ?"
        params.append(tt_no)
    if user_id is not None:
        where += (
            " AND EXISTS (SELECT 1 FROM EngHub_Task tk "
            "JOIN EngHub_AssignmentHistory ah ON ah.EntityType = 'Task' AND ah.EntityId = tk.TaskId "
            "AND ah.RoleType = 'Developer' AND ah.UnassignedAt IS NULL "
            "WHERE tk.DevItemId = d.DevItemId AND ah.UserId = ?)"
        )
        params.append(user_id)
    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT d.DevItemId, d.Title, f.Name AS FeatureName, d.OriginTicketVoucherNo,
                   c.DisplayName AS CustomerName, s.Name AS StatusName
            FROM EngHub_DevelopmentItem d
            JOIN EngHub_Feature f ON f.FeatureId = d.FeatureId
            JOIN EngHub_Module mo ON mo.ModuleId = f.ModuleId
            JOIN EngHub_Status s ON s.StatusId = d.StatusId
            LEFT JOIN EngHub_OriginType ot ON ot.OriginTypeId = d.OriginTypeId
            LEFT JOIN CustomerMaster c ON c.CustID = d.CustomerId
            {where}
            ORDER BY d.DevItemId DESC
            """,
            *params,
        )
        dev_items = rows_to_dicts(cursor, limit=500)

        result: list[SupportDrivenWorkRow] = []
        for d in dev_items:
            effort_params: list = [d["DevItemId"]]
            effort_where = "WHERE (a.DevItemId = ? OR a.TaskId IN (SELECT TaskId FROM EngHub_Task WHERE DevItemId = ?))"
            effort_params.append(d["DevItemId"])
            effort_where += _date_filter(date_from, date_to, effort_params)
            cursor.execute(
                f"SELECT SUM(a.DurationMinutes) AS TotalMinutes FROM EngHub_Activity a {effort_where}",
                *effort_params,
            )
            effort_row = rows_to_dicts(cursor)
            total_minutes = (effort_row[0]["TotalMinutes"] if effort_row else 0) or 0
            result.append(
                SupportDrivenWorkRow(
                    dev_item_id=d["DevItemId"], title=d["Title"] or "", feature_name=d["FeatureName"] or "",
                    ticket_voucher_no=d["OriginTicketVoucherNo"], customer_name=d["CustomerName"],
                    status_name=d["StatusName"] or "", total_minutes=total_minutes,
                )
            )
    return result


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

class DashboardSummary(BaseModel):
    open_dev_items: int
    open_tasks: int
    minutes_logged_last_7_days: int
    pending_decision_reviews: int


@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary() -> DashboardSummary:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS N FROM EngHub_DevelopmentItem d JOIN EngHub_Status s ON s.StatusId = d.StatusId WHERE s.IsTerminal = 0"
        )
        open_dev_items = rows_to_dicts(cursor)[0]["N"]

        cursor.execute(
            "SELECT COUNT(*) AS N FROM EngHub_Task t JOIN EngHub_Status s ON s.StatusId = t.StatusId WHERE s.IsTerminal = 0"
        )
        open_tasks = rows_to_dicts(cursor)[0]["N"]

        cursor.execute(
            "SELECT SUM(DurationMinutes) AS N FROM EngHub_Activity WHERE OccurredAt >= DATEADD(day, -7, SYSUTCDATETIME())"
        )
        minutes_logged = rows_to_dicts(cursor)[0]["N"] or 0

        cursor.execute(
            "SELECT COUNT(*) AS N FROM EngHub_Decision d JOIN EngHub_Status s ON s.StatusId = d.StatusId "
            "WHERE s.Name = 'Under Review' AND d.ReviewDate IS NOT NULL AND d.ReviewDate <= SYSUTCDATETIME()"
        )
        pending_reviews = rows_to_dicts(cursor)[0]["N"]

    return DashboardSummary(
        open_dev_items=open_dev_items, open_tasks=open_tasks,
        minutes_logged_last_7_days=minutes_logged, pending_decision_reviews=pending_reviews,
    )


class RecentActivityRow(BaseModel):
    activity_id: int
    activity_type_name: str
    description: str | None = None
    duration_minutes: int | None = None
    occurred_at: str
    logged_by_name: str


@router.get("/dashboard/recent-activity", response_model=list[RecentActivityRow])
def get_recent_activity() -> list[RecentActivityRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 20 a.ActivityId, at.Name AS ActivityTypeName, a.Description, a.DurationMinutes, "
            "a.OccurredAt, u.Name AS LoggedByName "
            "FROM EngHub_Activity a "
            "JOIN EngHub_ActivityType at ON at.ActivityTypeId = a.ActivityTypeId "
            "JOIN UserMaster u ON u.UserID = a.LoggedByUserId "
            "ORDER BY a.OccurredAt DESC"
        )
        rows = rows_to_dicts(cursor)
    return [
        RecentActivityRow(
            activity_id=r["ActivityId"], activity_type_name=r["ActivityTypeName"] or "", description=r["Description"],
            duration_minutes=r["DurationMinutes"], occurred_at=r["OccurredAt"].isoformat() if r["OccurredAt"] else "",
            logged_by_name=r["LoggedByName"] or "",
        )
        for r in rows
    ]
