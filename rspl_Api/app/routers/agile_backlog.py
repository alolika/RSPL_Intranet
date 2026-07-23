"""Mirrors Agile_project_Manegment/ProductBacklogDetails.aspx.vb,
StoryMaster.aspx.vb (Story/Task/Acceptance-Criteria tabs), and
Sprint Planning.aspx.vb.

Real gaps confirmed live (see agile_tasks.py's docstring for the module-wide
context): `Proc_GetEmptySprints` and `Proc_AddRemoveSprints` don't exist,
and neither does the `Agile_Sprints` lookup table Sprint Planning's combo
boxes read from. A real `AssignSprint` table does exist though — just a
per-project `SprintCount` counter — which the source itself references in
commented-out code as the intended mechanism for tracking how many sprints
a project has. Add/Delete Sprint here increments/decrements that counter
directly, and "available sprint numbers" is computed as the range
1..SprintCount minus sprint numbers already used in Agile_sprintmaster,
reproducing the source's "not yet assigned" combo-fill logic without the
missing lookup table.

Also confirmed live: `Agile_sprintmaster.SprintNo` is nvarchar storing full
"Sprint N" labels (e.g. "Sprint 0"), not a plain integer despite every VB
call site treating it as one — `_sprint_num()`/`_sprint_label()` convert
between that real stored format and the Angular contract's `sprintNo: number`.
"""

import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-backlog"])


def _sprint_num(s: str | None) -> int:
    if not s:
        return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0


def _sprint_label(n: int) -> str:
    return f"Sprint {n}"


class LookupOption(BaseModel):
    label: str
    value: int


class ProductBacklogForm(BaseModel):
    project_id: int
    module_id: int
    description: str
    priority_id: int
    complexity_id: int
    effort_size_id: int
    man_hours: float
    developer_id: int
    tester_id: int
    comments: str


class ProductBacklogRow(BaseModel):
    project_id: int
    product_backlog_id: int
    project_name: str
    module_id: int
    module_name: str
    description: str
    complexity_id: int
    complexity: str
    effort_size_id: int
    effort_size: str
    hours: float
    priority_id: int
    priority: str
    developer_id: int
    developer: str
    tester_id: int
    tester: str
    status_name: str
    attachments: list[str]
    comments: str


_PB_SELECT = (
    "select PB.ProjectId, PB.ProductBacklogId, PM.ProjectName, PB.ModuleID, AM.ModuleName, PB.Description, "
    "PB.ComplexityId, CM.Complexity, PB.EffortSizeId, ESM.EffortSize, PB.ManHours, PB.PriorityId, PR.Priority, "
    "PB.UserId, UD.Name Developer, PB.TesterId, UT.Name Tester, ST.StatusName, PB.Attachments, PB.Comments "
    "from Agile_productbacklogmaster PB "
    "left outer join Agile_ProjectMaster PM on PM.ProjectID = PB.ProjectId "
    "left outer join agile_ModuleMaster AM on AM.ModuleID = PB.ModuleID "
    "left outer join ComplexityMaster CM on CM.ComplexityID = PB.ComplexityId "
    "left outer join EffortSizeMaster ESM on ESM.EffortSizeID = PB.EffortSizeId "
    "left outer join PriorityMaster PR on PR.PriorityID = PB.PriorityId "
    "left outer join UserMaster UD on UD.UserID = PB.UserId "
    "left outer join UserMaster UT on UT.UserID = PB.TesterId "
    "left outer join StatusMaster ST on ST.StatusId = PB.StatusId"
)


def _pb_row(r: dict) -> ProductBacklogRow:
    return ProductBacklogRow(
        project_id=r["ProjectId"], product_backlog_id=r["ProductBacklogId"], project_name=r["ProjectName"] or "",
        module_id=r["ModuleID"] or 0, module_name=r["ModuleName"] or "", description=r["Description"] or "",
        complexity_id=r["ComplexityId"] or 0, complexity=r["Complexity"] or "", effort_size_id=r["EffortSizeId"] or 0,
        effort_size=r["EffortSize"] or "", hours=float(r["ManHours"] or 0), priority_id=r["PriorityId"] or 0,
        priority=r["Priority"] or "", developer_id=r["UserId"] or 0, developer=r["Developer"] or "",
        tester_id=r["TesterId"] or 0, tester=r["Tester"] or "", status_name=r["StatusName"] or "",
        attachments=[r["Attachments"]] if r.get("Attachments") else [], comments=r["Comments"] or "",
    )


@router.get("/product-backlog-form-lookups")
def get_product_backlog_form_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("select ModuleID, ModuleName from agile_ModuleMaster where ProjectID = ? and Enabled = 1 order by ModuleName", project_id)
        modules = [{"label": r["ModuleName"] or "", "value": r["ModuleID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select EffortSizeId, EffortSize, Hours from EffortSizeMaster order by EffortSize")
        sizes = [{"label": f"{r['EffortSize']} - {r['Hours']} (Man hrs)", "value": r["EffortSizeId"], "hours": r["Hours"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select PriorityID, Priority from PriorityMaster order by Priority")
        priorities = [{"label": r["Priority"] or "", "value": r["PriorityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select ComplexityID, Complexity from ComplexityMaster order by Complexity")
        complexities = [{"label": r["Complexity"] or "", "value": r["ComplexityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "select um.Name, um.userID from UserMaster um inner join Agile_assignteam ast on um.userID = ast.userID "
            "where ast.TypeID = 1 and Active = 1 and ast.Projectid = ? order by um.Name", project_id,
        )
        developers = [{"label": r["Name"] or "", "value": r["userID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "select um.Name, um.userID from UserMaster um inner join Agile_assignteam ast on um.userID = ast.userID "
            "where ast.TypeID = 2 and Active = 1 and ast.Projectid = ? order by um.Name", project_id,
        )
        testers = [{"label": r["Name"] or "", "value": r["userID"]} for r in rows_to_dicts(cursor)]
    return {"modules": modules, "sizes": sizes, "priorities": priorities, "complexities": complexities, "developers": developers, "testers": testers}


@router.post("/product-backlog")
def add_product_backlog(form: ProductBacklogForm) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec proc_AddUpdateProductBacklogMaster @ProjectId=?,@productbacklogid1=0,@Description=?,@EffortSizeId=?,"
            "@ManHours=?,@PriorityId=?,@UserId=?,@TesterId=?,@StatusId=1,@Comments=?,@ComplexityId=?,@SprintId=0,"
            "@Transtype='Insert',@Attachments='',@ModuleID=?",
            form.project_id, form.description, form.effort_size_id, form.man_hours, form.priority_id,
            form.developer_id, form.tester_id, form.comments, form.complexity_id, form.module_id,
        )
    return {"success": True, "message": "Product backlog saved successfully."}


@router.get("/product-backlog", response_model=list[ProductBacklogRow])
def get_product_backlog_list(project_id: int, module_id: int = 0) -> list[ProductBacklogRow]:
    where = " where PB.ProjectId = ?"
    params: list = [project_id]
    if module_id:
        where += " and PB.ModuleID = ?"
        params.append(module_id)
    with get_cursor() as cursor:
        cursor.execute(f"{_PB_SELECT}{where} order by PB.ProductBacklogId desc", *params)
        rows = rows_to_dicts(cursor)
    return [_pb_row(r) for r in rows]


@router.put("/product-backlog/{product_backlog_id}")
def update_product_backlog_row(product_backlog_id: int, row: ProductBacklogRow) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec proc_AddUpdateProductBacklogMaster @ProjectId=?,@ProductBacklogId1=?,@Description=?,@ComplexityId=?,"
            "@EffortSizeId=?,@ManHours=?,@PriorityId=?,@UserId=?,@TesterId=?,@StatusId=1,@Comments=?,@SprintId=-1,"
            "@Transtype='Update',@Attachments='',@ModuleID=?",
            row.project_id, product_backlog_id, row.description, row.complexity_id, row.effort_size_id, row.hours,
            row.priority_id, row.developer_id, row.tester_id, row.comments, row.module_id,
        )
    return {"success": True}


# -------------------- StoryMaster: Story tab --------------------


class StoryForm(BaseModel):
    project_id: int
    module_id: int
    product_backlog_id: int
    story_title: str
    as_a: str
    i_wish_to: str
    so_that_i_can: str
    complexity_id: int
    priority_id: int
    effort_size_id: int
    man_days: float
    man_hours: float
    start_date: str
    end_date: str
    developer_id: int
    tester_id: int
    comments: str


class StoryRow(BaseModel):
    story_id: int
    project_id: int
    module_name: str
    product_backlog_description: str
    feature: str
    i_wish_to: str
    so_that: str
    start_date: str | None
    end_date: str | None
    complexity: str
    priority: str
    status_name: str
    effort_size: str
    man_hours: float
    man_days: float
    developer: str
    tester: str
    comments: str


@router.get("/story-form-lookups")
def get_story_form_lookups(project_id: int, module_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "select ProductBacklogId, Description from Agile_productbacklogmaster where ModuleID = ? and ProjectId = ? order by Description",
            module_id, project_id,
        )
        backlogs = [{"label": r["Description"] or "", "value": r["ProductBacklogId"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select complexity, complexityID from ComplexityMaster order by complexity")
        complexities = [{"label": r["complexity"] or "", "value": r["complexityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select Priority, PriorityID from PriorityMaster order by Priority")
        priorities = [{"label": r["Priority"] or "", "value": r["PriorityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select EffortSize, Hours, EffortSizeID from EffortSizeMaster order by EffortSize")
        effort_sizes = [{"label": r["EffortSize"] or "", "value": r["EffortSizeID"], "days": 1, "hours": r["Hours"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "Select AT.UserId, U.Name from Agile_assignteam AT inner join UserMaster U On AT.userId = U.UserID "
            "Where At.typeID = 1 And ProjectID = ? Order by Name", project_id,
        )
        developers = [{"label": r["Name"] or "", "value": r["UserId"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "Select AT.UserId, U.Name from Agile_assignteam AT inner join UserMaster U On AT.userId = U.UserID "
            "Where At.typeID = 2 And ProjectID = ? Order by Name", project_id,
        )
        testers = [{"label": r["Name"] or "", "value": r["UserId"]} for r in rows_to_dicts(cursor)]
    as_a_options = [{"label": "End User", "value": 1}, {"label": "Administrator", "value": 2}]
    return {
        "productBacklogs": backlogs, "asAOptions": as_a_options, "complexities": complexities, "priorities": priorities,
        "effortSizes": effort_sizes, "developers": developers, "testers": testers,
    }


@router.post("/stories")
def add_story(form: StoryForm) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec PROC_StoryMasterInsUpdate @ProjectID=?,@ProductBacklogId=?,@StoryID=0,@AsA=?,@Owner=?,@tester=?,"
            "@Feature=?,@FeatureComplexityID=?,@PriorityID=?,@IwishTodesc=?,@Sothatdesc=?,@EffortSizeId=?,@ManHours=?,"
            "@ManDays=?,@StartDate=?,@EndDate=?,@ActualStartDate=?,@ActualEndDate=?,@Enabled=1,@DetailEstimation=?,"
            "@ActualHours=0,@StatusId=1,@UserID=?,@comments=?,@MUserID=?,@TarnsType='insert'",
            form.project_id, form.product_backlog_id, form.as_a, form.developer_id, form.tester_id, form.story_title,
            form.complexity_id, form.priority_id, form.i_wish_to, form.so_that_i_can, form.effort_size_id,
            form.man_hours, form.man_days, form.start_date, form.end_date, form.start_date, form.end_date,
            form.man_hours, form.developer_id, form.comments, form.developer_id,
        )
        cursor.execute("select ISNULL(MAX(StoryID), 0) from StoryMaster where ProjectID = ?", form.project_id)
        story_id = cursor.fetchone()[0]
    return {"result_code": 100, "result_msg": "Story saved successfully.", "story_id": story_id}


@router.get("/stories", response_model=list[StoryRow])
def get_story_list(project_id: int, module_id: int = 0, product_backlog_id: int = 0) -> list[StoryRow]:
    where = " where ST.ProjectID = ?"
    params: list = [project_id]
    if product_backlog_id:
        where += " and ST.ProductBacklogID = ?"
        params.append(product_backlog_id)
    if module_id:
        where += " and PB.ModuleID = ?"
        params.append(module_id)
    with get_cursor() as cursor:
        cursor.execute(
            "select ST.StoryID, ST.ProjectID, AM.ModuleName, PB.Description ProductBacklogDescription, ST.Feature, "
            "ST.IwishTodesc, ST.Sothatdesc, ST.StartDate, ST.EndDate, CM.Complexity, PR.Priority, SM.StatusName, "
            "ESM.EffortSize, ST.ManHours, ST.ManDays, UD.Name Developer, UT.Name Tester, ST.Comments "
            "from StoryMaster ST "
            "left outer join Agile_productbacklogmaster PB on PB.ProductBacklogId = ST.ProductBacklogID and PB.ProjectId = ST.ProjectID "
            "left outer join agile_ModuleMaster AM on AM.ModuleID = PB.ModuleID "
            "left outer join ComplexityMaster CM on CM.ComplexityID = ST.FeatureComplexityID "
            "left outer join PriorityMaster PR on PR.PriorityID = ST.PriorityID "
            "left outer join StatusMaster SM on SM.StatusId = ST.StatusID "
            "left outer join EffortSizeMaster ESM on ESM.EffortSizeID = ST.EffortSizeID "
            "left outer join UserMaster UD on UD.UserID = ST.Owner "
            "left outer join UserMaster UT on UT.UserID = ST.Tester"
            f"{where} order by ST.StoryID desc",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        StoryRow(
            story_id=r["StoryID"], project_id=r["ProjectID"], module_name=r["ModuleName"] or "",
            product_backlog_description=r["ProductBacklogDescription"] or "", feature=r["Feature"] or "",
            i_wish_to=r["IwishTodesc"] or "", so_that=r["Sothatdesc"] or "",
            start_date=r["StartDate"].isoformat() if r["StartDate"] else None,
            end_date=r["EndDate"].isoformat() if r["EndDate"] else None, complexity=r["Complexity"] or "",
            priority=r["Priority"] or "", status_name=r["StatusName"] or "", effort_size=r["EffortSize"] or "",
            man_hours=float(r["ManHours"] or 0), man_days=float(r["ManDays"] or 0), developer=r["Developer"] or "",
            tester=r["Tester"] or "", comments=r["Comments"] or "",
        )
        for r in rows
    ]


@router.put("/stories/{story_id}")
def update_story_row(story_id: int, row: StoryRow) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "update StoryMaster set IwishTodesc = ?, Sothatdesc = ?, StartDate = ?, EndDate = ?, Comments = ? where StoryID = ? and ProjectID = ?",
            row.i_wish_to, row.so_that, row.start_date, row.end_date, row.comments, story_id, row.project_id,
        )
    return {"success": True}


# -------------------- StoryMaster: Task tab --------------------


class StoryTaskForm(BaseModel):
    project_id: int
    module_id: int
    story_id: int
    task_details: str
    task_hours: float


class StoryTaskRow(BaseModel):
    task_id: int
    task: str
    status_name: str
    hours: float


@router.get("/task-form-lookups")
def get_task_form_lookups(project_id: int, module_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "select sm.StoryID, sm.Feature from agile_ProductBAckLogMaster pb inner join StoryMaster sm "
            "on pb.ProductbacklogID = sm.ProductbacklogID and pb.ProjectID = sm.ProjectID "
            "where sm.projectid = ? and pb.ModuleID = ? order by Feature",
            project_id, module_id,
        )
        return {"storyFeatures": [{"label": r["Feature"] or "", "value": r["StoryID"]} for r in rows_to_dicts(cursor)]}


@router.get("/story-hours/{project_id}/{story_id}")
def get_story_hours(project_id: int, story_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("webProc_GetRemainingHrs ?, ?", project_id, story_id)
        rows = rows_to_dicts(cursor)
    if not rows:
        return {"assigned_hours": 0, "remaining_hours": 0}
    r = rows[0]
    keys = list(r.keys())
    assigned = r.get("ManHours") or r.get(keys[0]) or 0
    remaining = r.get("RemainingHours") or r.get(keys[-1]) or 0
    return {"assigned_hours": float(assigned or 0), "remaining_hours": float(remaining or 0)}


@router.post("/story-tasks")
def add_story_task(form: StoryTaskForm) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec Proc_InsertUpdateTaskMaster @TaskID=0,@ProjectID=?,@StoryID=?,@feature='',@task=?,@StatusID=1,@Enabled=1,"
            "@StoryHours=?,@ActualHours=0,@RemainHours=?,@UserID=0,@Date=GETDATE(),@Time=GETDATE(),@MUserID=0,"
            "@MDate=GETDATE(),@MTime=GETDATE(),@TransType='insert'",
            form.project_id, form.story_id, form.task_details, form.task_hours, form.task_hours,
        )
    return {"result_code": 100, "result_msg": "Task saved successfully."}


@router.get("/story-tasks/{story_id}", response_model=list[StoryTaskRow])
def get_tasks_for_story(story_id: int) -> list[StoryTaskRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select t.TaskID, t.Task, sm.StatusName, t.StoryHours from Tasks t "
            "inner join StatusMaster sm on t.StatusID = sm.statusid where t.StoryId = ?",
            story_id,
        )
        rows = rows_to_dicts(cursor)
    return [StoryTaskRow(task_id=r["TaskID"], task=r["Task"] or "", status_name=r["StatusName"] or "", hours=float(r["StoryHours"] or 0)) for r in rows]


# -------------------- StoryMaster: Acceptance Criteria tab --------------------


class AcceptanceCriteriaForm(BaseModel):
    project_id: int
    story_id: int
    task_id: int
    criteria: str


class AcceptanceCriteriaRow(BaseModel):
    criteria_id: int
    story_feature: str
    task: str
    criteria: str


@router.get("/acceptance-criteria-form-lookups")
def get_acceptance_criteria_form_lookups(project_id: int, module_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "select sm.StoryID, sm.Feature from agile_ProductBAckLogMaster pb inner join StoryMaster sm "
            "on pb.ProductbacklogID = sm.ProductbacklogID and pb.ProjectID = sm.ProjectID "
            "where sm.projectid = ? and pb.ModuleID = ? order by Feature",
            project_id, module_id,
        )
        story_features = [{"label": r["Feature"] or "", "value": r["StoryID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select TaskID, Task from Tasks where ProjectID = ?", project_id)
        tasks = [{"label": r["Task"] or "", "value": r["TaskID"]} for r in rows_to_dicts(cursor)]
    return {"storyFeatures": story_features, "tasks": tasks}


@router.post("/acceptance-criteria")
def add_acceptance_criteria(form: AcceptanceCriteriaForm) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "insert into AcceptanceCriteria (ProjectID, StoryID, TaskID, Criteria, Enabled, Date, Time) "
            "values (?, ?, ?, ?, 1, GETDATE(), GETDATE())",
            form.project_id, form.story_id, form.task_id, form.criteria,
        )
    return {"result_code": 100, "result_msg": "Acceptance criteria saved successfully."}


@router.get("/acceptance-criteria", response_model=list[AcceptanceCriteriaRow])
def get_acceptance_criteria_list(project_id: int, story_id: int = 0) -> list[AcceptanceCriteriaRow]:
    where = " where AC.ProjectID = ?"
    params: list = [project_id]
    if story_id:
        where += " and AC.StoryID = ?"
        params.append(story_id)
    with get_cursor() as cursor:
        cursor.execute(
            "select AC.AcceptanceID, ST.Feature, T.Task, AC.Criteria from AcceptanceCriteria AC "
            "left outer join StoryMaster ST on ST.StoryID = AC.StoryID and ST.ProjectID = AC.ProjectID "
            "left outer join Tasks T on T.TaskID = AC.TaskID"
            f"{where} order by AC.AcceptanceID desc",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        AcceptanceCriteriaRow(criteria_id=r["AcceptanceID"], story_feature=r["Feature"] or "", task=r["Task"] or "", criteria=r["Criteria"] or "")
        for r in rows
    ]


# -------------------- Sprint Planning --------------------


class SprintBacklogRow(BaseModel):
    project_id: int
    product_backlog_id: int
    module_name: str
    description: str
    complexity: str
    effort_size: str
    man_hours: float
    priority: str
    developer: str
    tester: str
    status: str
    sprint_id: int


class SprintRow(BaseModel):
    sprint_no: int
    start_date: str | None
    end_date: str | None
    completed_percent: float


class SprintStoryTask(BaseModel):
    task: str
    status: str
    man_hours: float
    worked_hours: float
    percent_complete: float


class SprintStoryCriteria(BaseModel):
    criteria: str
    verified: bool


class SprintStoryRow(BaseModel):
    story_id: int
    description: str
    feature: str
    effort_size: str
    man_hours: float
    worked_hours: float
    work_complete: float
    developer: str
    tester: str
    status: str
    as_a: str
    i_wish_to: str
    so_that: str
    tasks: list[SprintStoryTask]
    criteria: list[SprintStoryCriteria]


@router.get("/sprint-planning-lookups")
def get_sprint_planning_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("select ModuleID, ModuleName from agile_ModuleMaster where ProjectID = ? and Enabled = 1 order by ModuleName", project_id)
        modules = [{"label": r["ModuleName"] or "", "value": r["ModuleID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select ISNULL(SprintCount, 0) from AssignSprint where Projectid = ?", project_id)
        row = cursor.fetchone()
        sprint_count = row[0] if row else 0
        cursor.execute("select distinct SprintNo from Agile_sprintmaster where ProjectID = ?", project_id)
        used = {_sprint_num(r[0]) for r in cursor.fetchall()}
        available = [n for n in range(1, sprint_count + 1) if n not in used]
    return {"modules": modules, "sprints": [{"label": f"Sprint {n}", "value": n} for n in available]}


@router.post("/sprints")
def add_sprint(project_id: int, start_date: str, end_date: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute("select SprintCount from AssignSprint where Projectid = ?", project_id)
        row = cursor.fetchone()
        if row is None:
            cursor.execute("insert into AssignSprint (Projectid, SprintCount) values (?, 1)", project_id)
        else:
            cursor.execute("update AssignSprint set SprintCount = SprintCount + 1 where Projectid = ?", project_id)
    return {"result_code": 100, "result_msg": "Sprint added successfully."}


@router.delete("/sprints/{project_id}/{sprint_id}")
def delete_sprint(project_id: int, sprint_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("update AssignSprint set SprintCount = CASE WHEN SprintCount > 0 THEN SprintCount - 1 ELSE 0 END where Projectid = ?", project_id)
    return {"result_code": 100, "result_msg": "Sprint deleted successfully."}


@router.get("/product-backlog-for-sprint", response_model=list[SprintBacklogRow])
def get_product_backlog_for_sprint(project_id: int, module_id: int = 0) -> list[SprintBacklogRow]:
    where = " where PB.ProjectId = ? and ISNULL(PB.SprintId, 0) = 0"
    params: list = [project_id]
    if module_id:
        where += " and PB.ModuleID = ?"
        params.append(module_id)
    with get_cursor() as cursor:
        cursor.execute(f"{_PB_SELECT}{where} order by PB.ProductBacklogId", *params)
        rows = rows_to_dicts(cursor)
    return [
        SprintBacklogRow(
            project_id=r["ProjectId"], product_backlog_id=r["ProductBacklogId"], module_name=r["ModuleName"] or "",
            description=r["Description"] or "", complexity=r["Complexity"] or "", effort_size=r["EffortSize"] or "",
            man_hours=float(r["ManHours"] or 0), priority=r["Priority"] or "", developer=r["Developer"] or "",
            tester=r["Tester"] or "", status=r["StatusName"] or "", sprint_id=0,
        )
        for r in rows
    ]


@router.post("/sprints/{sprint_id}/backlog/{product_backlog_id}")
def move_backlog_to_sprint(sprint_id: int, product_backlog_id: int, project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("exec InsertSprint ?, ?, ?", project_id, product_backlog_id, sprint_id)
    return {"result_code": 100, "result_msg": "Product backlog moved to sprint successfully."}


@router.get("/sprints", response_model=list[SprintRow])
def get_sprints(project_id: int) -> list[SprintRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select SprintNo, MIN(StartDate) StartDate, MAX(EndDate) EndDate, "
            "AVG(CASE WHEN ManHours > 0 THEN ISNULL(ActualHours,0) * 100.0 / ManHours ELSE 0 END) Pct "
            "from Agile_sprintmaster where ProjectID = ? group by SprintNo order by SprintNo",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        SprintRow(
            sprint_no=_sprint_num(r["SprintNo"]), start_date=r["StartDate"].isoformat() if r["StartDate"] else None,
            end_date=r["EndDate"].isoformat() if r["EndDate"] else None, completed_percent=round(min(100.0, float(r["Pct"] or 0)), 1),
        )
        for r in rows
    ]


@router.get("/sprints/{project_id}/{sprint_no}/stories", response_model=list[SprintStoryRow])
def get_sprint_stories(project_id: int, sprint_no: int) -> list[SprintStoryRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select SM.StoryID, PB.Description, ST.Feature, ST.IwishTodesc, ST.Sothatdesc, ESM.EffortSize, "
            "SM.ManHours, ISNULL(SM.ActualHours,0) ActualHours, STM.StatusName, UD.Name Developer, UT.Name Tester "
            "from Agile_sprintmaster SM "
            "left outer join Agile_productbacklogmaster PB on PB.ProductBacklogId = SM.ProductBacklogId and PB.ProjectId = SM.ProjectID "
            "left outer join StoryMaster ST on ST.StoryID = SM.StoryID and ST.ProjectID = SM.ProjectID "
            "left outer join EffortSizeMaster ESM on ESM.EffortSizeID = SM.EffortSizeId "
            "left outer join StatusMaster STM on STM.StatusId = SM.StatusId "
            "left outer join UserMaster UD on UD.UserID = SM.Owner "
            "left outer join UserMaster UT on UT.UserID = SM.Tester "
            "where SM.ProjectID = ? and SM.SprintNo = ?",
            project_id, _sprint_label(sprint_no),
        )
        stories = rows_to_dicts(cursor)
        result = []
        for s in stories:
            cursor.execute("select Task, StatusName Status, StoryHours ManHours, ActualHours WorkedHours from Tasks t inner join StatusMaster sm on t.StatusID = sm.statusid where t.StoryId = ?", s["StoryID"])
            tasks = rows_to_dicts(cursor)
            cursor.execute("select Criteria, verifyCriteria from AcceptanceCriteria where StoryID = ?", s["StoryID"])
            criteria = rows_to_dicts(cursor)
            result.append(
                SprintStoryRow(
                    story_id=s["StoryID"], description=s["Description"] or "", feature=s["Feature"] or "",
                    effort_size=s["EffortSize"] or "", man_hours=float(s["ManHours"] or 0),
                    worked_hours=float(s["ActualHours"] or 0),
                    work_complete=round(min(100, float(s["ActualHours"] or 0) / float(s["ManHours"] or 1) * 100), 1),
                    developer=s["Developer"] or "", tester=s["Tester"] or "", status=s["StatusName"] or "",
                    as_a="", i_wish_to=s["IwishTodesc"] or "", so_that=s["Sothatdesc"] or "",
                    tasks=[
                        SprintStoryTask(
                            task=t["Task"] or "", status=t["Status"] or "", man_hours=float(t["ManHours"] or 0),
                            worked_hours=float(t["WorkedHours"] or 0),
                            percent_complete=round(min(100, float(t["WorkedHours"] or 0) / float(t["ManHours"] or 1) * 100), 1),
                        )
                        for t in tasks
                    ],
                    criteria=[SprintStoryCriteria(criteria=c["Criteria"] or "", verified=bool(c["verifyCriteria"])) for c in criteria],
                )
            )
    return result
