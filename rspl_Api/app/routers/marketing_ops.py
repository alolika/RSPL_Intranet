"""Mirrors Section_Marketing/CustTeleCallingRegister.aspx.vb,
OutstandingFollowup.aspx.vb, and EnquiryDownloaded.aspx.vb.

`webproc_ShowTeleCallEnquiry` selects a friendly `UM.Name as ReceivedBy`
column near the top of its result set, then also does `E.*` later in the
same SELECT — `Web_Enquiry` has its own raw `ReceivedBy` (a UserID) column,
so the result set has two columns both literally named `ReceivedBy` (SQL
Server permits this). The established `rows_to_dicts()` (`dict(zip(columns,
row))`) keeps the *last* value for a repeated key — which here would be the
raw numeric ID, not the display name the source's DataTable access actually
reads (ADO.NET keeps the *first* occurrence under the plain name, renaming
the second to `ReceivedBy1`). Used a first-occurrence dict builder here
instead of the shared last-wins helper to get this right.
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing/ops", tags=["marketing-ops"])


class LookupOption(BaseModel):
    label: str
    value: int


class TeleCallingRow(BaseModel):
    cust_id: int
    next_action: str
    ref_action_id: int
    mobile_no: str
    ph_no: str
    enquiry_date: str | None
    shop_name: str
    contact_person: str
    city: str
    received_by: str
    narration: str
    segment_name: str
    business_nature_name: str
    source_type_name: str
    introduced_by_name: str
    communication_method_name: str
    referred_by_name: str


class TeleCallingActionRow(BaseModel):
    action_id: int
    action_stage: str
    action_stage_status: str
    creation_date: str | None
    creation_time: str
    created_by: str
    action_date: str | None
    action_time: str
    assigned_to: str
    action_description: str
    action_remark: str
    completion_date: str | None
    completion_time: str
    completed_by: str
    completed_remark: str
    sales_person: str
    voucher_no: str
    to_do_voucher_no: str


class OutstandingFollowupRow(BaseModel):
    cust_id: int
    sales_man: str
    display_name: str
    city: str
    mobile_no: str
    ledger_balance: float
    ledger_group_balance: float
    amc_amount: float
    amc_due_date: str | None


class DownloadedEnquiryRow(BaseModel):
    cust_id: int
    customer_name: str
    city: str
    narration: str
    enquiry_date: str | None
    assigned_user_id: int
    assigned_to: str
    segment_name: str
    business_nature_name: str


def _first_occurrence_dicts(cursor) -> list[dict]:
    while cursor.description is None:
        if not cursor.nextset():
            return []
    columns = [c[0] for c in cursor.description]
    rows = []
    for row in cursor.fetchall():
        d: dict = {}
        for name, value in zip(columns, row):
            if name not in d:
                d[name] = value
        rows.append(d)
    return rows


# -------------------- CustTeleCallingRegister --------------------


@router.get("/telecalling/filters")
def get_telecalling_filters() -> dict:
    with get_cursor() as cursor:
        cursor.execute("SELECT GroupID, Name FROM CustomerGroupMaster WHERE Disabled = 0 ORDER BY Name")
        groups = [{"label": r["Name"], "value": r["GroupID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "SELECT ID ActionStageStatusID, MasterValue Name FROM Web_LittleMaster WHERE MasterName = 'TelecallStatusMaster'"
        )
        stages = [{"label": r["Name"], "value": r["ActionStageStatusID"]} for r in rows_to_dicts(cursor)]
    return {"groups": groups, "stages": stages}


@router.get("/telecalling", response_model=list[TeleCallingRow])
def get_telecalling_rows(group_id: int = 0, action_stage_status_id: int = 0) -> list[TeleCallingRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_ShowTeleCallEnquiry ?, ?", group_id, action_stage_status_id)
        rows = _first_occurrence_dicts(cursor)
    return [
        TeleCallingRow(
            cust_id=r["CustID"], next_action=r["NextAction"] or "", ref_action_id=r["RefActionID"] or 0,
            mobile_no=r["MobileNo"] or "", ph_no=r["PhNo"] or "",
            enquiry_date=r["EnquiryDate"].isoformat() if r["EnquiryDate"] else None,
            shop_name=r["ShopName"] or "", contact_person=r["ContactPerson"] or "", city=r["City"] or "",
            received_by=r["ReceivedBy"] or "", narration=r["Narration"] or "", segment_name=r["SegmentName"] or "",
            business_nature_name=r["BusinessNatureName"] or "", source_type_name=r["SourceTypeName"] or "",
            introduced_by_name=r["IntroducedByName"] or "", communication_method_name=r["CommunicationMethodName"] or "",
            referred_by_name=r["ReferredByName"] or "",
        )
        for r in rows
    ]


@router.get("/telecalling/{cust_id}/actions", response_model=list[TeleCallingActionRow])
def get_telecalling_actions(cust_id: int) -> list[TeleCallingActionRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_ShowTeleCallEnquiryAction ?", cust_id)
        rows = rows_to_dicts(cursor)
    return [
        TeleCallingActionRow(
            action_id=r["ActionId"], action_stage=r.get("ActionStage") or "",
            action_stage_status=r.get("ActionStageStatus") or "",
            creation_date=r["CreationDate"].isoformat() if r.get("CreationDate") else None,
            creation_time=str(r.get("CreationTime") or ""), created_by=r.get("CreatedBy") or "",
            action_date=r["ActionDate"].isoformat() if r.get("ActionDate") else None,
            action_time=str(r.get("ActionTime") or ""), assigned_to=r.get("AssignedTo") or "",
            action_description=r.get("ActionDescription") or "", action_remark=r.get("ActionRemark") or "",
            completion_date=r["CompletionDate"].isoformat() if r.get("CompletionDate") else None,
            completion_time=str(r.get("CompletionTime") or ""), completed_by=r.get("Completedby") or "",
            completed_remark=r.get("CompletedRemark") or "", sales_person=r.get("SalesPerson") or "",
            voucher_no=str(r.get("VoucherNo") or ""), to_do_voucher_no=str(r.get("ToDoVoucherNo") or ""),
        )
        for r in rows
    ]


# -------------------- OutstandingFollowup --------------------


@router.get("/outstanding-followup/filters")
def get_outstanding_followup_filters() -> dict:
    # Customer alone was an unfiltered `SELECT DisplayName ... WHERE disabled=0
    # AND groupid=1` — 5832 rows, ~0.6s just for the query, loaded into a plain
    # <p-select> on every page open. Capped to top 100 here like every other
    # lookup in the app; re-searched by name as the user types via
    # /outstanding-followup/customers below.
    with get_cursor() as cursor:
        cursor.execute("SELECT DISTINCT vm.SalesPerson FROM vw_CustomerTINFollowup vm ORDER BY vm.SalesPerson")
        salesmen = [r["SalesPerson"] for r in rows_to_dicts(cursor)]
        cursor.execute(
            "SELECT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
            "WHERE AttributeID=1) AND disabled=0 AND groupid=1 ORDER BY DisplayName"
        )
        customers = [r["DisplayName"] for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT LedgerGroupID, Name FROM LedgerGroupMaster ORDER BY name")
        ledger_groups = [{"label": r["Name"], "value": r["LedgerGroupID"]} for r in rows_to_dicts(cursor)]
    return {"salesmen": salesmen, "customers": customers, "ledgerGroups": ledger_groups}


@router.get("/outstanding-followup/customers", response_model=list[str])
def get_outstanding_followup_customers(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
                "WHERE AttributeID=1) AND disabled=0 AND groupid=1 AND DisplayName LIKE ? ORDER BY DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
                "WHERE AttributeID=1) AND disabled=0 AND groupid=1 ORDER BY DisplayName"
            )
        return [r["DisplayName"] for r in rows_to_dicts(cursor)]


@router.get("/outstanding-followup", response_model=list[OutstandingFollowupRow])
def get_outstanding_followup_rows(
    salesman: str = "", amount: float | None = None, customer: str = "", ledger_group_id: int = 0,
) -> list[OutstandingFollowupRow]:
    if amount is None or amount < 1:
        return []
    where = ""
    params: list = []
    if salesman:
        where += " and A.AttrValue LIKE ?"
        params.append(f"%{salesman}%")
    where += " and c.Ledgerbalance >= ?"
    params.append(amount)
    if customer:
        where += " and DisplayName = ?"
        params.append(customer)
    if ledger_group_id:
        where += " and LedgerGroupId = ?"
        params.append(ledger_group_id)
    sql = (
        "select A.AttrValue SalesMan, C.Custid, C.Displayname, C.Mobileno, C.City, C.Ledgerbalance, C.AMCAmount, "
        "C.AMCDueDate, (Select Sum(Ledgerbalance) from VWAMCForAllCustomers v Where v.Ledgergroupid=c.Ledgergroupid "
        "and v.Ledgergroupid>0) LedgerGroupBalance "
        "from VWAMCForAllCustomers C inner join CustomerAttributes A On A.DepID=1 and A.AttributeID=31 and A.CustID=C.Custid "
        f"Where Disabled=0{where} Order By Displayname, Ledgerbalance desc"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        OutstandingFollowupRow(
            cust_id=r["Custid"], sales_man=r["SalesMan"] or "", display_name=r["Displayname"] or "",
            city=r["City"] or "", mobile_no=r["Mobileno"] or "", ledger_balance=float(r["Ledgerbalance"] or 0),
            ledger_group_balance=float(r["LedgerGroupBalance"] or 0), amc_amount=float(r["AMCAmount"] or 0),
            amc_due_date=r["AMCDueDate"].isoformat() if r["AMCDueDate"] else None,
        )
        for r in rows
    ]


@router.get("/outstanding-followup/{cust_id}/modules")
def get_outstanding_customer_modules(cust_id: int) -> list[dict]:
    with get_cursor() as cursor:
        cursor.execute(
            "select * from RSPL_CustSoftwareProductDetail where CustId = ? order by date", cust_id
        )
        return rows_to_dicts(cursor)


# -------------------- EnquiryDownloaded --------------------


@router.get("/downloaded-enquiry", response_model=list[DownloadedEnquiryRow])
def get_downloaded_enquiry_rows(from_date: date, to_date: date) -> list[DownloadedEnquiryRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC WebProc_GetDownLoadEnquiry ?, ?", from_date, to_date)
        rows = rows_to_dicts(cursor)
    return [
        DownloadedEnquiryRow(
            cust_id=r["CustID"], customer_name=r["Customer Name"] or "", city=r["City"] or "",
            narration=r["Narration"] or "",
            enquiry_date=r["Enquiry Date"].isoformat() if r.get("Enquiry Date") else None,
            assigned_user_id=r["AssignedUserID"] or 0, assigned_to=r["Assigned To"] or "",
            segment_name=r["Segment Name"] or "", business_nature_name=r["Business Nature Name"] or "",
        )
        for r in rows
    ]


@router.post("/downloaded-enquiry/{cust_id}/reassign")
def reassign_downloaded_enquiry(cust_id: int, existing_assigned: int, new_assigned_to: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_UpdateAssignedToDownloadEnq ?, ?, ?", cust_id, existing_assigned, new_assigned_to
        )
        rows = rows_to_dicts(cursor)
    return {"success": bool(rows)}
