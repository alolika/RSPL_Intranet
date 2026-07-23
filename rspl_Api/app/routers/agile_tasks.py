"""Mirrors Agile_project_Manegment/TaskTracking.aspx.vb, AddTask.aspx.vb,
AddAction.aspx.vb, WorkTracking.aspx.vb, BrachWiseUserTask.aspx.vb.

Real findings (confirmed live via OBJECT_ID / INFORMATION_SCHEMA lookups —
this module has by far the most infrastructure gaps of any section in this
migration; the user was informed and chose to proceed with best-effort
substitutes rather than pausing the whole module):

- `Proc_UpdateTaskWork`, `Proc_GetActionDetail`, `Proc_CheckUserType`,
  `Proc_GetTaskTrackingDetail`, and `TaskType` (a lookup table the source's
  AddTask/AddAction dropdowns read from) do not exist on this database at
  all. There is also no separate task-action/work-log table anywhere in the
  schema — `Tasks` only carries current-state fields (ActualHours,
  RemainHours, StatusID), not a timestamped per-entry history — so the
  "action history" grid on AddAction/WorkTracking has no possible data
  source here and is left genuinely empty rather than faked.
- `Proc_GetTasksDetails` DOES exist, but its real deployed signature
  (`@ProjectId,@ModuleID,@ProductBacklogID,@StoryId,@TasksId` — 5 params)
  is narrower than every VB call site expects (7-12 positional args) and
  its result set lacks Module/AssignedBy/AssignedTo/ForDate columns
  entirely. Rather than call it and get a truncated result, task listing
  here is a direct SELECT off the real `Tasks` table (confirmed schema)
  joined to StoryMaster/StatusMaster, which yields a richer, more useful
  result than the proc would.
- `Tasks` has no AssignedTo/AssignedBy distinction in its real schema
  (only `UserID` creator and `MUserID` modifier) — despite AddTask's own
  form having separate dropdowns for both. Both map to the creator's name
  here; a real assignment concept isn't persisted in this table.
- `Proc_InsertUpdateTaskMaster` DOES exist and is used for the actual
  add/update-task write, but its real 17-param signature (confirmed via
  INFORMATION_SCHEMA) differs from what several VB call sites pass
  (`@ManHours`/`@WorkedHours` don't exist as real param names — the real
  ones are `@StoryHours`/`@ActualHours`/`@RemainHours`) — called here
  against the confirmed real signature, not the source's calls verbatim.
- "Log work" (TaskTracking's inline hours entry / AddAction's save) has no
  real proc to call (`Proc_UpdateTaskWork` missing) — substituted with a
  direct UPDATE against `Tasks` (adds worked hours to ActualHours,
  subtracts from RemainHours, sets StatusID), which achieves the practical
  effect even though no proc-level side effects (if any existed) run.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-tasks"])


class LookupOption(BaseModel):
    label: str
    value: int


class TaskTrackingRow(BaseModel):
    project_id: int
    project_name: str
    module_id: int
    module_name: str
    task_id: int
    task: str
    status_id: int
    status_name: str
    for_date: str | None
    assigned_by: str
    assigned_to: str
    man_hours: float
    total_worked_hours: float
    work_complete: float
    current_task: bool
    work_hours_to_add: float


class UpdateTaskWorkRequest(BaseModel):
    project_id: int
    task_id: int
    worked_hours: float
    work_percent: float
    current_task: bool
    for_date: str


class AddTaskForm(BaseModel):
    project_id: int
    module_id: int
    task_details: str
    task_hours: str
    task_type_id: int
    assigned_to: int
    assigned_by: int
    status_id: int
    for_date: str
    is_current_task: bool


class TaskDetail(BaseModel):
    task: str
    current_task: bool
    work_complete: float
    man_hours: float
    task_type_id: int
    status_id: int


class ActionHistoryRow(BaseModel):
    action_id: int
    action_description: str
    task_type: str
    user_name: str
    assigned_by: str
    assigned_to: str
    total_worked_hours: float
    date: str


class AddActionForm(BaseModel):
    action: str
    worked_hours_type: str
    worked_hours: float
    for_date: str
    percent_complete: float
    task_type_id: int
    assigned_by: int
    assigned_to: int
    status_id: int
    is_current_task: bool


class WorkTrackingRow(BaseModel):
    project_id: int
    project_name: str
    module_id: int
    module_name: str
    task_id: int
    task: str
    action_description: str
    status_name: str
    date: str | None
    assigned_by: str
    assigned_to: str
    man_hours: float
    total_worked_hours: float
    work_complete: float
    current_task: bool


class BranchWiseTaskRow(BaseModel):
    user_id: int
    product_backlog_id: int
    developer: str
    task: str
    project_name: str
    priority: str
    man_hours: float
    status: str
    start_date: str | None
    internal_external: str
    core_skills: list[str]


def _percent(actual: float | None, story: float | None) -> float:
    story = story or 0
    if story <= 0:
        return 0
    return round(min(100, (actual or 0) / story * 100), 1)


_TASKS_SELECT = (
    "select T.TaskID, T.ProjectID, PM.ProjectName, T.StoryID, T.Task, T.StatusID, SM.StatusName, "
    "T.ActualHours, T.RemainHours, T.StoryHours, T.Date, U.Name CreatedBy, "
    "ST.ProductBacklogID, AM.ModuleID, AM.ModuleName "
    "from Tasks T "
    "left outer join Agile_ProjectMaster PM on PM.ProjectID = T.ProjectID "
    "left outer join StatusMaster SM on SM.StatusId = T.StatusID "
    "left outer join UserMaster U on U.UserID = T.UserID "
    "left outer join StoryMaster ST on ST.StoryID = T.StoryID and ST.ProjectID = T.ProjectID "
    "left outer join Agile_productbacklogmaster PB on PB.ProductBacklogId = ST.ProductBacklogID and PB.ProjectId = T.ProjectID "
    "left outer join agile_ModuleMaster AM on AM.ModuleID = PB.ModuleID"
)


@router.get("/task-statuses", response_model=list[LookupOption])
def get_task_statuses() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("select StatusID, Name from StatusMaster where Disabled = 0 order by Name")
        return [LookupOption(label=r["Name"] or "", value=r["StatusID"]) for r in rows_to_dicts(cursor)]


@router.get("/my-tasks", response_model=list[TaskTrackingRow])
def get_my_tasks(project_id: int = 0) -> list[TaskTrackingRow]:
    where = " where 1=1"
    params: list = []
    if project_id:
        where += " and T.ProjectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(f"{_TASKS_SELECT}{where} order by T.Date desc", *params)
        rows = rows_to_dicts(cursor)
    return [
        TaskTrackingRow(
            project_id=r["ProjectID"], project_name=r["ProjectName"] or "", module_id=r["ModuleID"] or 0,
            module_name=r["ModuleName"] or "", task_id=r["TaskID"], task=r["Task"] or "", status_id=r["StatusID"] or 0,
            status_name=r["StatusName"] or "", for_date=r["Date"].isoformat() if r["Date"] else None,
            assigned_by=r["CreatedBy"] or "", assigned_to=r["CreatedBy"] or "", man_hours=float(r["StoryHours"] or 0),
            total_worked_hours=float(r["ActualHours"] or 0), work_complete=_percent(r["ActualHours"], r["StoryHours"]),
            current_task=False, work_hours_to_add=0,
        )
        for r in rows
    ]


@router.post("/tasks/log-work")
def update_task_work(body: UpdateTaskWorkRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "update Tasks set ActualHours = ISNULL(ActualHours,0) + ?, RemainHours = CASE WHEN ISNULL(RemainHours,0) - ? < 0 THEN 0 ELSE ISNULL(RemainHours,0) - ? END "
            "where TaskID = ? and ProjectID = ?",
            body.worked_hours, body.worked_hours, body.worked_hours, body.task_id, body.project_id,
        )
    return {"result_msg": "Task work updated successfully."}


@router.get("/add-task-lookups")
def get_add_task_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("select ModuleID, ModuleName from agile_ModuleMaster where ProjectID = ? and Enabled = 1 order by ModuleName", project_id)
        modules = [{"label": r["ModuleName"] or "", "value": r["ModuleID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "select um.Name, um.userID from UserMaster um inner join Agile_assignteam ast on um.userID = ast.userID "
            "where Active = 1 and ast.Projectid = ? order by um.Name", project_id,
        )
        users = [{"label": r["Name"] or "", "value": r["userID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select StatusID, Name from StatusMaster where Disabled = 0 order by Name")
        statuses = [{"label": r["Name"] or "", "value": r["StatusID"]} for r in rows_to_dicts(cursor)]
    # TaskType table doesn't exist on this database — Tasks itself has no
    # TaskTypeID column either, so this is a static, non-persisted list.
    task_types = [{"label": "Development", "value": 1}, {"label": "Testing", "value": 2}, {"label": "Bug Fix", "value": 3}]
    return {"modules": modules, "taskTypes": task_types, "users": users, "statuses": statuses}


@router.post("/tasks")
def add_task(form: AddTaskForm, current_user_id: int = 0) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec Proc_InsertUpdateTaskMaster @TaskID=0,@ProjectID=?,@StoryID=0,@feature='',@task=?,@StatusID=?,@Enabled=1,"
            "@StoryHours=?,@ActualHours=0,@RemainHours=?,@UserID=?,@Date=GETDATE(),@Time=GETDATE(),@MUserID=?,@MDate=GETDATE(),@MTime=GETDATE(),@TransType='insert'",
            form.project_id, form.task_details, form.status_id, form.task_hours or 0, form.task_hours or 0,
            form.assigned_by, form.assigned_by,
        )
        cursor.execute("select ISNULL(MAX(TaskID), 0) from Tasks where ProjectID = ?", form.project_id)
        task_id = cursor.fetchone()[0]
    return {"result_code": 100, "result_msg": "Task added successfully.", "task_id": task_id}


@router.get("/tasks/{project_id}/{task_id}", response_model=TaskDetail)
def get_task_detail(project_id: int, task_id: int, module_id: int = 0) -> TaskDetail:
    with get_cursor() as cursor:
        cursor.execute(
            "select Task, ActualHours, StoryHours, StatusID from Tasks where ProjectID = ? and TaskID = ?",
            project_id, task_id,
        )
        rows = rows_to_dicts(cursor)
    if not rows:
        return TaskDetail(task="", current_task=False, work_complete=0, man_hours=0, task_type_id=0, status_id=0)
    r = rows[0]
    return TaskDetail(
        task=r["Task"] or "", current_task=False, work_complete=_percent(r["ActualHours"], r["StoryHours"]),
        man_hours=float(r["StoryHours"] or 0), task_type_id=0, status_id=r["StatusID"] or 0,
    )


@router.get("/tasks/{project_id}/{task_id}/actions", response_model=list[ActionHistoryRow])
def get_action_history(project_id: int, task_id: int, module_id: int = 0) -> list[ActionHistoryRow]:
    # Proc_GetActionDetail is confirmed missing, and no per-entry task-action
    # log table exists anywhere in this schema — genuinely no data source.
    return []


@router.post("/tasks/{project_id}/{task_id}/actions")
def add_action(project_id: int, task_id: int, form: AddActionForm) -> dict:
    hours = form.worked_hours
    if form.worked_hours_type == "Minute":
        hours = form.worked_hours / 60
    elif form.worked_hours_type == "Day":
        hours = form.worked_hours * 8
    with get_cursor() as cursor:
        cursor.execute(
            "update Tasks set ActualHours = ISNULL(ActualHours,0) + ?, "
            "RemainHours = CASE WHEN ISNULL(RemainHours,0) - ? < 0 THEN 0 ELSE ISNULL(RemainHours,0) - ? END, StatusID = ? "
            "where TaskID = ? and ProjectID = ?",
            hours, hours, hours, form.status_id, task_id, project_id,
        )
    return {"result_code": 100, "result_msg": "Action added successfully.", "task_id": task_id}


@router.get("/work-tracking", response_model=list[WorkTrackingRow])
def get_work_tracking(project_id: int = 0) -> list[WorkTrackingRow]:
    where = " where 1=1"
    params: list = []
    if project_id:
        where += " and T.ProjectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(f"{_TASKS_SELECT}{where} order by T.Date desc", *params)
        rows = rows_to_dicts(cursor)
    return [
        WorkTrackingRow(
            project_id=r["ProjectID"], project_name=r["ProjectName"] or "", module_id=r["ModuleID"] or 0,
            module_name=r["ModuleName"] or "", task_id=r["TaskID"], task=r["Task"] or "", action_description="",
            status_name=r["StatusName"] or "", date=r["Date"].isoformat() if r["Date"] else None,
            assigned_by=r["CreatedBy"] or "", assigned_to=r["CreatedBy"] or "", man_hours=float(r["StoryHours"] or 0),
            total_worked_hours=float(r["ActualHours"] or 0), work_complete=_percent(r["ActualHours"], r["StoryHours"]),
            current_task=False,
        )
        for r in rows
    ]


@router.get("/branches", response_model=list[LookupOption])
def get_branches() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("select distinct City from UserMaster where City is not null and City <> '' order by City")
        return [LookupOption(label=r["City"], value=i + 1) for i, r in enumerate(rows_to_dicts(cursor))]


@router.get("/branch-wise-tasks", response_model=list[BranchWiseTaskRow])
def get_branch_wise_tasks(city: str) -> list[BranchWiseTaskRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select um.Name as Developer, pm.projectName, pm.CoreSkill, pm.InternalExternal, apb.ProjectId, "
            "apb.ProductBacklogId, apb.Description as Task, apb.ManHours, prim.Priority, sm.startdate, "
            "stm.statusName as Status, um.UserID "
            "from usermaster um "
            "left join StoryMaster sm on um.UserID = sm.Owner and sm.StatusID <> 4 "
            "left join Agile_productbacklogmaster apb on apb.ProductBacklogId = sm.ProductBacklogID and sm.Owner = apb.UserId "
            "left join PriorityMaster prim on prim.PriorityID = sm.PriorityID "
            "left join StatusMaster stm on stm.StatusId = sm.StatusId "
            "left join Agile_ProjectMaster pm on apb.ProjectID = pm.ProjectID "
            "where um.City = ? and um.Enabled = 1 order by Developer",
            city,
        )
        rows = rows_to_dicts(cursor)
    return [
        BranchWiseTaskRow(
            user_id=r["UserID"], product_backlog_id=r["ProductBacklogId"] or 0, developer=r["Developer"] or "",
            task=r["Task"] or "", project_name=r["projectName"] or "", priority=r["Priority"] or "",
            man_hours=float(r["ManHours"] or 0), status=r["Status"] or "",
            start_date=r["startdate"].isoformat() if r["startdate"] else None,
            internal_external=r["InternalExternal"] or "", core_skills=(r["CoreSkill"] or "").split(",") if r["CoreSkill"] else [],
        )
        for r in rows
    ]


class UpdateBranchTaskTypeRequest(BaseModel):
    internal_external: str


class UpdateBranchTaskCoreSkillsRequest(BaseModel):
    core_skills: list[str]


@router.post("/branch-wise-tasks/{project_id}/{product_backlog_id}/type")
def update_branch_task_type(project_id: int, product_backlog_id: int, body: UpdateBranchTaskTypeRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute("EXEC Insert_CoreSkill_InternalExternal ?, NULL, ?, 2", project_id, 1 if body.internal_external == "Internal" else 0)
    return {"success": True}


@router.post("/branch-wise-tasks/{project_id}/{product_backlog_id}/core-skills")
def update_branch_task_core_skills(project_id: int, product_backlog_id: int, body: UpdateBranchTaskCoreSkillsRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Update Agile_productbacklogmaster set CoreSkill = ? where ProjectId = ? and ProductBacklogId = ?",
            ",".join(body.core_skills), project_id, product_backlog_id,
        )
    return {"success": True}
