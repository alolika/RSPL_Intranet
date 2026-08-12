"""Engineering Hub - Development Items (Feature > Development Item).

Origin captures where the work came from (Support/Management/Sales/
Customer/Partner/Tester/Developer per the PRD) plus two optional real FKs
into the same WNT database: OriginTicketVoucherNo -> TTMaster.VoucherNo and
CustomerId -> CustomerMaster.CustID — referencing existing data rather than
duplicating it, per the approved design. /customers and /tickets below are
lightweight search endpoints for picking those references, not a copy of
Support's ticket data.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-development-items"])


class LookupOption(BaseModel):
    label: str
    value: int


class DevItemRow(BaseModel):
    dev_item_id: int
    feature_id: int
    feature_name: str
    module_id: int
    product_id: int
    dev_item_type_id: int
    dev_item_type_name: str
    title: str
    description: str
    origin_type_id: int | None = None
    origin_type_name: str | None = None
    origin_ticket_voucher_no: int | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    priority_id: int | None = None
    priority_name: str | None = None
    status_id: int
    status_name: str
    is_terminal: bool
    closed_at: str | None = None
    actual_minutes: int = 0


# Time Spent rolls up EVERY duration-carrying Activity attached to this
# DevItem — directly (a.DevItemId, e.g. Discussion/Comment logged straight
# against the Dev Item) OR via one of its Tasks (a.TaskId, e.g. the My Tasks
# grid's Log Work column) — same "any activity with a duration counts as
# effort" convention already established by enghub_tasks.py's TaskRow.actual_hours
# (Work Logged, Discussion, Interruption entries can all carry a duration,
# not narrowed to just Work Logged). The two sources are UNION ALL'd before
# summing rather than two separate LEFT JOINs, which would double-count via
# the join fan-out.
_DEV_ITEM_SELECT = """
    SELECT d.DevItemId, d.FeatureId, f.Name AS FeatureName, f.ModuleId, m.ProductId,
           d.DevItemTypeId, dt.Name AS DevItemTypeName,
           d.Title, d.Description,
           d.OriginTypeId, ot.Name AS OriginTypeName,
           d.OriginTicketVoucherNo, d.CustomerId, c.DisplayName AS CustomerName,
           d.PriorityId, pr.Name AS PriorityName,
           d.StatusId, s.Name AS StatusName, s.IsTerminal, d.ClosedAt,
           act.TotalMinutes AS ActualMinutes
    FROM EngHub_DevelopmentItem d
    JOIN EngHub_Feature f ON f.FeatureId = d.FeatureId
    JOIN EngHub_Module m ON m.ModuleId = f.ModuleId
    JOIN EngHub_DevItemType dt ON dt.DevItemTypeId = d.DevItemTypeId
    JOIN EngHub_Status s ON s.StatusId = d.StatusId
    LEFT JOIN EngHub_OriginType ot ON ot.OriginTypeId = d.OriginTypeId
    LEFT JOIN EngHub_Priority pr ON pr.PriorityId = d.PriorityId
    LEFT JOIN CustomerMaster c ON c.CustID = d.CustomerId
    LEFT JOIN (
        SELECT DevItemId, SUM(DurationMinutes) AS TotalMinutes
        FROM (
            SELECT a.DevItemId, a.DurationMinutes
            FROM EngHub_Activity a
            WHERE a.DevItemId IS NOT NULL AND a.DurationMinutes IS NOT NULL
            UNION ALL
            SELECT t.DevItemId, a.DurationMinutes
            FROM EngHub_Activity a
            JOIN EngHub_Task t ON t.TaskId = a.TaskId
            WHERE a.DurationMinutes IS NOT NULL
        ) both_sources
        GROUP BY DevItemId
    ) act ON act.DevItemId = d.DevItemId
"""


def _row_to_dev_item(r: dict) -> DevItemRow:
    return DevItemRow(
        dev_item_id=r["DevItemId"], feature_id=r["FeatureId"], feature_name=r["FeatureName"] or "",
        module_id=r["ModuleId"], product_id=r["ProductId"],
        dev_item_type_id=r["DevItemTypeId"], dev_item_type_name=r["DevItemTypeName"] or "",
        title=r["Title"] or "", description=r["Description"] or "",
        origin_type_id=r["OriginTypeId"], origin_type_name=r["OriginTypeName"],
        origin_ticket_voucher_no=r["OriginTicketVoucherNo"], customer_id=r["CustomerId"],
        customer_name=(r["CustomerName"] or "").strip() or None,
        priority_id=r["PriorityId"], priority_name=r["PriorityName"],
        status_id=r["StatusId"], status_name=r["StatusName"] or "", is_terminal=bool(r["IsTerminal"]),
        closed_at=r["ClosedAt"].isoformat() if r["ClosedAt"] else None,
        actual_minutes=int(r["ActualMinutes"]) if r["ActualMinutes"] is not None else 0,
    )


@router.get("/development-items", response_model=list[DevItemRow])
def get_development_items(
    feature_id: int | None = None, status_id: int | None = None, include_closed: bool = False
) -> list[DevItemRow]:
    where: list[str] = []
    params: list = []
    if feature_id is not None:
        where.append("d.FeatureId = ?")
        params.append(feature_id)
    if status_id is not None:
        where.append("d.StatusId = ?")
        params.append(status_id)
    if not include_closed:
        where.append("s.IsTerminal = 0")
    sql = _DEV_ITEM_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.DevItemId DESC"
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_dev_item(r) for r in rows]


@router.get("/development-items/lookup", response_model=list[LookupOption])
def get_development_items_lookup(feature_id: int | None = None) -> list[LookupOption]:
    # feature_id omitted -> every development item across all features, used
    # by the Tasks page's unscoped Development Item filter (not a cascading
    # picker there); capped per this app's established unbounded-query caution.
    with get_cursor() as cursor:
        if feature_id is not None:
            cursor.execute(
                "SELECT DevItemId, Title FROM EngHub_DevelopmentItem WHERE FeatureId = ? ORDER BY DevItemId DESC",
                feature_id,
            )
            rows = rows_to_dicts(cursor)
        else:
            cursor.execute("SELECT DevItemId, Title FROM EngHub_DevelopmentItem ORDER BY DevItemId DESC")
            rows = rows_to_dicts(cursor, limit=500)
    return [LookupOption(label=r["Title"] or "", value=r["DevItemId"]) for r in rows]


@router.get("/development-items/{dev_item_id}", response_model=DevItemRow)
def get_development_item(dev_item_id: int) -> DevItemRow:
    with get_cursor() as cursor:
        cursor.execute(_DEV_ITEM_SELECT + " WHERE d.DevItemId = ?", dev_item_id)
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Development Item not found")
    return _row_to_dev_item(row)


class DevItemForm(BaseModel):
    dev_item_id: int
    feature_id: int
    dev_item_type_id: int
    title: str
    description: str
    origin_type_id: int | None = None
    origin_ticket_voucher_no: int | None = None
    customer_id: int | None = None
    priority_id: int | None = None
    status_id: int


@router.post("/development-items")
def save_development_item(row: DevItemForm, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if row.dev_item_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_DevelopmentItem "
                "(FeatureId, DevItemTypeId, Title, Description, OriginTypeId, OriginTicketVoucherNo, "
                "CustomerId, PriorityId, StatusId, CreatedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?); "
                "SELECT SCOPE_IDENTITY() AS Id",
                row.feature_id, row.dev_item_type_id, row.title, row.description, row.origin_type_id,
                row.origin_ticket_voucher_no, row.customer_id, row.priority_id, row.status_id, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
        else:
            cursor.execute(
                "UPDATE EngHub_DevelopmentItem SET FeatureId=?, DevItemTypeId=?, Title=?, Description=?, "
                "OriginTypeId=?, OriginTicketVoucherNo=?, CustomerId=?, PriorityId=?, StatusId=?, "
                "LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE DevItemId=?",
                row.feature_id, row.dev_item_type_id, row.title, row.description, row.origin_type_id,
                row.origin_ticket_voucher_no, row.customer_id, row.priority_id, row.status_id,
                user.user_id, row.dev_item_id,
            )
            new_id = row.dev_item_id
    return {"success": True, "dev_item_id": new_id}


class CustomerLookupRow(BaseModel):
    cust_id: int
    display_name: str


@router.get("/customers", response_model=list[CustomerLookupRow])
def search_customers(search: str = "") -> list[CustomerLookupRow]:
    # Same top-100 + type-to-search shape as SupportDataService.getCustomers /
    # admin_vouchers.get_customers — CustomerMaster is 80k+ rows.
    words = [w for w in search.strip().split() if w]
    where = "Groupid = 1 AND CustID <> 0 AND DisplayName <> ''"
    params: list = []
    for w in words:
        where += " AND DisplayName LIKE ?"
        params.append(f"%{w}%")
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT TOP 100 CustID, LTRIM(RTRIM(UPPER(DisplayName))) AS DisplayName "
            f"FROM CustomerMaster WHERE {where} ORDER BY DisplayName",
            *params,
        )
        rows = rows_to_dicts(cursor)
    return [CustomerLookupRow(cust_id=r["CustID"], display_name=r["DisplayName"] or "") for r in rows]


class TicketLookupRow(BaseModel):
    voucher_no: int
    date: str | None = None
    customer_name: str | None = None


@router.get("/tickets", response_model=list[TicketLookupRow])
def search_tickets(search: str = "") -> list[TicketLookupRow]:
    # customer_name is an additive field (existing callers that only read
    # voucher_no/date, e.g. the Dev Item Support-origin picker, are
    # unaffected) — joined in so search results can show who a ticket
    # belongs to, not just its bare number.
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 50 TM.VoucherNo, TM.Date, LTRIM(RTRIM(CM.DisplayName)) AS CustomerName "
                "FROM TTMaster TM LEFT JOIN CustomerMaster CM ON CM.CustID = TM.CustID "
                "WHERE CAST(TM.VoucherNo AS VARCHAR(20)) LIKE ? ORDER BY TM.VoucherNo DESC",
                f"%{search.strip()}%",
            )
        else:
            cursor.execute(
                "SELECT TOP 50 TM.VoucherNo, TM.Date, LTRIM(RTRIM(CM.DisplayName)) AS CustomerName "
                "FROM TTMaster TM LEFT JOIN CustomerMaster CM ON CM.CustID = TM.CustID "
                "ORDER BY TM.VoucherNo DESC"
            )
        rows = rows_to_dicts(cursor)
    return [
        TicketLookupRow(voucher_no=r["VoucherNo"], date=str(r["Date"]) if r["Date"] else None, customer_name=r["CustomerName"])
        for r in rows
    ]
