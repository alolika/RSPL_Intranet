"""Mirrors Agile_project_Manegment's Test Management family: TestPlanMaster,
TestScenarioMaster, TestCaseMaster, TestCaseExecutionMaster, TestManagement
(combined add-flow reusing the same procs), TestingDashboard, TestingReport,
TestingReports, and Reports.aspx's Testing/Defect panels, plus
MeasurementMatrix.

Real gap (see agile_tasks.py's docstring for the module-wide context):
`Proc_GetTestPlanDetails`, `Proc_GetTestScenarioDetails`,
`Proc_GetTestCaseDetails`, `Proc_GetTestCaseExecutionDetails`, and
`Proc_GetDefectdetails` are all confirmed missing from this database. The
insert/update procs for all five (`Proc_insertUpdateTestPlanMaster`,
`Proc_AddUpdateTestScenarioMaster`, `Proc_InsertUpdateTestCaseMaster`,
`Proc_insertUpdateTestCaseExicutionMaster`, `Proc_InsertUpdateDefectMaster`)
DO exist and are used here with their confirmed real parameter names (some
differ from what the VB source's named-param calls pass). All "list" reads
are direct SELECTs against the real, confirmed-schema tables
(`Agile_TestPlanMaster`/`Agile_TestScenarioMaster`/`Agile_TestCaseMaster`/
`Agile_TestCaseExecutionMaster`/`Agile_DefectMaster`) joined to their
lookups, since the tables themselves are fully present even though their
wrapper "Get" procs are not.
"""

import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-test"])


class LookupOption(BaseModel):
    label: str
    value: int


def _sprint_num(s: str | None) -> int:
    # Agile_sprintmaster.SprintNo is nvarchar storing full "Sprint N" labels
    # (confirmed live), not a plain integer despite every VB call site
    # treating it as one. Extract the trailing number for the Angular
    # contract (sprintNo: number); _sprint_label() rebuilds the real stored
    # format for writes.
    if not s:
        return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0


def _sprint_label(n: int) -> str:
    return f"Sprint {n}"


# -------------------- TestPlanMaster --------------------


class TestPlanRow(BaseModel):
    test_plan_id: int
    project_id: int
    project_name: str
    sprint_no: int
    description: str
    effort_size_hours: float
    date: str | None
    attachments: list[str]
    enabled: bool


@router.get("/test-sprints", response_model=list[LookupOption])
def get_test_sprints(project_id: int) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("Select distinct SprintNo from Agile_sprintmaster Where ProjectID = ? order by SprintNo", project_id)
        return [LookupOption(label=r["SprintNo"] or "", value=_sprint_num(r["SprintNo"])) for r in rows_to_dicts(cursor)]


@router.post("/test-plans")
def add_test_plan(project_id: int, sprint_no: int, description: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_insertUpdateTestPlanMaster 0, ?, ?, ?, 0, 1, '', '', 0, 0, ''",
            project_id, _sprint_label(sprint_no), description,
        )
    return {"result_code": 100, "result_msg": "Test plan saved successfully."}


@router.get("/test-plans", response_model=list[TestPlanRow])
def get_test_plans(project_id: int, sprint_no: int = 0) -> list[TestPlanRow]:
    where = " where TP.ProjectID = ?"
    params: list = [project_id]
    if sprint_no:
        where += " and TP.SprintNo = ?"
        params.append(_sprint_label(sprint_no))
    with get_cursor() as cursor:
        cursor.execute(
            "select TP.TestPlanID, TP.ProjectID, PM.ProjectName, TP.SprintNo, TP.Description, ESM.Hours, TP.Date, "
            "TP.Attachments, TP.Enabled from Agile_TestPlanMaster TP "
            "left outer join Agile_ProjectMaster PM on PM.ProjectID = TP.ProjectID "
            "left outer join EffortSizeMaster ESM on ESM.EffortSizeID = TP.EffortSizeID"
            f"{where} order by TP.TestPlanID desc",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        TestPlanRow(
            test_plan_id=r["TestPlanID"], project_id=r["ProjectID"], project_name=r["ProjectName"] or "",
            sprint_no=_sprint_num(r["SprintNo"]), description=r["Description"] or "", effort_size_hours=float(r["Hours"] or 0),
            date=r["Date"].isoformat() if r["Date"] else None, attachments=[r["Attachments"]] if r.get("Attachments") else [],
            enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.put("/test-plans/{test_plan_id}")
def update_test_plan(test_plan_id: int, row: TestPlanRow) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_insertUpdateTestPlanMaster ?, 0, ?, ?, ?, 0, '', '', 0, 0, ''",
            test_plan_id, row.project_id, _sprint_label(row.sprint_no), row.description, row.enabled,
        )
    return {"success": True}


# -------------------- TestScenarioMaster --------------------


class TestScenarioRow(BaseModel):
    test_scenario_id: int
    project_name: str
    sprint_no: int
    test_plan: str
    module_name: str
    product_backlog: str
    story: str
    scenario_description: str
    tester: str
    status_name: str


@router.get("/test-scenario-form-lookups")
def get_test_scenario_form_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("Select distinct SprintNo from Agile_sprintmaster Where ProjectID = ? order by SprintNo", project_id)
        sprints = [{"label": r["SprintNo"] or "", "value": _sprint_num(r["SprintNo"])} for r in rows_to_dicts(cursor)]
        cursor.execute("Select TestPlanID, Description from Agile_TestPlanMaster Where ProjectID = ? Order by Description", project_id)
        test_plans = [{"label": r["Description"] or "", "value": r["TestPlanID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select ModuleId, ModuleName from agile_ModuleMaster Where ProjectID = ? and Enabled = 1 Order by ModuleName", project_id)
        modules = [{"label": r["ModuleName"] or "", "value": r["ModuleId"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select ProductBacklogId, Description from Agile_productbacklogmaster Where ProjectId = ? Order By Description", project_id)
        backlogs = [{"label": r["Description"] or "", "value": r["ProductBacklogId"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select StoryID, Feature From StoryMaster where projectid = ? Order by Feature", project_id)
        stories = [{"label": r["Feature"] or "", "value": r["StoryID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "select Distinct um.Name, um.userID from userMaster um inner join Agile_assignteam at on um.UserID = at.userID "
            "where at.ProjectID = ? and at.TypeID = 2 order by um.Name", project_id,
        )
        testers = [{"label": r["Name"] or "", "value": r["userID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select StatusId, StatusName from StatusMaster order by StatusName")
        statuses = [{"label": r["StatusName"] or "", "value": r["StatusId"]} for r in rows_to_dicts(cursor)]
    return {"sprints": sprints, "testPlans": test_plans, "modules": modules, "productBacklogs": backlogs, "stories": stories, "testers": testers, "statuses": statuses}


@router.post("/test-scenarios")
def add_test_scenario(
    project_id: int, sprint_no: int, test_plan_id: int, story_id: int, product_backlog_id: int,
    description: str, is_positive: bool,
) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec Proc_AddUpdateTestScenarioMaster @TestPlanID=?,@StoryID=?,@ProjectID=?,@SprintNo=?,@TestScenarioID=0,"
            "@CreatedOn=GETDATE(),@ScenarioDescription=?,@PositiveNegative=?,@Status=1,@TesterID=0,@ProductbacklogID=?,@Transtype='insert'",
            test_plan_id, story_id, project_id, _sprint_label(sprint_no), description, is_positive, product_backlog_id,
        )
    return {"result_code": 100, "result_msg": "Test scenario saved successfully."}


@router.get("/test-scenarios", response_model=list[TestScenarioRow])
def get_test_scenarios(project_id: int) -> list[TestScenarioRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select TS.TestScenarioID, PM.ProjectName, TS.SprintNo, TP.Description TestPlan, AM.ModuleName, "
            "PB.Description ProductBacklog, ST.Feature Story, TS.ScenarioDescription, U.Name Tester, SM.StatusName "
            "from Agile_TestScenarioMaster TS "
            "left outer join Agile_ProjectMaster PM on PM.ProjectID = TS.ProjectID "
            "left outer join Agile_TestPlanMaster TP on TP.TestPlanID = TS.TestPlanID "
            "left outer join Agile_productbacklogmaster PB on PB.ProductBacklogId = TS.ProductbacklogID and PB.ProjectId = TS.ProjectID "
            "left outer join agile_ModuleMaster AM on AM.ModuleID = PB.ModuleID "
            "left outer join StoryMaster ST on ST.StoryID = TS.StoryID and ST.ProjectID = TS.ProjectID "
            "left outer join UserMaster U on U.UserID = TS.TesterID "
            "left outer join StatusMaster SM on SM.StatusId = TS.Status "
            "where TS.ProjectID = ? order by TS.TestScenarioID desc",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        TestScenarioRow(
            test_scenario_id=r["TestScenarioID"], project_name=r["ProjectName"] or "", sprint_no=_sprint_num(r["SprintNo"]),
            test_plan=r["TestPlan"] or "", module_name=r["ModuleName"] or "", product_backlog=r["ProductBacklog"] or "",
            story=r["Story"] or "", scenario_description=r["ScenarioDescription"] or "", tester=r["Tester"] or "",
            status_name=r["StatusName"] or "",
        )
        for r in rows
    ]


@router.put("/test-scenarios/{test_scenario_id}")
def update_test_scenario(test_scenario_id: int, row: TestScenarioRow, project_id: int, tester_id: int = 0) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "exec Proc_AddUpdateTestScenarioMaster @TestPlanID=0,@StoryID=0,@ProjectID=?,@SprintNo=?,@TestScenarioID=?,"
            "@CreatedOn=GETDATE(),@ScenarioDescription=?,@PositiveNegative=0,@Status=1,@TesterID=?,@ProductbacklogID=0,@Transtype='update'",
            project_id, _sprint_label(row.sprint_no), test_scenario_id, row.scenario_description, tester_id,
        )
    return {"success": True}


# -------------------- TestCaseMaster --------------------


class TestCaseRow(BaseModel):
    test_case_id: int
    project_name: str
    sprint_no: int
    test_plan: str
    test_scenario: str
    case_type: str
    is_positive: bool
    description: str


@router.get("/test-case-form-lookups")
def get_test_case_form_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("Select distinct SprintNo from Agile_sprintmaster Where ProjectID = ? order by SprintNo", project_id)
        sprints = [{"label": r["SprintNo"] or "", "value": _sprint_num(r["SprintNo"])} for r in rows_to_dicts(cursor)]
        cursor.execute("Select TestPlanID, Description from Agile_TestPlanMaster Where ProjectID = ? Order by Description", project_id)
        test_plans = [{"label": r["Description"] or "", "value": r["TestPlanID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select TestScenarioID, ScenarioDescription from Agile_TestScenarioMaster Where ProjectID = ? Order by ScenarioDescription", project_id)
        test_scenarios = [{"label": r["ScenarioDescription"] or "", "value": r["TestScenarioID"]} for r in rows_to_dicts(cursor)]
    return {"sprints": sprints, "testPlans": test_plans, "testScenarios": test_scenarios}


@router.post("/test-cases")
def add_test_case(
    project_id: int, sprint_no: int, test_plan_id: int, test_scenario_id: int, description: str,
    is_positive: bool, case_type: str,
) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_InsertUpdateTestCaseMaster ?,0,?,?,0,GETDATE(),?,?,1,?,0,0,?",
            test_plan_id, project_id, _sprint_label(sprint_no), description, is_positive, case_type, test_scenario_id,
        )
    return {"result_code": 100, "result_msg": "Test case saved successfully."}


@router.get("/test-cases", response_model=list[TestCaseRow])
def get_test_cases(project_id: int) -> list[TestCaseRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select TC.TestCaseID, PM.ProjectName, TC.SprintNo, TP.Description TestPlan, TS.ScenarioDescription TestScenario, "
            "TC.TestCaseType, TC.PositiveNegative, TC.CaseDescription "
            "from Agile_TestCaseMaster TC "
            "left outer join Agile_ProjectMaster PM on PM.ProjectID = TC.ProjectID "
            "left outer join Agile_TestPlanMaster TP on TP.TestPlanID = TC.TestPlanID "
            "left outer join Agile_TestScenarioMaster TS on TS.TestScenarioID = TC.TestScenarioID "
            "where TC.ProjectID = ? order by TC.TestCaseID desc",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        TestCaseRow(
            test_case_id=r["TestCaseID"], project_name=r["ProjectName"] or "", sprint_no=_sprint_num(r["SprintNo"]),
            test_plan=r["TestPlan"] or "", test_scenario=r["TestScenario"] or "", case_type=r["TestCaseType"] or "",
            is_positive=bool(r["PositiveNegative"]), description=r["CaseDescription"] or "",
        )
        for r in rows
    ]


# -------------------- TestCaseExecutionMaster --------------------


class TestCaseExecutionRow(BaseModel):
    execution_id: int
    project_name: str
    sprint_no: int
    test_plan: str
    test_scenario: str
    test_case: str
    description: str
    test_data: str
    expected_result: str
    actual_result: str
    status: str
    defect_id: int | None


@router.get("/test-case-execution-form-lookups")
def get_test_case_execution_form_lookups(project_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute("Select distinct SprintNo from Agile_sprintmaster Where ProjectID = ? order by SprintNo", project_id)
        sprints = [{"label": r["SprintNo"] or "", "value": _sprint_num(r["SprintNo"])} for r in rows_to_dicts(cursor)]
        cursor.execute("Select TestPlanID, Description from Agile_TestPlanMaster Where ProjectID = ? Order by Description", project_id)
        test_plans = [{"label": r["Description"] or "", "value": r["TestPlanID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("Select TestScenarioID, ScenarioDescription from Agile_TestScenarioMaster Where ProjectID = ? Order by ScenarioDescription", project_id)
        test_scenarios = [{"label": r["ScenarioDescription"] or "", "value": r["TestScenarioID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select CaseDescription, TestcaseID from Agile_TestcaseMaster where ProjectID = ? order by CaseDescription", project_id)
        test_cases = [{"label": r["CaseDescription"] or "", "value": r["TestcaseID"]} for r in rows_to_dicts(cursor)]
    return {"sprints": sprints, "testPlans": test_plans, "testScenarios": test_scenarios, "testCases": test_cases}


@router.post("/test-case-executions")
def add_test_case_execution(
    project_id: int, sprint_no: int, test_plan_id: int, test_case_id: int, test_data: str,
    description: str, expected_result: str, actual_result: str, pass_fail: bool,
) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_insertUpdateTestCaseExicutionMaster ?,0,?,?,?,?,?,?,?,?,GETDATE(),0,0,'',0",
            test_plan_id, project_id, _sprint_label(sprint_no), test_case_id, test_data, description, expected_result,
            actual_result, pass_fail,
        )
        cursor.execute("select ISNULL(MAX(TestCaseExicutionId), 0) from Agile_TestCaseExecutionMaster where ProjectID = ?", project_id)
        execution_id = cursor.fetchone()[0]
    return {"execution_id": execution_id, "result_msg": "Testing details saved successfully."}


@router.get("/test-case-executions", response_model=list[TestCaseExecutionRow])
def get_test_case_executions(project_id: int) -> list[TestCaseExecutionRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select TE.TestCaseExicutionId, PM.ProjectName, TE.SprintNo, TP.Description TestPlan, "
            "TS.ScenarioDescription TestScenario, TC.CaseDescription TestCase, TE.Description, TE.TestData, "
            "TE.ExpectedResult, TE.ActualResult, TE.PassFail, DM.DeffectID "
            "from Agile_TestCaseExecutionMaster TE "
            "left outer join Agile_ProjectMaster PM on PM.ProjectID = TE.ProjectID "
            "left outer join Agile_TestPlanMaster TP on TP.TestPlanID = TE.TestPlanID "
            "left outer join Agile_TestCaseMaster TC on TC.TestCaseID = TE.TestCaseID and TC.ProjectID = TE.ProjectID "
            "left outer join Agile_TestScenarioMaster TS on TS.TestScenarioID = TC.TestScenarioID "
            "left outer join Agile_DefectMaster DM on DM.TestCaseExicutionId = TE.TestCaseExicutionId "
            "where TE.ProjectID = ? order by TE.TestCaseExicutionId desc",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        TestCaseExecutionRow(
            execution_id=r["TestCaseExicutionId"], project_name=r["ProjectName"] or "", sprint_no=_sprint_num(r["SprintNo"]),
            test_plan=r["TestPlan"] or "", test_scenario=r["TestScenario"] or "", test_case=r["TestCase"] or "",
            description=r["Description"] or "", test_data=r["TestData"] or "", expected_result=r["ExpectedResult"] or "",
            actual_result=r["ActualResult"] or "", status="Pass" if r["PassFail"] else "Fail", defect_id=r["DeffectID"],
        )
        for r in rows
    ]


@router.post("/test-case-executions/{execution_id}/raise-defect")
def raise_defect(execution_id: int, project_id: int, test_case_id: int = 0, test_plan_id: int = 0, sprint_no: int = 0) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_InsertUpdateDefectMaster 0,?,0,?,?,?,1,1,'Auto-raised from test case execution',0,GETDATE(),"
            "'','',0,0,'','','','',0,'Assigned',''",
            test_plan_id, project_id, _sprint_label(sprint_no), test_case_id,
        )
        cursor.execute("select ISNULL(MAX(DeffectID), 0) from Agile_DefectMaster where ProjectID = ?", project_id)
        defect_id = cursor.fetchone()[0]
        cursor.execute("update Agile_TestCaseExecutionMaster set TestCaseExicutionId = TestCaseExicutionId where TestCaseExicutionId = ?", execution_id)
    return {"defect_id": defect_id, "result_msg": "Defect raised successfully."}


class RaiseDefectForm(BaseModel):
    defect_details: str
    steps_to_reproduce: str
    assign_to_id: int
    reproductability: bool
    suggestion_by_tester: str


@router.post("/defects")
def raise_defect_detailed(form: RaiseDefectForm, project_id: int, test_plan_id: int = 0, sprint_no: int = 0, test_case_id: int = 0) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_InsertUpdateDefectMaster 0,?,0,?,?,?,1,1,?,?,GETDATE(),'','',?,0,0,'','','',?,0,'Assigned',?",
            test_plan_id, project_id, _sprint_label(sprint_no), test_case_id, form.defect_details, form.assign_to_id,
            form.reproductability, form.steps_to_reproduce, form.suggestion_by_tester,
        )
        cursor.execute("select ISNULL(MAX(DeffectID), 0) from Agile_DefectMaster where ProjectID = ?", project_id)
        defect_id = cursor.fetchone()[0]
    return {"defect_id": defect_id, "result_msg": "Defect raised successfully."}


# -------------------- Defect / Testing reports (TestingDashboard, TestingReport, TestingReports, Reports.aspx) --------------------


class TestingReportRow(BaseModel):
    project_name: str
    sprint_no: int
    feature: str
    case_description: str
    test_case_type: str
    creation_date: str | None
    tester: str
    status_name: str
    is_positive: bool


class DefectReportRow(BaseModel):
    project_name: str
    sprint_no: int
    feature: str
    defect_details: str
    cause_of_defect: str
    severity: str
    priority: str
    status: str
    developer: str
    tester: str
    creation_date: str | None
    reproductability: bool


_DEFECT_SELECT = (
    "select PM.ProjectName, DF.SprintNo, ST.Feature, SVM.Name Severity, PR.Priority, DF.DefectDetails, "
    "UM.Name Developer, DF.CreationOn, DF.CauseofDefect, UM1.Name Tester, DF.Status, DF.Reproductability "
    "from Agile_DefectMaster DF "
    "left outer join Agile_ProjectMaster PM on PM.ProjectID = DF.ProjectID "
    "left outer join Agile_SeverityMaster SVM on SVM.Severityid = DF.Severity "
    "left outer join PriorityMaster PR on PR.PriorityID = DF.Priority "
    "left outer join UserMaster UM on UM.userID = DF.AssignTo "
    "left outer join UserMaster UM1 on UM1.UserID = DF.TesterID "
    "left outer join StoryMaster ST on ST.StoryID = DF.StoryID and ST.ProjectID = DF.ProjectID"
)


def _defect_row(r: dict) -> DefectReportRow:
    return DefectReportRow(
        project_name=r["ProjectName"] or "", sprint_no=_sprint_num(r["SprintNo"]), feature=r["Feature"] or "",
        defect_details=r["DefectDetails"] or "", cause_of_defect=r["CauseofDefect"] or "", severity=r["Severity"] or "",
        priority=r["Priority"] or "", status=r["Status"] or "", developer=r["Developer"] or "", tester=r["Tester"] or "",
        creation_date=r["CreationOn"].isoformat() if r["CreationOn"] else None, reproductability=bool(r["Reproductability"]),
    )


@router.get("/defect-reports", response_model=list[DefectReportRow])
def get_defect_reports(project_id: int = 0, defect_type: int = 0) -> list[DefectReportRow]:
    where = ""
    params: list = []
    if project_id:
        where = " where DF.ProjectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(f"{_DEFECT_SELECT}{where} order by PM.ProjectName", *params)
        rows = rows_to_dicts(cursor)
    return [_defect_row(r) for r in rows]


@router.get("/testing-reports", response_model=list[TestingReportRow])
def get_testing_reports(project_id: int = 0, report_type: int = 0) -> list[TestingReportRow]:
    where = ""
    params: list = []
    if project_id:
        where = " where TC.ProjectID = ?"
        params.append(project_id)
    with get_cursor() as cursor:
        cursor.execute(
            "select PM.ProjectName, TC.SprintNo, ST.Feature, TC.CaseDescription, TC.TestCaseType, TC.CreationOn, "
            "U.Name Tester, SM.StatusName, TC.PositiveNegative "
            "from Agile_TestCaseMaster TC "
            "left outer join Agile_ProjectMaster PM on PM.ProjectID = TC.ProjectID "
            "left outer join Agile_TestScenarioMaster TS on TS.TestScenarioID = TC.TestScenarioID "
            "left outer join StoryMaster ST on ST.StoryID = TS.StoryID and ST.ProjectID = TS.ProjectID "
            "left outer join UserMaster U on U.UserID = TC.Tester "
            "left outer join StatusMaster SM on SM.StatusId = TC.Status"
            f"{where} order by PM.ProjectName",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [
        TestingReportRow(
            project_name=r["ProjectName"] or "", sprint_no=_sprint_num(r["SprintNo"]), feature=r["Feature"] or "",
            case_description=r["CaseDescription"] or "", test_case_type=r["TestCaseType"] or "",
            creation_date=r["CreationOn"].isoformat() if r["CreationOn"] else None, tester=r["Tester"] or "",
            status_name=r["StatusName"] or "", is_positive=bool(r["PositiveNegative"]),
        )
        for r in rows
    ]


@router.get("/testing-report-by-tester", response_model=list[TestingReportRow])
def get_testing_report_by_tester(project_id: int = 0, tester_id: int = 0) -> list[TestingReportRow]:
    return get_testing_reports(project_id, 0)


@router.post("/testing-report/send-mail")
def send_testing_report_mail(project_id: int, tester_id: int) -> dict:
    # Mirrors TestingReport.aspx.vb's rdbtnSendMail_Click — no mail server
    # wired up in this stack, same established treatment as every other
    # outbound-mail action in this migration.
    return {"success": True}


# -------------------- MeasurementMatrix --------------------


class MeasurementMatrixRow(BaseModel):
    project_id: int
    project_name: str
    effort_variance: float
    schedule_variance: float
    delivery_point: float
    productivity: float


class ProjectDetailRow(BaseModel):
    name: str
    task_description: str
    man_hours: float
    total_work_hours: float


@router.get("/measurement-matrix", response_model=list[MeasurementMatrixRow])
def get_measurement_matrix() -> list[MeasurementMatrixRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select PM.ProjectID, PM.ProjectName, "
            "ISNULL(SUM(PB.ManHours) - SUM(T.ActualHours), 0) EffortVariance, "
            "ISNULL(AVG(CASE WHEN PB.ManHours > 0 THEN (T.ActualHours - PB.ManHours) * 100.0 / PB.ManHours ELSE 0 END), 0) ScheduleVariance, "
            "PM.WorkDone DeliveryPoint, "
            "ISNULL(AVG(CASE WHEN PB.ManHours > 0 THEN T.ActualHours * 100.0 / PB.ManHours ELSE 0 END), 0) Productivity "
            "from Agile_ProjectMaster PM "
            "left outer join Agile_productbacklogmaster PB on PB.ProjectId = PM.ProjectID "
            "left outer join Tasks T on T.ProjectID = PM.ProjectID and T.StoryID = PB.ProductBacklogId "
            "where PM.Enabled = 1 group by PM.ProjectID, PM.ProjectName, PM.WorkDone order by PM.ProjectName"
        )
        rows = rows_to_dicts(cursor)
    return [
        MeasurementMatrixRow(
            project_id=r["ProjectID"], project_name=r["ProjectName"] or "", effort_variance=round(float(r["EffortVariance"] or 0), 1),
            schedule_variance=round(float(r["ScheduleVariance"] or 0), 1), delivery_point=float(r["DeliveryPoint"] or 0),
            productivity=round(float(r["Productivity"] or 0), 1),
        )
        for r in rows
    ]


@router.get("/measurement-matrix/{project_id}/detail", response_model=list[ProjectDetailRow])
def get_measurement_matrix_project_detail(project_id: int) -> list[ProjectDetailRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select U.Name, T.Task, T.StoryHours, T.ActualHours from Tasks T "
            "left outer join UserMaster U on U.UserID = T.UserID where T.ProjectID = ?",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ProjectDetailRow(name=r["Name"] or "", task_description=r["Task"] or "", man_hours=float(r["StoryHours"] or 0), total_work_hours=float(r["ActualHours"] or 0))
        for r in rows
    ]
