"""Mirrors Agile_project_Manegment/Dashboard.aspx.vb — a 3-tab role
dashboard (Top Management/Project Lead/User), consolidated into one Chart.js
doughnut on the Angular side (see mock docstring). Not linked from the
Agile menu in the source. All queries here are direct SQL against real,
confirmed-existing tables — no missing-proc gaps in this page.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-dashboard"])


class ProjectStatusPercent(BaseModel):
    project_name: str
    percent_complete: float


class BalanceRevenueRow(BaseModel):
    project_name: str
    payment_received: float
    work_done: float


class ProjectStatusRow(BaseModel):
    project_name: str
    status: str


class ProductBacklogTrackingRow(BaseModel):
    product_backlog: str
    effort_size: str
    man_hours: float
    priority: str
    developer: str
    status: str
    work_complete: float


class UserTaskRow(BaseModel):
    name: str
    description: str
    project_name: str
    man_hours: float
    priority: str
    status: str


@router.get("/dashboard/project-status-chart", response_model=list[ProjectStatusPercent])
def get_project_status_chart() -> list[ProjectStatusPercent]:
    with get_cursor() as cursor:
        cursor.execute("select ProjectName, WorkDone from Agile_ProjectMaster where Enabled = 1 order by ProjectName")
        rows = rows_to_dicts(cursor)
    return [ProjectStatusPercent(project_name=r["ProjectName"] or "", percent_complete=float(r["WorkDone"] or 0)) for r in rows]


@router.get("/dashboard/balance-revenue", response_model=list[BalanceRevenueRow])
def get_balance_revenue() -> list[BalanceRevenueRow]:
    with get_cursor() as cursor:
        cursor.execute("select ProjectName, perRecieved, WorkDone from Agile_ProjectMaster where Enabled = 1 order by ProjectName")
        rows = rows_to_dicts(cursor)
    return [BalanceRevenueRow(project_name=r["ProjectName"] or "", payment_received=float(r["perRecieved"] or 0), work_done=float(r["WorkDone"] or 0)) for r in rows]


@router.get("/dashboard/project-status-list", response_model=list[ProjectStatusRow])
def get_project_status_list() -> list[ProjectStatusRow]:
    with get_cursor() as cursor:
        cursor.execute("select ProjectName, case when Enabled = 0 then 'In-Active' else 'Active' end Status from Agile_ProjectMaster order by Status, ProjectName")
        rows = rows_to_dicts(cursor)
    return [ProjectStatusRow(project_name=r["ProjectName"] or "", status=r["Status"]) for r in rows]


@router.get("/dashboard/product-backlog-tracking", response_model=list[ProductBacklogTrackingRow])
def get_product_backlog_tracking(project_id: int = 0) -> list[ProductBacklogTrackingRow]:
    where = ""
    params: list = []
    if project_id:
        where = " where pbm.projectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(
            "select pbm.Description ProductBacklog, efs.EffortSize, pbm.ManHours, pm.Priority, um.Name Developer, "
            "stm.StatusName Status, "
            "ISNULL((select avg(isnull(sm.PercentComplet, 0)) from StoryMaster sm where sm.ProjectId = pbm.ProjectID and sm.ProductBacklogId = pbm.ProductBacklogId), 0) WorkComplete "
            "from Agile_productbacklogmaster pbm "
            "inner join EffortSizeMaster efs on pbm.EffortSizeId = efs.EffortSizeID "
            "inner join PriorityMaster pm on pbm.PriorityId = pm.PriorityID "
            "inner join userMaster um on pbm.UserId = um.UserID "
            "inner join StatusMaster stm on pbm.StatusId = stm.StatusId"
            f"{where} order by pbm.Description",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        ProductBacklogTrackingRow(
            product_backlog=r["ProductBacklog"] or "", effort_size=r["EffortSize"] or "", man_hours=float(r["ManHours"] or 0),
            priority=r["Priority"] or "", developer=r["Developer"] or "", status=r["Status"] or "",
            work_complete=float(r["WorkComplete"] or 0),
        )
        for r in rows
    ]


@router.get("/dashboard/user-tasks", response_model=list[UserTaskRow])
def get_user_wise_task_list(user_id: int, project_id: int = 0) -> list[UserTaskRow]:
    where = " where sm.Owner = ?"
    params: list = [user_id]
    if project_id:
        where += " and sm.ProjectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(
            "select um.Name, pb.Description, pm.ProjectName, pb.ManHours, prim.Priority, stm.statusName Status "
            "from storymaster sm "
            "inner join Agile_productbacklogmaster pb on sm.ProductbacklogID = pb.ProductbacklogId and sm.ProjectID = pb.ProjectId "
            "inner join Agile_ProjectMaster pm on pm.ProjectID = sm.ProjectID "
            "inner join statusMaster stm on stm.statusId = sm.StatusID "
            "inner join priorityMaster prim on prim.PriorityID = pb.PriorityId "
            "inner join userMaster um on um.UserID = sm.Owner"
            f"{where} order by pb.Description",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        UserTaskRow(name=r["Name"] or "", description=r["Description"] or "", project_name=r["ProjectName"] or "", man_hours=float(r["ManHours"] or 0), priority=r["Priority"] or "", status=r["Status"] or "")
        for r in rows
    ]
