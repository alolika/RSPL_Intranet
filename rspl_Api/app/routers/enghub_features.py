"""Engineering Hub - Features (Product > Module > Feature).

Feature is the "permanent business capability" level of the hierarchy —
Enabled=0 retires a Feature rather than deleting it, matching every other
soft-delete table in this schema. Feature Owner / Technical Owner are
tracked two ways: a convenience "current holder" column on EngHub_Feature
itself (fast to read, always reflects the latest assignment) and the full
append-only EngHub_AssignmentHistory trail (never overwritten, see
enghub_common.py) — assigning a new owner updates both in one call.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.enghub_common import AssignmentHistoryRow, AssignRequest, assign_role, get_assignment_history

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-features"])


class LookupOption(BaseModel):
    label: str
    value: int


class FeatureRow(BaseModel):
    feature_id: int
    module_id: int
    module_name: str
    product_id: int
    product_name: str
    name: str
    description: str
    feature_owner_user_id: int | None = None
    feature_owner_name: str | None = None
    technical_owner_user_id: int | None = None
    technical_owner_name: str | None = None
    enabled: bool
    actual_minutes: int = 0


# Time Spent rolls up EVERY duration-carrying Activity anywhere under this
# Feature — logged directly against the Feature, against one of its
# Development Items, or against one of THOSE Dev Items' Tasks (the My Tasks
# grid's Log Work column) — same three-level scope as get_activities()'s
# feature_id rollup in enghub_activities.py, and the same "any activity with
# a duration counts as effort" convention as Tasks'/Dev Items' actual hours/
# minutes. The three sources are UNION ALL'd before summing rather than
# joined separately, which would double-count via join fan-out.
_FEATURE_SELECT = """
    SELECT f.FeatureId, f.ModuleId, m.Name AS ModuleName, m.ProductId, p.Name AS ProductName,
           f.Name, f.Description, f.FeatureOwnerUserId, fo.Name AS FeatureOwnerName,
           f.TechnicalOwnerUserId, tow.Name AS TechnicalOwnerName, f.Enabled,
           act.TotalMinutes AS ActualMinutes
    FROM EngHub_Feature f
    JOIN EngHub_Module m ON m.ModuleId = f.ModuleId
    JOIN EngHub_Product p ON p.ProductId = m.ProductId
    LEFT JOIN UserMaster fo ON fo.UserID = f.FeatureOwnerUserId
    LEFT JOIN UserMaster tow ON tow.UserID = f.TechnicalOwnerUserId
    LEFT JOIN (
        SELECT FeatureId, SUM(DurationMinutes) AS TotalMinutes
        FROM (
            SELECT a.FeatureId, a.DurationMinutes
            FROM EngHub_Activity a
            WHERE a.FeatureId IS NOT NULL AND a.DurationMinutes IS NOT NULL
            UNION ALL
            SELECT d.FeatureId, a.DurationMinutes
            FROM EngHub_Activity a
            JOIN EngHub_DevelopmentItem d ON d.DevItemId = a.DevItemId
            WHERE a.DurationMinutes IS NOT NULL
            UNION ALL
            SELECT d.FeatureId, a.DurationMinutes
            FROM EngHub_Activity a
            JOIN EngHub_Task t ON t.TaskId = a.TaskId
            JOIN EngHub_DevelopmentItem d ON d.DevItemId = t.DevItemId
            WHERE a.DurationMinutes IS NOT NULL
        ) all_sources
        GROUP BY FeatureId
    ) act ON act.FeatureId = f.FeatureId
"""


def _row_to_feature(r: dict) -> FeatureRow:
    return FeatureRow(
        feature_id=r["FeatureId"], module_id=r["ModuleId"], module_name=r["ModuleName"] or "",
        product_id=r["ProductId"], product_name=r["ProductName"] or "",
        name=r["Name"] or "", description=r["Description"] or "",
        feature_owner_user_id=r["FeatureOwnerUserId"], feature_owner_name=r["FeatureOwnerName"],
        technical_owner_user_id=r["TechnicalOwnerUserId"], technical_owner_name=r["TechnicalOwnerName"],
        enabled=bool(r["Enabled"]),
        actual_minutes=int(r["ActualMinutes"]) if r["ActualMinutes"] is not None else 0,
    )


@router.get("/features", response_model=list[FeatureRow])
def get_features(module_id: int | None = None, product_id: int | None = None, include_disabled: bool = False) -> list[FeatureRow]:
    where: list[str] = []
    params: list = []
    if module_id is not None:
        where.append("f.ModuleId = ?")
        params.append(module_id)
    # product_id scopes across every Module under that Product (unlike
    # module_id above, which the existing filter bar already narrows to one
    # Module at a time) — added for the breadcrumb's "Product Name" link on
    # Feature/Dev Item detail, which lands here with only a Product known,
    # not yet a specific Module.
    if product_id is not None:
        where.append("m.ProductId = ?")
        params.append(product_id)
    if not include_disabled:
        where.append("f.Enabled = 1")
    sql = _FEATURE_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY f.Name"
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [_row_to_feature(r) for r in rows]


@router.get("/features/lookup", response_model=list[LookupOption])
def get_features_lookup(module_id: int | None = None) -> list[LookupOption]:
    # module_id omitted -> every enabled feature across all modules, used by
    # the Tasks page's unscoped Feature filter (not a cascading picker there).
    with get_cursor() as cursor:
        if module_id is not None:
            cursor.execute(
                "SELECT FeatureId, Name FROM EngHub_Feature WHERE ModuleId = ? AND Enabled = 1 ORDER BY Name",
                module_id,
            )
        else:
            cursor.execute("SELECT FeatureId, Name FROM EngHub_Feature WHERE Enabled = 1 ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [LookupOption(label=r["Name"] or "", value=r["FeatureId"]) for r in rows]


@router.get("/features/{feature_id}", response_model=FeatureRow)
def get_feature(feature_id: int) -> FeatureRow:
    with get_cursor() as cursor:
        cursor.execute(_FEATURE_SELECT + " WHERE f.FeatureId = ?", feature_id)
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Feature not found")
    return _row_to_feature(row)


class FeatureForm(BaseModel):
    feature_id: int
    module_id: int
    name: str
    description: str
    enabled: bool


@router.post("/features")
def save_feature(row: FeatureForm, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.feature_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Feature (ModuleId, Name, Description, Enabled, CreatedByUserId) "
                "VALUES (?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
                row.module_id, row.name, row.description, row.enabled, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
        else:
            cursor.execute(
                "UPDATE EngHub_Feature SET ModuleId=?, Name=?, Description=?, Enabled=?, "
                "LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE FeatureId=?",
                row.module_id, row.name, row.description, row.enabled, user.user_id, row.feature_id,
            )
            new_id = row.feature_id
    return {"success": True, "feature_id": new_id}


# Feature Owner / Technical Owner are the only two AssignmentHistory
# RoleTypes that also have a convenience column on EngHub_Feature itself —
# Developer/Tester (Task-level) don't, see enghub_tasks.py.
_FEATURE_ROLE_COLUMNS = {"FeatureOwner": "FeatureOwnerUserId", "TechnicalOwner": "TechnicalOwnerUserId"}


@router.post("/features/{feature_id}/assign")
def assign_feature_role(feature_id: int, body: AssignRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    assign_role("Feature", feature_id, body.role_type, body.user_id, user)
    column = _FEATURE_ROLE_COLUMNS.get(body.role_type)
    if column:
        with get_cursor() as cursor:
            cursor.execute(f"UPDATE EngHub_Feature SET {column} = ? WHERE FeatureId = ?", body.user_id, feature_id)
    return {"success": True}


@router.get("/features/{feature_id}/assignment-history", response_model=list[AssignmentHistoryRow])
def get_feature_assignment_history(feature_id: int) -> list[AssignmentHistoryRow]:
    return get_assignment_history("Feature", feature_id)


# ---------------------------------------------------------------------------
# Trouble Ticket linking — exactly one real Trouble Ticket per Feature at a
# time (not a multi-add list; EngHub_FeatureTicket's schema still allows
# more than one row per FeatureId, but set_feature_ticket() below always
# clears any existing row first, so in practice there's only ever 0 or 1).
# Search reuses the same TTMaster-backed /tickets endpoint already proven for
# Development Items' Support-origin picker (enghub_devitems.py);
# ticket_customer_name here adds CustomerMaster context that picker's
# plainer voucher+date label doesn't need.
# ---------------------------------------------------------------------------

class FeatureTicketRow(BaseModel):
    feature_ticket_id: int
    ticket_voucher_no: int
    ticket_date: str | None = None
    ticket_customer_name: str | None = None
    added_by_user_id: int
    added_by_name: str
    added_at: str


@router.get("/features/{feature_id}/ticket", response_model=FeatureTicketRow | None)
def get_feature_ticket(feature_id: int) -> FeatureTicketRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 1 ft.FeatureTicketId, ft.TicketVoucherNo, tm.Date AS TicketDate, "
            "LTRIM(RTRIM(cm.DisplayName)) AS CustomerName, ft.AddedByUserId, u.Name AS AddedByName, ft.AddedAt "
            "FROM EngHub_FeatureTicket ft "
            "LEFT JOIN TTMaster tm ON tm.VoucherNo = ft.TicketVoucherNo "
            "LEFT JOIN CustomerMaster cm ON cm.CustID = tm.CustID "
            "JOIN UserMaster u ON u.UserID = ft.AddedByUserId "
            "WHERE ft.FeatureId = ? ORDER BY ft.AddedAt DESC",
            feature_id,
        )
        row = first_row_or_none(cursor)
    if row is None:
        return None
    return FeatureTicketRow(
        feature_ticket_id=row["FeatureTicketId"], ticket_voucher_no=row["TicketVoucherNo"],
        ticket_date=str(row["TicketDate"]) if row["TicketDate"] else None, ticket_customer_name=row["CustomerName"],
        added_by_user_id=row["AddedByUserId"], added_by_name=row["AddedByName"] or "",
        added_at=row["AddedAt"].isoformat() if row["AddedAt"] else "",
    )


class SetFeatureTicketRequest(BaseModel):
    ticket_voucher_no: int | None = None


@router.put("/features/{feature_id}/ticket")
def set_feature_ticket(feature_id: int, body: SetFeatureTicketRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if body.ticket_voucher_no is not None:
            cursor.execute("SELECT 1 FROM TTMaster WHERE VoucherNo = ?", body.ticket_voucher_no)
            if first_row_or_none(cursor) is None:
                raise HTTPException(status_code=404, detail="Ticket not found")
        # Always clear any existing link first — this endpoint sets THE one
        # ticket for the Feature, it doesn't add to a list.
        cursor.execute("DELETE FROM EngHub_FeatureTicket WHERE FeatureId = ?", feature_id)
        if body.ticket_voucher_no is not None:
            cursor.execute(
                "INSERT INTO EngHub_FeatureTicket (FeatureId, TicketVoucherNo, AddedByUserId) VALUES (?, ?, ?)",
                feature_id, body.ticket_voucher_no, user.user_id,
            )
    return {"success": True}
