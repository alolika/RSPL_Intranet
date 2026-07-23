"""Mirrors Section_General/ManageChart.aspx.vb and OragnizationalChart.aspx
(the latter via App_Code/VB/Org_ChartWebservice.vb's GetEmployees, backed by
sp_testRec). The org chart is rebuilt with PrimeNG's native OrganizationChart
component instead of the source's canvas-based orgchart.js — that needs one
flat list of all employees (client builds the tree), not sp_testRec's
paginated get-children-of-these-parent-ids shape, so this queries UserMaster
directly in one shot instead of porting the paging.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(tags=["general-org"])

_IMAGE_DIR = Path(__file__).resolve().parent.parent / "static" / "emp_images"


class LookupOption(BaseModel):
    label: str
    value: int


class DepartmentRow(BaseModel):
    dept_id: int
    name: str
    hod_name: str
    hod_user_id: int
    enabled: bool


class AddDepartmentRequest(BaseModel):
    name: str
    hod_user_id: int
    enabled: bool


class DesignationRow(BaseModel):
    designation_id: int
    desig_name: str
    enabled: bool


class AddDesignationRequest(BaseModel):
    name: str
    enabled: bool


class AssignmentRow(BaseModel):
    user_id: int
    emp_name: str
    desig_name: str
    desig_id: int
    dept_name: str
    dept_id: int
    parent_emp_name: str
    parent_emp_id: int


class OrgEmployee(BaseModel):
    user_id: int
    name: str
    department: str
    designation: str
    parent_emp_id: int
    mobile_no: str
    branch_name: str


# --- Mirrors Section_General/ManageChart.aspx.vb ---


@router.get("/general/manage-chart/employees", response_model=list[LookupOption])
def get_employees() -> list[LookupOption]:
    with get_cursor() as cursor:
        # Also include any currently-assigned department HOD even if since
        # disabled (e.g. resigned) — otherwise the HOD <p-select> on the
        # Department tab has no matching option for that row and renders
        # blank when a row is opened for editing (confirmed live: dept HODs
        # 27/Jayesh Soundattikar and 23/Navnath Shinde are Enabled=0 in
        # UserMaster but still HODUserID on RT_DepartmentMaster rows).
        cursor.execute(
            "SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 "
            "UNION "
            "SELECT u.UserID, u.Name FROM UserMaster u "
            "INNER JOIN RT_DepartmentMaster d ON d.HODUserID = u.UserID "
            "ORDER BY Name"
        )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/general/manage-chart/departments", response_model=list[DepartmentRow])
def get_departments() -> list[DepartmentRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT d.DeptID, d.Name, u.Name AS HODName, d.HODUserID, d.Enabled "
            "FROM RT_DepartmentMaster d INNER JOIN UserMaster u ON d.HODUserID = u.UserID"
        )
        rows = rows_to_dicts(cursor)
    return [
        DepartmentRow(
            dept_id=r["DeptID"], name=r["Name"], hod_name=r["HODName"] or "",
            hod_user_id=r["HODUserID"], enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.post("/general/manage-chart/departments")
def add_department(body: AddDepartmentRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ?", body.hod_user_id)
        hod_row = cursor.fetchone()
        hod_name = hod_row[0] if hod_row else ""
        cursor.execute(
            "EXEC Scheduler_Proc_InsertDemaprtment @Name=?, @HODName=?, @Enabled=?, @UserName=?, @TranType=?, @UpdateKey=?",
            body.name, hod_name, body.enabled, current_user.username, "INSERT", 0,
        )
    return {"success": True}


@router.put("/general/manage-chart/departments/{dept_id}")
def update_department(dept_id: int, body: DepartmentRow, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ?", body.hod_user_id)
        hod_row = cursor.fetchone()
        hod_name = hod_row[0] if hod_row else body.hod_name
        cursor.execute(
            "EXEC Scheduler_Proc_InsertDemaprtment @Name=?, @HODName=?, @Enabled=?, @UserName=?, @TranType=?, @UpdateKey=?",
            body.name, hod_name, body.enabled, current_user.username, "UPDATE", dept_id,
        )
    return {"success": True}


@router.get("/general/manage-chart/designations", response_model=list[DesignationRow])
def get_designations() -> list[DesignationRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT DesignationID, DesigName, Enabled FROM RSPL_Designation")
        rows = rows_to_dicts(cursor)
    return [
        DesignationRow(designation_id=r["DesignationID"], desig_name=r["DesigName"] or "", enabled=bool(r["Enabled"]))
        for r in rows
    ]


@router.post("/general/manage-chart/designations")
def add_designation(body: AddDesignationRequest) -> dict[str, bool]:
    # Bypasses Scheduler_Proc_InsertDesignation (confirmed live: its INSERT
    # branch does `INSERT INTO RSPL_Designation VALUES(@DesignationID,
    # @DesigName,@Enabled)` — only 3 values, but the table now has a 4th
    # column (IsSynctoCloud, added after the proc was written). SQL Server
    # compiles the whole proc body at once, so this column-count mismatch
    # 500s the ENTIRE proc — including the @TranType='UPDATE' branch, whose
    # own UPDATE statement has no such problem. Replicated here with the
    # same ID-generation logic, using an explicit column list so
    # IsSynctoCloud just takes its own default (0) instead of needing a value.
    with get_cursor() as cursor:
        cursor.execute("SELECT ISNULL(MAX(DesignationID), 0) + 1 FROM RSPL_Designation")
        new_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO RSPL_Designation (DesignationID, DesigName, Enabled) VALUES (?, ?, ?)",
            new_id, body.name, body.enabled,
        )
    return {"success": True}


@router.put("/general/manage-chart/designations/{designation_id}")
def update_designation(designation_id: int, body: DesignationRow) -> dict[str, bool]:
    # See add_designation's comment — bypasses the same broken proc.
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_Designation SET DesigName = ?, Enabled = ? WHERE DesignationID = ?",
            body.desig_name, body.enabled, designation_id,
        )
    return {"success": True}


@router.get("/general/manage-chart/assignments", response_model=list[AssignmentRow])
def get_assignments() -> list[AssignmentRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT u.UserID, u.Name AS EmpName, ISNULL(ds.DesigName, '') AS DesigName, ISNULL(u.DesignationID, 0) AS DesigID,
                   ISNULL(dp.Name, '') AS DeptName, ISNULL(u.DepartmentID, 0) AS DeptID,
                   ISNULL(um.Name, '') AS ParentEmpName, ISNULL(u.ParentEmpID, 0) AS ParentEmpID
            FROM UserMaster u
            LEFT OUTER JOIN UserMaster um ON u.ParentEmpID = um.UserID
            LEFT JOIN RT_DepartmentMaster dp ON u.DepartmentID = dp.DeptID
            LEFT JOIN RSPL_Designation ds ON u.DesignationID = ds.DesignationID
            WHERE u.Enabled = 1
            ORDER BY u.Name
            """
        )
        rows = rows_to_dicts(cursor)
    return [
        AssignmentRow(
            user_id=r["UserID"], emp_name=r["EmpName"] or "", desig_name=r["DesigName"],
            desig_id=r["DesigID"], dept_name=r["DeptName"], dept_id=r["DeptID"],
            parent_emp_name=r["ParentEmpName"], parent_emp_id=r["ParentEmpID"],
        )
        for r in rows
    ]


@router.put("/general/manage-chart/assignments/{user_id}")
def update_assignment(user_id: int, body: AssignmentRow) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE UserMaster SET DepartmentID = ?, DesignationID = ?, ParentEmpID = ? WHERE UserID = ?",
            body.dept_id, body.desig_id, body.parent_emp_id, user_id,
        )
    return {"success": True}


@router.post("/general/manage-chart/upload-image")
async def upload_employee_image(employee_id: int, file: UploadFile) -> dict[str, bool]:
    _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _IMAGE_DIR / f"{employee_id}.jpeg"
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE UserMaster SET ImagePath = ? WHERE UserID = ?",
            os.path.join("App_Image", "EMP", f"{employee_id}.jpeg"),
            employee_id,
        )
    return {"success": True}


# --- Mirrors OragnizationalChart.aspx / Org_ChartWebservice.vb's GetEmployees (sp_testRec) ---


@router.get("/general/org-chart/employees", response_model=list[OrgEmployee])
def get_org_employees() -> list[OrgEmployee]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT u.UserID, u.Name, ISNULL(u.ParentEmpID, 0) AS ParentEmpID, ISNULL(u.MobileNo, '') AS MobileNo,
                   ISNULL(d.Name, '') AS Department, ISNULL(dg.DesigName, '') AS Designation,
                   ISNULL(b.BranchName, '') AS BranchName
            FROM UserMaster u
            LEFT JOIN RT_DepartmentMaster d ON u.DepartmentID = d.DeptId
            LEFT JOIN RSPL_Designation dg ON dg.DesignationID = u.DesignationID
            LEFT OUTER JOIN Branch b ON u.BranchID = b.BranchID
            WHERE u.Enabled = 1
            """
        )
        rows = rows_to_dicts(cursor)
    return [
        OrgEmployee(
            user_id=r["UserID"], name=r["Name"] or "", department=r["Department"],
            designation=r["Designation"], parent_emp_id=r["ParentEmpID"],
            mobile_no=r["MobileNo"], branch_name=r["BranchName"],
        )
        for r in rows
    ]
