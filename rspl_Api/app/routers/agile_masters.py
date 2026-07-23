"""Mirrors Agile_project_Manegment's simple master pages: PriorityMaster,
ComplexityMaster, StatusMaster, EffortSizeMasteer, ModuleMaster. Each is an
add-form + inline-editable grid over its own lookup table.

Real finding: `PriorityMaster.aspx.vb` calls a proc literally named
`proc_AddUpdateComplexityMaster` to save PRIORITY rows — a confusing legacy
name (its actual parameters are `@PriorityID`/`@Priority`, so it genuinely
does operate on PriorityMaster; not a functional bug, just a misleading name
carried forward faithfully here rather than "fixed."

Real gap: `ModuleMaster.aspx.vb`'s `Proc_GetModuleDetails` and
`Proc_AddModifyModule` do not exist on this database instance (confirmed via
OBJECT_ID) — part of a broader pattern of missing procs across this module
(see agile_tasks.py/agile_test.py docstrings). Substituted with direct
SELECT/INSERT/UPDATE against the real `agile_ModuleMaster` table, whose
schema is fully confirmed live.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-masters"])


class LookupOption(BaseModel):
    label: str
    value: int


class PriorityRow(BaseModel):
    priority_id: int
    priority: str
    enabled: bool


class ComplexityRow(BaseModel):
    complexity_id: int
    complexity: str
    enabled: bool


class StatusRow(BaseModel):
    status_id: int
    status_name: str
    color: str
    enabled: bool


class EffortSizeRow(BaseModel):
    effort_size_id: int
    effort_size: str
    hours: int
    description: str
    enabled: bool


class ModuleRow(BaseModel):
    module_id: int
    project_id: int
    module_name: str
    description: str
    enabled: bool


@router.get("/priorities", response_model=list[PriorityRow])
def get_priorities() -> list[PriorityRow]:
    with get_cursor() as cursor:
        cursor.execute("select PriorityID, Priority, Enabled from PriorityMaster order by Priority")
        rows = rows_to_dicts(cursor)
    return [PriorityRow(priority_id=r["PriorityID"], priority=r["Priority"] or "", enabled=bool(r["Enabled"])) for r in rows]


@router.post("/priorities")
def save_priority(row: PriorityRow) -> dict:
    trans_type = "Insert" if row.priority_id == 0 else "Update"
    with get_cursor() as cursor:
        cursor.execute(
            "exec proc_AddUpdateComplexityMaster @PriorityID=?,@Priority=?,@Enabled=?,@Transtype=?",
            row.priority_id, row.priority, row.enabled, trans_type,
        )
    return {"success": True}


@router.get("/complexities", response_model=list[ComplexityRow])
def get_complexities() -> list[ComplexityRow]:
    with get_cursor() as cursor:
        cursor.execute("select ComplexityId, Complexity, Enabled from ComplexityMaster order by Complexity")
        rows = rows_to_dicts(cursor)
    return [ComplexityRow(complexity_id=r["ComplexityId"], complexity=r["Complexity"] or "", enabled=bool(r["Enabled"])) for r in rows]


@router.post("/complexities")
def save_complexity(row: ComplexityRow) -> dict:
    trans_type = "INSERT" if row.complexity_id == 0 else "UPDATE"
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Agile_insertUpdateComplexity ?, ?, ?, ?",
            row.complexity, row.enabled, row.complexity_id, trans_type,
        )
    return {"success": True}


@router.get("/statuses", response_model=list[StatusRow])
def get_statuses() -> list[StatusRow]:
    with get_cursor() as cursor:
        cursor.execute("select StatusId, StatusName, Disabled, Colore from StatusMaster order by StatusName")
        rows = rows_to_dicts(cursor)
    return [
        StatusRow(status_id=r["StatusId"], status_name=r["StatusName"] or "", color=r["Colore"] or "", enabled=not bool(r["Disabled"]))
        for r in rows
    ]


@router.post("/statuses")
def save_status(row: StatusRow) -> dict:
    trans_type = "INSERT" if row.status_id == 0 else "UPDATE"
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Agile_insertupdateStatus ?, ?, ?, ?, ?",
            row.status_name, not row.enabled, row.color, row.status_id, trans_type,
        )
    return {"success": True}


@router.get("/effort-sizes", response_model=list[EffortSizeRow])
def get_effort_sizes() -> list[EffortSizeRow]:
    with get_cursor() as cursor:
        cursor.execute("select EffortSizeID, EffortSize, Hours, Description, Enabled from EffortSizeMaster order by EffortSize")
        rows = rows_to_dicts(cursor)
    return [
        EffortSizeRow(
            effort_size_id=r["EffortSizeID"], effort_size=r["EffortSize"] or "", hours=r["Hours"] or 0,
            description=r["Description"] or "", enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.post("/effort-sizes")
def save_effort_size(row: EffortSizeRow) -> dict:
    trans_type = "INSERT" if row.effort_size_id == 0 else "UPDATE"
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Agile_InsertUudateEffortsize ?, ?, ?, ?, ?, ?",
            row.effort_size, row.hours, row.description, row.enabled, row.effort_size_id, trans_type,
        )
    return {"success": True}


@router.get("/modules", response_model=list[ModuleRow])
def get_modules(project_id: int) -> list[ModuleRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "select ModuleID, ProjectID, ModuleName, Description, Enabled from agile_ModuleMaster where ProjectID = ? order by ModuleName",
            project_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ModuleRow(module_id=r["ModuleID"], project_id=r["ProjectID"], module_name=r["ModuleName"] or "", description=r["Description"] or "", enabled=bool(r["Enabled"]))
        for r in rows
    ]


@router.post("/modules")
def save_module(row: ModuleRow) -> dict:
    with get_cursor() as cursor:
        if row.module_id == 0:
            cursor.execute(
                "insert into agile_ModuleMaster (ModuleName, Description, Enabled, ProjectID) values (?, ?, ?, ?)",
                row.module_name, row.description, row.enabled, row.project_id,
            )
        else:
            cursor.execute(
                "update agile_ModuleMaster set ModuleName = ?, Description = ?, Enabled = ? where ModuleID = ?",
                row.module_name, row.description, row.enabled, row.module_id,
            )
    return {"result_msg": "Module saved successfully.", "result_code": 100}
