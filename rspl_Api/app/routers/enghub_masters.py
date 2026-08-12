"""Engineering Hub - master data maintenance (Masters menu).

Nine lookup tables, each an add-form + inline-editable grid, following the
established Agile master-page pattern (see agile_masters.py) but using plain
parameterized INSERT/UPDATE against the new EngHub_* tables directly — this
is a brand-new schema with no legacy stored procs to match, so there's no
reason to introduce a new Proc_/WebProc_ naming inconsistency for it.

Endpoints are one thin GET/POST pair per master rather than a single generic
"pick a table by name" endpoint — the latter would need to accept a table or
column name from the client, which is a SQL-injection surface this app has
otherwise avoided everywhere. The seven structurally-identical simple masters
(Id/Name/Enabled) share one internal helper instead; the actual table/column
names passed to that helper are always hardcoded Python literals in this
file, never client-supplied.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-masters"])


class LookupOption(BaseModel):
    label: str
    value: int


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    # Same UserMaster lookup query duplicated across 8+ existing data
    # services (see the approved design proposal §2/§8) — kept independent
    # rather than importing a Main-portal service, matching how Agile (the
    # module Engineering Hub mirrors) also has its own copy rather than
    # reusing Main's. Centralizing this app-wide is a suggested future
    # improvement, not forced here.
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [LookupOption(label=r["Name"] or "", value=r["UserID"]) for r in rows]


class SimpleMasterRow(BaseModel):
    id: int
    name: str
    enabled: bool


def _get_simple_master(table: str, id_col: str) -> list[SimpleMasterRow]:
    with get_cursor() as cursor:
        cursor.execute(f"SELECT {id_col}, Name, Enabled FROM {table} ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [SimpleMasterRow(id=r[id_col], name=r["Name"] or "", enabled=bool(r["Enabled"])) for r in rows]


def _save_simple_master(table: str, id_col: str, row: SimpleMasterRow, user: CurrentUser) -> dict:
    with get_cursor() as cursor:
        if row.id == 0:
            cursor.execute(
                f"INSERT INTO {table} (Name, Enabled, CreatedByUserId) VALUES (?, ?, ?)",
                row.name, row.enabled, user.user_id,
            )
        else:
            cursor.execute(
                f"UPDATE {table} SET Name = ?, Enabled = ?, LastEditedByUserId = ?, LastEditedAt = SYSUTCDATETIME() "
                f"WHERE {id_col} = ?",
                row.name, row.enabled, user.user_id, row.id,
            )
    return {"success": True}


# ---------------------------------------------------------------------------
# Seven simple Id/Name/Enabled masters
# ---------------------------------------------------------------------------

@router.get("/task-types", response_model=list[SimpleMasterRow])
def get_task_types() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_TaskType", "TaskTypeId")


@router.post("/task-types")
def save_task_type(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_TaskType", "TaskTypeId", row, user)


@router.get("/dev-item-types", response_model=list[SimpleMasterRow])
def get_dev_item_types() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_DevItemType", "DevItemTypeId")


@router.post("/dev-item-types")
def save_dev_item_type(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_DevItemType", "DevItemTypeId", row, user)


@router.get("/activity-types", response_model=list[SimpleMasterRow])
def get_activity_types() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_ActivityType", "ActivityTypeId")


@router.post("/activity-types")
def save_activity_type(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_ActivityType", "ActivityTypeId", row, user)


@router.get("/decision-types", response_model=list[SimpleMasterRow])
def get_decision_types() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_DecisionType", "DecisionTypeId")


@router.post("/decision-types")
def save_decision_type(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_DecisionType", "DecisionTypeId", row, user)


@router.get("/interruption-reasons", response_model=list[SimpleMasterRow])
def get_interruption_reasons() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_InterruptionReason", "InterruptionReasonId")


@router.post("/interruption-reasons")
def save_interruption_reason(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_InterruptionReason", "InterruptionReasonId", row, user)


@router.get("/priorities", response_model=list[SimpleMasterRow])
def get_priorities() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_Priority", "PriorityId")


@router.post("/priorities")
def save_priority(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_Priority", "PriorityId", row, user)


@router.get("/origin-types", response_model=list[SimpleMasterRow])
def get_origin_types() -> list[SimpleMasterRow]:
    return _get_simple_master("EngHub_OriginType", "OriginTypeId")


@router.post("/origin-types")
def save_origin_type(row: SimpleMasterRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    return _save_simple_master("EngHub_OriginType", "OriginTypeId", row, user)


# ---------------------------------------------------------------------------
# Product / Module (own hierarchy — deliberately not Agile_ProjectMaster /
# agile_ModuleMaster, see the approved design proposal)
# ---------------------------------------------------------------------------

class ProductRow(BaseModel):
    product_id: int
    name: str
    description: str
    enabled: bool


@router.get("/products", response_model=list[ProductRow])
def get_products() -> list[ProductRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ProductId, Name, Description, Enabled FROM EngHub_Product ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [
        ProductRow(product_id=r["ProductId"], name=r["Name"] or "", description=r["Description"] or "", enabled=bool(r["Enabled"]))
        for r in rows
    ]


@router.get("/products/lookup", response_model=list[LookupOption])
def get_products_lookup() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ProductId, Name FROM EngHub_Product WHERE Enabled = 1 ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [LookupOption(label=r["Name"] or "", value=r["ProductId"]) for r in rows]


@router.post("/products")
def save_product(row: ProductRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.product_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Product (Name, Description, Enabled, CreatedByUserId) VALUES (?, ?, ?, ?)",
                row.name, row.description, row.enabled, user.user_id,
            )
        else:
            cursor.execute(
                "UPDATE EngHub_Product SET Name=?, Description=?, Enabled=?, LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() "
                "WHERE ProductId=?",
                row.name, row.description, row.enabled, user.user_id, row.product_id,
            )
    return {"success": True}


class ModuleRow(BaseModel):
    module_id: int
    product_id: int
    name: str
    description: str
    enabled: bool


@router.get("/modules", response_model=list[ModuleRow])
def get_modules(product_id: int) -> list[ModuleRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ModuleId, ProductId, Name, Description, Enabled FROM EngHub_Module WHERE ProductId = ? ORDER BY Name",
            product_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ModuleRow(
            module_id=r["ModuleId"], product_id=r["ProductId"], name=r["Name"] or "",
            description=r["Description"] or "", enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.get("/modules/lookup", response_model=list[LookupOption])
def get_modules_lookup(product_id: int | None = None) -> list[LookupOption]:
    # product_id omitted -> every enabled module across all products, used by
    # the Tasks page's unscoped Module filter (not a cascading picker there).
    with get_cursor() as cursor:
        if product_id is not None:
            cursor.execute(
                "SELECT ModuleId, Name FROM EngHub_Module WHERE ProductId = ? AND Enabled = 1 ORDER BY Name", product_id
            )
        else:
            cursor.execute("SELECT ModuleId, Name FROM EngHub_Module WHERE Enabled = 1 ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [LookupOption(label=r["Name"] or "", value=r["ModuleId"]) for r in rows]


@router.post("/modules")
def save_module(row: ModuleRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.module_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Module (ProductId, Name, Description, Enabled, CreatedByUserId) VALUES (?, ?, ?, ?, ?)",
                row.product_id, row.name, row.description, row.enabled, user.user_id,
            )
        else:
            cursor.execute(
                "UPDATE EngHub_Module SET Name=?, Description=?, Enabled=?, LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() "
                "WHERE ModuleId=?",
                row.name, row.description, row.enabled, user.user_id, row.module_id,
            )
    return {"success": True}


# ---------------------------------------------------------------------------
# Status (one physical table, scoped by EntityType, serving Feature /
# DevelopmentItem / Task / Decision / Release status vocabularies)
# ---------------------------------------------------------------------------

class StatusRow(BaseModel):
    status_id: int
    entity_type: str
    name: str
    color: str | None = None
    sort_order: int
    is_terminal: bool
    enabled: bool


@router.get("/statuses", response_model=list[StatusRow])
def get_statuses(entity_type: str) -> list[StatusRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT StatusId, EntityType, Name, Color, SortOrder, IsTerminal, Enabled "
            "FROM EngHub_Status WHERE EntityType = ? ORDER BY SortOrder, Name",
            entity_type,
        )
        rows = rows_to_dicts(cursor)
    return [
        StatusRow(
            status_id=r["StatusId"], entity_type=r["EntityType"], name=r["Name"] or "",
            color=r["Color"], sort_order=r["SortOrder"] or 0, is_terminal=bool(r["IsTerminal"]),
            enabled=bool(r["Enabled"]),
        )
        for r in rows
    ]


@router.post("/statuses")
def save_status(row: StatusRow, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.status_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Status (EntityType, Name, Color, SortOrder, IsTerminal, Enabled, CreatedByUserId) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                row.entity_type, row.name, row.color, row.sort_order, row.is_terminal, row.enabled, user.user_id,
            )
        else:
            cursor.execute(
                "UPDATE EngHub_Status SET Name=?, Color=?, SortOrder=?, IsTerminal=?, Enabled=?, "
                "LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE StatusId=?",
                row.name, row.color, row.sort_order, row.is_terminal, row.enabled, user.user_id, row.status_id,
            )
    return {"success": True}
