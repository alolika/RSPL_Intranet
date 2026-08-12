"""Engineering Hub - Decision Log.

Captures WHY something was implemented a particular way (Customer Exception,
Management Override, Technical Debt, etc.) — the PRD's "Feature Timeline" is
a UNION of EngHub_Activity and EngHub_Decision rows for a given scope, so
this router's rollup query mirrors enghub_activities.py's exactly (same
nested Feature -> DevItem -> Task IN-clause shape) rather than inventing a
different scoping mechanism for the two entity types the Timeline merges.

A Decision may attach to a Feature and/or DevelopmentItem and/or Task (at
least one required) — same flexible-attachment shape as Activity, per the
approved design.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-decisions"])


class DecisionRow(BaseModel):
    decision_id: int
    feature_id: int | None = None
    feature_name: str | None = None
    dev_item_id: int | None = None
    dev_item_title: str | None = None
    task_id: int | None = None
    task_title: str | None = None
    decision_type_id: int
    decision_type_name: str
    description: str
    approver_user_id: int | None = None
    approver_name: str | None = None
    approver_external_name: str | None = None
    reason: str
    risk_level: str
    review_date: str | None = None
    status_id: int
    status_name: str
    customer_id: int | None = None
    customer_name: str | None = None
    created_by_user_id: int
    created_by_name: str
    created_at: str


_DECISION_SELECT = """
    SELECT d.DecisionId, d.FeatureId, f.Name AS FeatureName, d.DevItemId, di.Title AS DevItemTitle,
           d.TaskId, t.Title AS TaskTitle, d.DecisionTypeId, dt.Name AS DecisionTypeName,
           d.Description, d.ApproverUserId, au.Name AS ApproverName, d.ApproverExternalName,
           d.Reason, d.RiskLevel, d.ReviewDate, d.StatusId, s.Name AS StatusName,
           d.CustomerId, c.DisplayName AS CustomerName,
           d.CreatedByUserId, cu.Name AS CreatedByName, d.CreatedAt
    FROM EngHub_Decision d
    JOIN EngHub_DecisionType dt ON dt.DecisionTypeId = d.DecisionTypeId
    JOIN EngHub_Status s ON s.StatusId = d.StatusId
    JOIN UserMaster cu ON cu.UserID = d.CreatedByUserId
    LEFT JOIN EngHub_Feature f ON f.FeatureId = d.FeatureId
    LEFT JOIN EngHub_DevelopmentItem di ON di.DevItemId = d.DevItemId
    LEFT JOIN EngHub_Task t ON t.TaskId = d.TaskId
    LEFT JOIN UserMaster au ON au.UserID = d.ApproverUserId
    LEFT JOIN CustomerMaster c ON c.CustID = d.CustomerId
"""


def _row_to_decision(r: dict) -> DecisionRow:
    return DecisionRow(
        decision_id=r["DecisionId"], feature_id=r["FeatureId"], feature_name=r["FeatureName"],
        dev_item_id=r["DevItemId"], dev_item_title=r["DevItemTitle"], task_id=r["TaskId"], task_title=r["TaskTitle"],
        decision_type_id=r["DecisionTypeId"], decision_type_name=r["DecisionTypeName"] or "",
        description=r["Description"] or "", approver_user_id=r["ApproverUserId"], approver_name=r["ApproverName"],
        approver_external_name=r["ApproverExternalName"], reason=r["Reason"] or "", risk_level=r["RiskLevel"],
        review_date=str(r["ReviewDate"]) if r["ReviewDate"] else None,
        status_id=r["StatusId"], status_name=r["StatusName"] or "",
        customer_id=r["CustomerId"], customer_name=r["CustomerName"],
        created_by_user_id=r["CreatedByUserId"], created_by_name=r["CreatedByName"] or "",
        created_at=r["CreatedAt"].isoformat() if r["CreatedAt"] else "",
    )


@router.get("/decisions", response_model=list[DecisionRow])
def get_decisions(
    feature_id: int | None = None,
    dev_item_id: int | None = None,
    task_id: int | None = None,
    decision_type_id: int | None = None,
    status_id: int | None = None,
    customer_id: int | None = None,
) -> list[DecisionRow]:
    scoped = sum(x is not None for x in (feature_id, dev_item_id, task_id))
    if scoped > 1:
        raise HTTPException(status_code=400, detail="Provide at most one of feature_id, dev_item_id, task_id")

    where: list[str] = []
    params: list = []

    if feature_id is not None:
        where.append(
            "(d.FeatureId = ? "
            "OR d.DevItemId IN (SELECT DevItemId FROM EngHub_DevelopmentItem WHERE FeatureId = ?) "
            "OR d.TaskId IN (SELECT TaskId FROM EngHub_Task WHERE DevItemId IN "
            "(SELECT DevItemId FROM EngHub_DevelopmentItem WHERE FeatureId = ?)))"
        )
        params += [feature_id, feature_id, feature_id]
    elif dev_item_id is not None:
        where.append("(d.DevItemId = ? OR d.TaskId IN (SELECT TaskId FROM EngHub_Task WHERE DevItemId = ?))")
        params += [dev_item_id, dev_item_id]
    elif task_id is not None:
        where.append("d.TaskId = ?")
        params.append(task_id)

    if decision_type_id is not None:
        where.append("d.DecisionTypeId = ?")
        params.append(decision_type_id)
    if status_id is not None:
        where.append("d.StatusId = ?")
        params.append(status_id)
    if customer_id is not None:
        where.append("d.CustomerId = ?")
        params.append(customer_id)

    sql = _DECISION_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.CreatedAt DESC"
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_decision(r) for r in rows]


@router.get("/decisions/{decision_id}", response_model=DecisionRow)
def get_decision(decision_id: int) -> DecisionRow:
    with get_cursor() as cursor:
        cursor.execute(f"{_DECISION_SELECT} WHERE d.DecisionId = ?", decision_id)
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return _row_to_decision(row)


class DecisionForm(BaseModel):
    decision_id: int
    feature_id: int | None = None
    dev_item_id: int | None = None
    task_id: int | None = None
    decision_type_id: int
    description: str
    approver_user_id: int | None = None
    approver_external_name: str | None = None
    reason: str
    risk_level: str
    review_date: str | None = None
    status_id: int
    customer_id: int | None = None


@router.post("/decisions")
def save_decision(form: DecisionForm, user: CurrentUser = Depends(get_current_user)) -> dict:
    if form.feature_id is None and form.dev_item_id is None and form.task_id is None:
        raise HTTPException(status_code=400, detail="Provide at least one of feature_id, dev_item_id, task_id")

    with get_cursor() as cursor:
        if form.decision_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Decision (FeatureId, DevItemId, TaskId, DecisionTypeId, Description, "
                "ApproverUserId, ApproverExternalName, Reason, RiskLevel, ReviewDate, StatusId, CustomerId, "
                "CreatedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
                form.feature_id, form.dev_item_id, form.task_id, form.decision_type_id, form.description,
                form.approver_user_id, form.approver_external_name, form.reason, form.risk_level,
                form.review_date, form.status_id, form.customer_id, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
        else:
            cursor.execute(
                "UPDATE EngHub_Decision SET FeatureId=?, DevItemId=?, TaskId=?, DecisionTypeId=?, Description=?, "
                "ApproverUserId=?, ApproverExternalName=?, Reason=?, RiskLevel=?, ReviewDate=?, StatusId=?, "
                "CustomerId=?, LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE DecisionId=?",
                form.feature_id, form.dev_item_id, form.task_id, form.decision_type_id, form.description,
                form.approver_user_id, form.approver_external_name, form.reason, form.risk_level,
                form.review_date, form.status_id, form.customer_id, user.user_id, form.decision_id,
            )
            new_id = form.decision_id
    return {"success": True, "decision_id": new_id}
