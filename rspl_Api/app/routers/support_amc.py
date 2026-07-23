"""Mirrors Section_Support/AMCPending.aspx.vb (AMCPending + AMCLastAction
menu entries via ?FromMenu=), AMCCo-OrdinatorDashBoard.aspx.vb,
CustAMCFollowUp.aspx.vb, Section_Admin/AddNewAction.aspx.vb, and
Section_Marketing/CustEnquiryAction.aspx.vb — all share the same underlying
webProc_AddEnquiryAction/webProc_CloseEnquiryAction/WebProc_ModifyEnquiryAction
procs; CustEnquiryAction is the fullest version (adds Competitor/Special
Rating/Thanks-Note-reasons/Quotation-amount fields on top of AMC's simpler
add/close flow), so its optional fields live on the same request models here
rather than duplicating a second copy of this proc-calling logic.

Note: webProc_AddEnquiryAction takes 25 positional params and
webProc_CloseEnquiryAction takes 13 — none have SQL-side defaults (confirmed
via sys.parameters). The three call sites below were originally written
short by 4-5 trailing params each (before CustEnquiryAction's fuller field
set surfaced the mismatch) — every one of them would have raised "too few
arguments" the first time they actually ran. Fixed here; flagged in memory
since none of them had been live-tested (per the established policy of not
executing mutating endpoints against real data).
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/support/amc", tags=["support-amc"])


class LookupOption(BaseModel):
    label: str
    value: int


class AmcRow(BaseModel):
    cust_id: int
    display_name: str
    area: str
    city: str
    mobile_no: str
    email: str
    creation_date: str | None
    ledger_balance: float
    ledger_group_balance: float
    action_date: str | None
    amc_amount: float
    amc_due_date: str | None
    action_stage_id: int
    ass_value: float
    amc_per: float
    basic_amc_amt: float
    service_tax: float


class AmcCoordPendingRow(BaseModel):
    action_date: str | None
    customer: str
    action_taken_by: str
    action_on: str
    action_description: str
    action_remark: str
    cust_id: int


class AmcCoordAmcRow(BaseModel):
    cust_id: int
    mobile_no: str
    area: str
    customer: str
    amc_per: float
    expiry_date: str | None
    city: str
    assessable_amt: float
    last_letter_date: str | None
    last_letter_value: float
    last_letter_no: int
    tt_after_expiry: int
    calls_after_expiry: int


class AmcActionRow(BaseModel):
    action_id: int
    creation_date: str | None
    creation_time: str
    follow_up_by: str
    follow_up_remark: str
    completion_date: str
    completion_time: str
    completed_by: str
    assigned_to: str
    action_description: str
    action_remark: str
    next_action: str


class AddEnquiryActionRequest(BaseModel):
    cust_id: int
    ref_action_id: int
    action_stage_id: int
    action_date: date
    action_time: str
    description: str
    assigned_to: int
    remark: str
    close_action: bool
    sales_person_id: int
    action_stage_status_id: int = 0
    competitor_id: int = 0
    special_rating: str = ""
    reasons: list[int] = []
    quotation_amt: float = 0
    is_modify: bool = False


class CloseEnquiryActionAdd(BaseModel):
    action_stage_id: int
    action_date: date
    action_time: str
    description: str
    assigned_to: int
    remark: str
    sales_person_id: int
    action_stage_status_id: int = 0
    reasons: list[int] = []
    quotation_amt: float = 0


class CloseEnquiryActionRequest(BaseModel):
    cust_id: int
    ref_action_id: int
    completed_by: int
    completed_date: date
    completed_time: str
    salesman_id: int
    narration: str
    action_stage_id: int
    special_rating: str = ""
    reasons: list[int] = []
    quotation_amt: float = 0
    add_new_action: CloseEnquiryActionAdd | None = None


class AmcFollowUpRequest(BaseModel):
    cust_id: int
    follow_up_by: int
    follow_up_date: date
    follow_up_time: str
    contact_person: str
    remark: str


def _dmy(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def _reasons_text(cursor, reason_ids: list[int]) -> str:
    """Mirrors CustEnquiryAction.aspx.vb's strReasons builder: joins reason
    *labels* (not ids) as " ,Label ,Label"."""
    if not reason_ids:
        return ""
    placeholders = ", ".join("?" for _ in reason_ids)
    cursor.execute(
        f"SELECT ReasonsForThankYouNote FROM web_ThanksNoteActionReasons WHERE ID IN ({placeholders})",
        *reason_ids,
    )
    labels = [r[0] for r in cursor.fetchall()]
    return " ,".join(labels)


@router.get("/products", response_model=list[LookupOption])
def get_products() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT AttrValue, AttrValueID FROM CustAttrMasterValue WHERE AttributeID = 1 ORDER BY UserId"
        )
        return [LookupOption(label=r["AttrValue"], value=r["AttrValueID"]) for r in rows_to_dicts(cursor)]


@router.get("/ledger-groups", response_model=list[LookupOption])
def get_ledger_groups() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT LedgerGroupId, Name FROM LedgerGroupmaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["LedgerGroupId"]) for r in rows_to_dicts(cursor)]


@router.get("/rows", response_model=list[AmcRow])
def get_amc_rows(
    mode: str,
    customer: str = "",
    product_id: int = 0,
    sign: str = ">=",
    amount: str = "",
    from_expiry_date: date | None = None,
    to_expiry_date: date | None = None,
    user_id: int = 0,
) -> list[AmcRow]:
    with get_cursor() as cursor:
        if mode == "AMCPending":
            where = " WHERE 1 = 1"
            params: list = []
            if customer:
                where += " AND Displayname LIKE ?"
                params.append(f"%{customer}%")
            if product_id:
                cursor.execute("SELECT AttrValue FROM CustAttrMasterValue WHERE AttrValueID = ?", product_id)
                prod_row = cursor.fetchone()
                if prod_row:
                    where += " AND Softwarename = ?"
                    params.append(prod_row[0])
            if amount and sign in (">=", "<=", "=", ">", "<"):
                where += f" AND AMCAmount {sign} ?"
                params.append(amount)
            if from_expiry_date and to_expiry_date:
                where += " AND AMCDueDate >= ? AND AMCDueDate <= ?"
                params += [_dmy(from_expiry_date), _dmy(to_expiry_date)]
            elif to_expiry_date:
                where += " AND AMCDueDate <= ?"
                params.append(_dmy(to_expiry_date))
            elif from_expiry_date:
                where += " AND AMCDueDate >= ?"
                params.append(_dmy(from_expiry_date))

            cursor.execute(
                f"SELECT '' ActionDescription, '' ActionDate, 0 LedgerGroupBalance, * FROM VWAMCForAllCustomers{where} ORDER BY DisplayName",
                *params,
            )
            rows = rows_to_dicts(cursor)
        else:
            cursor.execute("EXEC WebProc_GetLastAMCAction ?, 0, '', ''", user_id)
            rows = rows_to_dicts(cursor)

    return [
        AmcRow(
            cust_id=r["Custid"], display_name=r["Displayname"] or "", area=r.get("Area") or "",
            city=r.get("City") or "", mobile_no=r.get("Mobileno") or "", email=r.get("Email") or "",
            creation_date=r["CreationDate"].isoformat() if r.get("CreationDate") else None,
            ledger_balance=float(r.get("Ledgerbalance") or 0), ledger_group_balance=float(r.get("LedgerGroupBalance") or 0),
            action_date=r["ActionDate"].isoformat() if isinstance(r.get("ActionDate"), datetime) else None,
            amc_amount=float(r.get("AMCAmount") or 0),
            amc_due_date=r["AMCDueDate"].isoformat() if r.get("AMCDueDate") else None,
            action_stage_id=r.get("ActionStageID") or 0, ass_value=float(r.get("Assvalue") or 0),
            amc_per=float(r.get("AMCPer") or 0), basic_amc_amt=float(r.get("BasicAMCAmt") or 0),
            service_tax=float(r.get("ServiceTax") or 0),
        )
        for r in rows
    ]


@router.get("/coord/action-on-users", response_model=list[str])
def get_action_on_users() -> list[str]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT Um.Name FROM Web_EnquiryAction WA INNER JOIN UserMaster Um ON WA.AssignedTo = Um.UserID "
            "WHERE Um.UserID > 0 ORDER BY Um.Name"
        )
        return [r["Name"] for r in rows_to_dicts(cursor)]


@router.get("/coord/pending", response_model=list[AmcCoordPendingRow])
def get_coord_pending(action_on: str = "", remark: str = "", customer: str = "") -> list[AmcCoordPendingRow]:
    where = ""
    params: list = []
    if action_on and action_on != "All":
        where += " AND Um1.Name = ?"
        params.append(action_on)
    if remark:
        where += " AND ActionRemark LIKE ?"
        params.append(f"%{remark}%")
    if customer:
        where += " AND Cm.DisplayName = ?"
        params.append(customer)

    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT W.ActionDate, Cm.Displayname AS Customer, Um.Name AS ActionTakenBy,
                   (SELECT Name FROM UserMaster WHERE UserID = W.AssignedTo) AS ActionOn,
                   W.ActionDescription, W.ActionRemark, W.CustID
            FROM Web_EnquiryAction W
            INNER JOIN UserMaster UM ON W.CreatedBY = Um.Userid
            INNER JOIN UserMaster UM1 ON W.AssignedTo = Um1.Userid
            INNER JOIN CustomerMaster Cm ON W.Custid = Cm.Custid
            WHERE ActionStageId = 18 AND Actioncompleted = 0
              AND ActionDate = (SELECT BillDate FROM Options) {where}
            ORDER BY W.ActionDate, Cm.Displayname
            """,
            *params,
        )
        rows = rows_to_dicts(cursor)

    return [
        AmcCoordPendingRow(
            action_date=r["ActionDate"].isoformat() if r["ActionDate"] else None, customer=r["Customer"] or "",
            action_taken_by=r["ActionTakenBy"] or "", action_on=r["ActionOn"] or "",
            action_description=r["ActionDescription"] or "", action_remark=r["ActionRemark"] or "", cust_id=r["CustID"],
        )
        for r in rows
    ]


@router.get("/coord/amc-rows", response_model=list[AmcCoordAmcRow])
def get_coord_amc_rows(
    product_id: int = 0, ledger_group_id: int = 0, from_expiry_date: date | None = None,
    to_expiry_date: date | None = None, customer: str = "",
) -> list[AmcCoordAmcRow]:
    where = ""
    params: list = []
    with get_cursor() as cursor:
        if product_id:
            cursor.execute("SELECT AttrValue FROM CustAttrMasterValue WHERE AttrValueID = ?", product_id)
            prod_row = cursor.fetchone()
            if prod_row:
                where += " AND custid IN (SELECT custid FROM customerattributes WHERE AttrValue = ?)"
                params.append(prod_row[0])
        if ledger_group_id:
            where += " AND LedgerGroupId = ?"
            params.append(ledger_group_id)
        if from_expiry_date and to_expiry_date:
            where += " AND ExpiryDate BETWEEN ? AND ?"
            params += [from_expiry_date, to_expiry_date]
        if customer:
            where += " AND DisplayName = ?"
            params.append(customer)

        # Confirmed live: unfiltered, this returns 5,105+ rows in ~5.4s (no
        # cap previously existed) — the same "unfiltered report over a large
        # customer table" performance issue already capped elsewhere in this
        # app (VisitLogReport, CustLedger, TTRegister, etc.). TOP 1000 with
        # the existing ORDER BY matches that established convention.
        cursor.execute(
            f"""
            SELECT DISTINCT TOP 1000 Mobileno, area, displayname AS Customer, custid, AMCPer,
                   Expirydate, city, SUM(AssessableValue) AS AssessableAmt,
                   (SELECT TOP 1 date FROM AMCIssuemaster WHERE custid = v.custid ORDER BY Date DESC) AS LastLetterDate,
                   (SELECT TOP 1 AMCAMT FROM AMCIssuemaster WHERE custid = v.custid ORDER BY Date DESC, Letterno DESC) AS LastLetterValue,
                   (SELECT TOP 1 Letterno FROM AMCIssuemaster WHERE custid = v.custid ORDER BY Date DESC) AS LastLetterNo,
                   ISNULL((SELECT COUNT(*) FROM TTMaster WHERE custid = v.custid AND Date >= v.expiryDate), 0) AS TTAfterExpiry,
                   ISNULL((SELECT COUNT(*) FROM SIPEventRegister WHERE EntityTypeid = 3 AND EntityID = v.Custid AND Date >= v.ExpiryDate), 0) AS CallsAfterExpiry
            FROM Web_View_CustomerAMCList v
            WHERE 1 = 1 {where}
            GROUP BY Mobileno, DisplayName, v.CustID, AMCPer, ExpiryDate, area, City
            ORDER BY DisplayName
            """,
            *params,
        )
        rows = rows_to_dicts(cursor)

    return [
        AmcCoordAmcRow(
            cust_id=r["custid"], mobile_no=r["Mobileno"] or "", area=r["area"] or "", customer=r["Customer"] or "",
            amc_per=float(r["AMCPer"] or 0), expiry_date=r["Expirydate"].isoformat() if r["Expirydate"] else None,
            city=r["city"] or "", assessable_amt=float(r["AssessableAmt"] or 0),
            last_letter_date=r["LastLetterDate"].isoformat() if r["LastLetterDate"] else None,
            last_letter_value=float(r["LastLetterValue"] or 0), last_letter_no=r["LastLetterNo"] or 0,
            tt_after_expiry=r["TTAfterExpiry"] or 0, calls_after_expiry=r["CallsAfterExpiry"] or 0,
        )
        for r in rows
    ]


@router.get("/action-stages", response_model=list[LookupOption])
def get_action_stages() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ActionStageId, Name FROM web_enquiryactionstage")
        return [LookupOption(label=r["Name"], value=r["ActionStageId"]) for r in rows_to_dicts(cursor)]


@router.get("/action-stage-statuses", response_model=list[LookupOption])
def get_action_stage_statuses(action_stage_id: int) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT L.ID, L.MasterValue FROM Web_LittleMaster L "
            "INNER JOIN Web_EnquiryActionStage W ON L.MasterName = W.ActionStageStatusMaster "
            "WHERE W.ActionStageID = ?",
            action_stage_id,
        )
        return [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.post("/enquiry-action")
def add_enquiry_action(
    body: AddEnquiryActionRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    today = datetime.now()
    with get_cursor() as cursor:
        reasons_text = _reasons_text(cursor, body.reasons)

        if body.is_modify:
            cursor.execute(
                "EXEC WebProc_ModifyEnquiryAction ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
                body.cust_id, body.ref_action_id, body.action_stage_id, _dmy(body.action_date),
                body.action_time, body.description, reasons_text, 1 if body.close_action else 0,
                current_user.user_id, body.quotation_amt,
            )
            return {"success": True}

        cursor.execute(
            "SELECT RefTransType FROM Web_EnquiryActionStage WHERE ActionStageID = ?", body.action_stage_id
        )
        trans_type_row = cursor.fetchone()
        trans_type = trans_type_row[0] if trans_type_row else 0

        if not body.close_action:
            # Positions 12-15 (ActionCompleted, CompletedBy, CompletionDate, CompletionTime) stay
            # literal 0 — matches the source's "not completed yet" sentinel; passing real
            # timestamps here would wrongly imply the action was already completed today.
            cursor.execute(
                "EXEC webProc_AddEnquiryAction ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,0,0,0, ?, 0, ?, 0, '', ?, ?, ?, ?, ?",
                body.cust_id, body.ref_action_id, body.action_stage_id, today, today, current_user.user_id,
                _dmy(body.action_date), body.action_time, body.description, body.assigned_to, body.remark,
                trans_type, body.sales_person_id, body.action_stage_status_id,
                body.competitor_id, body.special_rating, reasons_text, body.quotation_amt,
            )
        else:
            cursor.execute(
                "EXEC webProc_AddEnquiryAction ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?, ?",
                body.cust_id, body.ref_action_id, body.action_stage_id, today, today, current_user.user_id,
                _dmy(body.action_date), body.action_time, body.description, body.assigned_to, body.remark,
                body.assigned_to, _dmy(body.action_date), body.action_time, trans_type,
                body.sales_person_id, body.description, body.action_stage_status_id,
                body.competitor_id, body.special_rating, reasons_text, body.quotation_amt,
            )
    return {"success": True}


@router.post("/enquiry-action/close")
def close_enquiry_action(
    body: CloseEnquiryActionRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        reasons_text = _reasons_text(cursor, body.reasons)
        cursor.execute(
            "EXEC webProc_CloseEnquiryAction ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?",
            body.cust_id, body.ref_action_id, body.completed_by, _dmy(body.completed_date), body.completed_time,
            body.salesman_id, body.narration, body.action_stage_id,
            body.special_rating, reasons_text, body.quotation_amt,
        )

        if body.add_new_action:
            add = body.add_new_action
            add_reasons_text = _reasons_text(cursor, add.reasons)
            cursor.execute(
                "SELECT RefTransType FROM Web_EnquiryActionStage WHERE ActionStageID = ?", add.action_stage_id
            )
            trans_type_row = cursor.fetchone()
            trans_type = trans_type_row[0] if trans_type_row else 0
            today = datetime.now()
            cursor.execute(
                "EXEC webProc_AddEnquiryAction ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,0,0,0, ?, 0, ?, 0, '', ?, 0, '', ?, ?",
                body.cust_id, body.ref_action_id, add.action_stage_id, today, today, current_user.user_id,
                _dmy(add.action_date), add.action_time, add.description, add.assigned_to, add.remark,
                trans_type, add.sales_person_id, add.action_stage_status_id,
                add_reasons_text, add.quotation_amt,
            )
    return {"success": True}


@router.post("/followup")
def add_amc_followup(
    body: AmcFollowUpRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    today = datetime.now()
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webProc_AddEnquiryAction ?, 0, 18, ?, ?, ?, ?, ?, ?, 0, ' ', 1, ?, ?, ?, 0, 0, 0, 0, ?, 0, '', '', 0",
            body.cust_id, _dmy(today.date()), today.strftime("%H:%M:%S"), current_user.user_id,
            _dmy(body.follow_up_date), body.follow_up_time, body.contact_person,
            current_user.user_id, _dmy(today.date()), today.strftime("%H:%M:%S"), body.remark,
        )
    return {"success": True}


@router.get("/competitors", response_model=list[LookupOption])
def get_competitors() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ID, Name FROM Web_Compitator WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.get("/thanks-note-reasons", response_model=list[LookupOption])
def get_thanks_note_reasons() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ID, ReasonsForThankYouNote FROM web_ThanksNoteActionReasons WHERE Disabled = 0 "
            "ORDER BY ReasonsForThankYouNote"
        )
        return [LookupOption(label=r["ReasonsForThankYouNote"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.get("/special-ratings", response_model=list[str])
def get_special_ratings() -> list[str]:
    with get_cursor() as cursor:
        cursor.execute("SELECT MasterValue FROM Web_LittleMaster WHERE MasterName = 'SpecialRatingMaster'")
        return [r["MasterValue"] for r in rows_to_dicts(cursor)]


@router.get("/enquiry-action-context")
def get_enquiry_action_context(cust_id: int, ref_action_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ActionStageId, SalesmanId FROM Web_EnquiryAction WHERE CustID = ? AND ActionID = ?",
            cust_id, ref_action_id,
        )
        row = cursor.fetchone()
        action_stage_id = row[0] if row else 0
        salesman_id = row[1] if row else 0
        cursor.execute("SELECT Referance FROM CustomerMaster WHERE CustID = ?", cust_id)
        rating_row = cursor.fetchone()
        special_rating = rating_row[0] if rating_row and rating_row[0] else ""
    return {"action_stage_id": action_stage_id or 0, "salesman_id": salesman_id or 0, "special_rating": special_rating}


@router.get("/enquiry-action-for-modify")
def get_enquiry_action_for_modify(cust_id: int, ref_action_id: int) -> dict | None:
    # webproc_showenquiryaction's 2nd param is @ShowLastStage BIT — the source passes
    # RefActionID directly into it (any nonzero value coerces to True), so this has
    # always fetched the customer's single latest action rather than filtering by
    # RefActionID specifically. Replicating that actual behavior rather than a
    # "corrected" WHERE-ActionID filter the proc doesn't support.
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_showenquiryaction ?, 1", cust_id)
        rows = rows_to_dicts(cursor)
    if not rows:
        return None
    r = rows[0]
    return {
        "quotation_amt": float(r.get("QuotationAmt") or 0),
        "action_description": r.get("ActionDescription") or "",
        "action_remark": r.get("ActionRemark") or "",
        "action_date": r["ActionDate"].isoformat() if r.get("ActionDate") else "",
        "action_time": str(r.get("ActionTime") or ""),
        "completed_remark": r.get("CompletedRemark") or "",
        "completion_date": r["CompletionDate"].isoformat() if r.get("CompletionDate") else "",
        "completion_time": str(r.get("CompletionTime") or ""),
    }


@router.get("/action-history", response_model=list[AmcActionRow])
def get_action_history(cust_id: int) -> list[AmcActionRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_showenquiryaction ?, 0", cust_id)
        rows = rows_to_dicts(cursor)

    return [
        AmcActionRow(
            action_id=r["ActionId"], creation_date=r["CreationDate"].isoformat() if r["CreationDate"] else None,
            creation_time=str(r["CreationTime"] or ""), follow_up_by=r["CreatedBy"] or "",
            follow_up_remark=r["ActionRemark"] or "",
            completion_date=r["CompletionDate"].isoformat() if r["CompletionDate"] else "",
            completion_time=str(r["CompletionTime"] or ""), completed_by=r["Completedby"] or "",
            assigned_to=r["AssignedTo"] or "", action_description=r["ActionDescription"] or "",
            action_remark=r["ActionRemark"] or "", next_action=r["NextAction"] or "",
        )
        for r in rows
    ]
