"""Mirrors Agile_project_Manegment/RSPL_Projects.aspx.vb (project master
CRUD) and Assign Team.aspx.vb (developer/tester team assignment per
project). Both write paths use real, confirmed-existing stored procs.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-projects"])


class LookupOption(BaseModel):
    label: str
    value: int


class AgileProjectRow(BaseModel):
    project_id: int
    project_name: str
    client_name: str
    project_description: str
    remark: str
    project_value: float
    funds_received: float
    percent_received: float
    work_done: float
    leader: str
    enabled: bool


class AgileProjectForm(BaseModel):
    client_id: int
    project_name: str
    description: str
    remark: str
    project_value: float
    funds_received: float
    currency_id: int
    spoc_name: str
    spoc_email: str
    spoc_mobile: str
    work_done: float
    project_lead_id: int


class UpdateProjectProgress(BaseModel):
    project_id: int
    funds_received: float
    work_done: float


class TeamMember(BaseModel):
    user_id: int
    name: str


class AssignUnassignTeamRequest(BaseModel):
    project_id: int
    user_ids: list[int]
    type_id: int
    add: bool


@router.get("/projects", response_model=list[LookupOption])
def get_agile_projects() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("select ProjectID, ProjectName from Agile_ProjectMaster where Enabled=1 order by ProjectName")
        return [LookupOption(label=r["ProjectName"] or "", value=r["ProjectID"]) for r in rows_to_dicts(cursor)]


@router.get("/project-form-lookups")
def get_project_form_lookups() -> dict:
    with get_cursor() as cursor:
        cursor.execute("select Name, CustID from CustomerMaster where Disabled=0 order by Name")
        clients = [{"label": "All", "value": 0}] + [{"label": r["Name"] or "", "value": r["CustID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select Currency, Currencyid from Agile_Currencymaster")
        currencies = [{"label": "All", "value": 0}] + [{"label": r["Currency"] or "", "value": r["Currencyid"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select UserID, Name from userMaster where usertype in (4,1) and Enabled=1 order by Name")
        leads = [{"label": "All", "value": 0}] + [{"label": r["Name"] or "", "value": r["UserID"]} for r in rows_to_dicts(cursor)]
    return {"clients": clients, "currencies": currencies, "leads": leads}


@router.get("/project-list", response_model=list[AgileProjectRow])
def get_agile_project_list() -> list[AgileProjectRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select PM.ProjectID, PM.ProjectName, PM.ClientName, PM.ProjectDescription, PM.Remark, PM.ProjectValue, "
            "PM.FundsRecieved, PM.perRecieved, PM.WorkDone, PM.Enabled, UM.Name Leader "
            "from Agile_ProjectMaster PM left outer join UserMaster UM on UM.UserID = PM.ProjectLead order by PM.ProjectName"
        )
        rows = rows_to_dicts(cursor)
    return [
        AgileProjectRow(
            project_id=r["ProjectID"], project_name=r["ProjectName"] or "", client_name=r["ClientName"] or "",
            project_description=r["ProjectDescription"] or "", remark=r["Remark"] or "",
            project_value=float(r["ProjectValue"] or 0), funds_received=float(r["FundsRecieved"] or 0),
            percent_received=float(r["perRecieved"] or 0), work_done=float(r["WorkDone"] or 0),
            leader=r["Leader"] or "", enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.post("/projects")
def add_agile_project(form: AgileProjectForm) -> dict:
    with get_cursor() as cursor:
        client_name = ""
        if form.client_id:
            cursor.execute("select Name from CustomerMaster where CustID = ?", form.client_id)
            row = cursor.fetchone()
            client_name = row[0] if row else ""
        cursor.execute(
            "exec Proc_AddUpdate_RSPL_Project @ProjectID=0,@ClientName=?,@ProjectName=?,@ProjectDesc=?,@Remark=?,"
            "@ProjectValue=?,@FundsReceived=?,@Currency=?,@Singlepointofcontact=?,@Emailid=?,@Mobileno=?,@WorkDone=?,@ProjectLead=?",
            client_name, form.project_name, form.description, form.remark, form.project_value, form.funds_received,
            form.currency_id, form.spoc_name, form.spoc_email, form.spoc_mobile or "0", form.work_done, form.project_lead_id,
        )
        cursor.execute("select ISNULL(MAX(ProjectID), 0) from Agile_ProjectMaster")
        project_id = cursor.fetchone()[0]
    return {"project_id": project_id, "result_msg": "Project Created Successfully"}


@router.put("/projects/{project_id}")
def update_agile_project_progress(project_id: int, body: UpdateProjectProgress) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Update Agile_ProjectMaster set FundsRecieved = ?, WorkDone = ? where ProjectID = ?",
            body.funds_received, body.work_done, project_id,
        )
    return {"success": True}


@router.get("/team/{project_id}")
def get_team_lists(project_id: int, type_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Select Name, UserID from userMaster Where userid not in "
            "(Select userid From Agile_assignteam where projectid = ? and Typeid = ? and active = 1) and Enabled = 1 order by Name",
            project_id, type_id,
        )
        unassigned = [{"user_id": r["UserID"], "name": r["Name"] or ""} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "Select um.Name, um.UserID from userMaster um inner join Agile_assignteam at on at.userid = um.userid "
            "where at.projectid = ? and at.Typeid = ? and at.active = 1 order by Name",
            project_id, type_id,
        )
        assigned = [{"user_id": r["UserID"], "name": r["Name"] or ""} for r in rows_to_dicts(cursor)]
    return {"unassigned": unassigned, "assigned": assigned}


@router.post("/team/assign")
def assign_unassign_team(body: AssignUnassignTeamRequest) -> dict:
    with get_cursor() as cursor:
        for user_id in body.user_ids:
            cursor.execute("Proc_AssignUnAssignTeam ?, ?, ?, ?, 1", body.add, body.project_id, user_id, body.type_id)
    return {"success": True}
