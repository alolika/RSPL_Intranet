"""Mirrors Section_Support/Modules.aspx.vb, EinvoiceStatus.aspx.vb,
CustDependent.aspx.vb, PayBank.aspx.vb, VideoHistory.aspx.vb.

NOTE: Modules.aspx's "ExeVersion" shape (license_WebProc_CheckSoftwareVersions),
the "Show Pirated" action (fun_Web_CustomerLicensePiracy ->
vw_Web_CustomerLicensePiracy), AND EinvoiceStatus's vw_web_Einvoice all read
from [License].[license_Retailware] via the same missing linked server as
Section_GST — written correctly but untestable/500s, same as that section.
Confirmed live: every other shape in this router works against real data;
only these three depend on the missing linked server.

NOTE: VideoHistory.aspx.vb's Page_Load does a cross-database sync from a
separate License-server connection (clsDBLayer.constr_Licence) before
rendering — that sync step is not replicated here (no such connection
available in this stack); only the read-only grid/customer-list queries
against the local VideoTrainingModeTrack table are wired.

NOTE: EinvoiceStatus.aspx.vb's FillGV filters with `Where C.Custid=...` but
vw_web_Einvoice is queried with no "C" alias anywhere in its FROM clause —
a latent bug in the source that would error if a customer were ever
selected. Fixed here to filter on the view's actual `Custid` column.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/support", tags=["support-modules"])


class LookupOption(BaseModel):
    label: str
    value: int


class ModulesData(BaseModel):
    title: str
    rows: list[dict]


class CustomerDependent(BaseModel):
    name: str
    mobile_no: str
    email: str


class AddCustomerDependentRequest(BaseModel):
    cust_id: int
    name: str
    mobile_no: str
    email: str


class AddCustomerDependentResponse(BaseModel):
    success: bool
    message: str


class EinvoiceStatusRow(BaseModel):
    cust_id: int
    customer_name: str
    date: str | None
    response: str


class BankCardsResponse(BaseModel):
    options: list[LookupOption]
    checked: list[int]


class SaveCustBankCardsRequest(BaseModel):
    cust_id: int
    checked_values: list[int]


class VideoHistoryRow(BaseModel):
    menu_id: int
    sr_no: int
    menu_name: str
    video_description: str
    client: str
    date: str | None
    video_link_url: str
    video_training_mode_status: str
    user_name: str
    counter_name: str


def _row(cursor) -> list[dict]:
    return rows_to_dicts(cursor)


@router.get("/modules-data", response_model=ModulesData)
def get_modules_data(field_name: str, cust_id: int) -> ModulesData:
    with get_cursor() as cursor:
        if field_name == "Modules":
            cursor.execute(
                """
                SELECT ProductID, ProductName, SUM(Qty) Qty FROM (
                  SELECT sd.ProductID, P.ProductName, sm.Date, SUM(Qty) Qty FROM Salesmaster sm
                    INNER JOIN Salesdetail sd ON sm.Voucherno = sd.voucherno
                    INNER JOIN Products P ON P.Productid = Sd.Productid
                    WHERE sd.ProductID NOT IN (490,496,511,510) AND sm.custid = ?
                    GROUP BY sd.ProductID, P.ProductName, sm.Date
                  UNION
                  SELECT sd.ProductID, P.ProductName, sm.Date, -SUM(Quantity) Qty FROM SalesReturnmaster sm
                    INNER JOIN SalesReturndetail sd ON sm.Voucherno = sd.voucherno
                    INNER JOIN Products P ON P.Productid = Sd.Productid
                    WHERE sd.ProductID NOT IN (490,496,511,510) AND sm.custid = ?
                    GROUP BY sd.ProductID, P.ProductName, sm.Date
                ) Tbl GROUP BY ProductID, ProductName HAVING SUM(Qty) <> 0 ORDER BY ProductName
                """,
                cust_id, cust_id,
            )
            rows = _row(cursor)
            return ModulesData(title="Total Modules", rows=[{"ProductID": r["ProductID"], "ProductName": r["ProductName"], "Qty": r["Qty"]} for r in rows])

        if field_name == "Invoices":
            cursor.execute(
                """
                SELECT sm.Voucherno AS VoucherNo, sm.Date,
                       ISNULL((SELECT value FROM udfdetail WHERE udfid=1 AND voucherno=sm.voucherno AND TransTypeID=1), 0) AS AssessableValue,
                       sm.netamount AS NetAmt, sm.Balance AS OutstandingAmt
                FROM salesmaster sm WHERE custid = ? ORDER BY date DESC
                """,
                cust_id,
            )
            rows = _row(cursor)
            return ModulesData(
                title="Total Invoices",
                rows=[
                    {
                        "VoucherNo": r["VoucherNo"], "Date": r["Date"].isoformat() if r["Date"] else None,
                        "Bill Value": float(r.get("AssessableValue") or 0), "Net Amt": float(r["NetAmt"] or 0),
                        "Outstanding Amt": float(r["OutstandingAmt"] or 0),
                    }
                    for r in rows
                ],
            )

        if field_name == "VisitLog":
            cursor.execute(
                """
                SELECT Vm.Date, Vm.RefNo,
                       CASE WHEN P.DisplayName = '' THEN P.ProductName ELSE P.DisplayName END AS ProductName,
                       ISNULL(CauseMast.Name, '') AS Cause, Vm.SupportReason
                FROM VisitLogMaster Vm
                INNER JOIN Products P ON Vm.Productid = P.Productid
                LEFT OUTER JOIN Custdependents CustDept ON CustDept.CustId = Vm.Clientid
                LEFT OUTER JOIN CauseMaster CauseMast ON CauseMast.CauseId = Vm.CauseId
                WHERE CustDept.DepID = 1 AND CustDept.CustId = ?
                ORDER BY Vm.Date, Vm.Logid
                """,
                cust_id,
            )
            rows = _row(cursor)
            return ModulesData(
                title="Total VisitLog",
                rows=[
                    {
                        "Date": r["Date"].isoformat() if r["Date"] else None, "RefNo": r["RefNo"],
                        "ProductName": r["ProductName"], "Cause": r["Cause"], "SupportReason": r["SupportReason"],
                    }
                    for r in rows
                ],
            )

        if field_name == "AMCDate":
            cursor.execute("SELECT * FROM dbo.Fun_GetCustomerAMCDtl(?) AmcAmount", cust_id)
            rows = _row(cursor)
            return ModulesData(
                title="Amc Info",
                rows=[
                    {
                        "Cust ID": cust_id, "Assessable Value": float(r.get("Assessablevalue") or 0),
                        "AMC Per": float(r.get("AMCPer") or 0), "BasicAmt": float(r.get("BasicAmt") or 0),
                        "Net Amc": float(r.get("NetAmc") or 0), "AMC Value": float(r.get("AMCValue") or 0),
                        "AMC DueDate": r["AMCDueDate"].isoformat() if r.get("AMCDueDate") else None,
                    }
                    for r in rows
                ],
            )

        if field_name == "ViewAction":
            cursor.execute("EXEC webproc_GetViewAction ?, 0", cust_id)
            rows = _row(cursor)
            return ModulesData(
                title="View Action",
                rows=[
                    {
                        "ActionDate": r["ActionDate"].isoformat() if r["ActionDate"] else None,
                        "ActionBy": r["Action By"], "ActionDescription": r["Issue"], "ActionRemark": r["ActionDetails"],
                    }
                    for r in rows
                ],
            )

        if field_name == "Notification":
            cursor.execute("EXEC webproc_GetNotificationList ?", cust_id)
            rows = _row(cursor)
            while cursor.nextset():
                rows += _row(cursor)
            return ModulesData(
                title="View Notification",
                rows=[
                    {
                        "Mobileno": r.get("Mobileno") or "", "Date": r["Date"].isoformat() if isinstance(r.get("Date"), datetime) else "",
                        "ReadTime": str(r.get("ReadTime") or ""), "ReadCount": r.get("ReadCount") or 0,
                    }
                    for r in rows
                ],
            )

        # ExeVersion: blocked by missing [License] linked server, see module docstring.
        cursor.execute("EXEC license_WebProc_CheckSoftwareVersions ?, 1, ''", cust_id)
        rows = _row(cursor)
        return ModulesData(title="View Action", rows=rows)


@router.get("/pirated-modules")
def get_pirated_modules(cust_id: int) -> list[dict]:
    """Blocked by missing [License] linked server, see module docstring."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ProductID, '**PIRATED-' + ProductName AS ProductName, PiratedQty FROM dbo.fun_Web_CustomerLicensePiracy(?)",
            cust_id,
        )
        return _row(cursor)


# --- Mirrors CustDependent.aspx.vb ---


@router.get("/customer-dependents", response_model=list[CustomerDependent])
def get_customer_dependents(cust_id: int) -> list[CustomerDependent]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webProc_GetCustDepdetails ?", cust_id)
        rows = _row(cursor)
    return [
        CustomerDependent(name=r["Name"] or "", mobile_no=r["DepMobileno"] or "", email=r["DepEmail"] or "")
        for r in rows
    ]


# CustDependent.aspx itself never had an Add form (confirmed against the
# source — it's a read-only RadGrid + Close button). The only place the
# legacy app actually inserts a new CustDependents row is buried inside
# WebProc_UpdateSIPEventRegisterCDR (ClosePopup.aspx's "Add Dependent"
# checkbox, unrelated to Trouble Ticket) — confirmed via its definition:
# duplicate-mobile check, then DepID = MAX(DepID)+1 per customer, then an
# INSERT that never sets DepEmail (defaults to ''). Reused that exact same
# duplicate-check/DepID-assignment logic here (rather than inventing a new
# scheme) since it's the one proven-working precedent in this schema, but
# extended to also set DepEmail — this add form is a genuinely new feature
# on Trouble Ticket's Show Dependent popup, not a restoration, so there's no
# existing behavior to stay bug-for-bug faithful to on the email field.
@router.post("/customer-dependents", response_model=AddCustomerDependentResponse)
def add_customer_dependent(req: AddCustomerDependentRequest, current_user: CurrentUser = Depends(get_current_user)) -> AddCustomerDependentResponse:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 1 1 FROM CustDependents WHERE CustID = ? AND DepMobileNo = ?",
            req.cust_id, req.mobile_no,
        )
        if cursor.fetchone():
            return AddCustomerDependentResponse(success=False, message="Mobile no already exists for this customer.")

        cursor.execute("SELECT ISNULL(MAX(DepID), 0) + 1 FROM CustDependents WHERE CustID = ?", req.cust_id)
        dep_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO CustDependents (CustID, DepID, Name, CreationDate, CreationTime, ModifyDate, ModifyTime, DepMobileNo, DepEmail, Userid)
            VALUES (?, ?, ?, dbo.GetdateIndia(), dbo.GettimeIndia(), dbo.GetdateIndia(), dbo.GettimeIndia(), ?, ?, ?)
            """,
            req.cust_id, dep_id, req.name, req.mobile_no, req.email, current_user.user_id,
        )
    return AddCustomerDependentResponse(success=True, message="Dependent added successfully.")


# --- Mirrors EinvoiceStatus.aspx.vb ---


@router.get("/einvoice-status", response_model=list[EinvoiceStatusRow])
def get_einvoice_status(cust_id: int = 0) -> list[EinvoiceStatusRow]:
    query = "SELECT * FROM vw_web_Einvoice"
    params: list = []
    if cust_id:
        query += " WHERE Custid = ?"
        params.append(cust_id)

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = _row(cursor)

    return [
        EinvoiceStatusRow(
            cust_id=r["Custid"], customer_name=r["CustomerName"] or "",
            date=r["Date"].isoformat() if r["Date"] else None, response=r["Response"] or "",
        )
        for r in rows
    ]


# --- Mirrors PayBank.aspx.vb ---


@router.get("/bank-cards", response_model=BankCardsResponse)
def get_bank_cards(cust_id: int) -> BankCardsResponse:
    with get_cursor() as cursor:
        cursor.execute("EXEC webProc_GetPayBankDetails 1, 0")
        options = [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in _row(cursor)]

        cursor.execute("EXEC webProc_GetPayBankDetails 0, ?", cust_id)
        checked_items = {r["Item"].strip().upper() for r in _row(cursor)}

    checked = [o.value for o in options if o.label.strip().upper() in checked_items]
    return BankCardsResponse(options=options, checked=checked)


@router.post("/bank-cards")
def add_bank_card(name: str) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_InsertPayBankMaster ?", name)
        rows = _row(cursor)
    return {"success": bool(rows) and str(rows[0].get("ResultCode")) == "100"}


@router.post("/bank-cards/apply")
def save_cust_bank_cards(
    body: SaveCustBankCardsRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, object]:
    with get_cursor() as cursor:
        cursor.execute("SELECT MasterValue FROM Web_LittleMaster WHERE MasterName = 'PaymentBankMaster' AND ID IN ({})".format(
            ",".join("?" for _ in body.checked_values) or "NULL"
        ), *body.checked_values)
        names = [r["MasterValue"] for r in _row(cursor)]
        checked_text = ",".join(names)

        cursor.execute(
            "EXEC Proc_AddModifyCustAttribute ?, 33, ?, ?, 0",
            body.cust_id, checked_text, current_user.user_id,
        )
        rows = _row(cursor)

    if not rows:
        return {"success": False, "message": "Failed to apply bank cards to customer"}
    return {"success": True, "message": rows[0].get("ResultMessage", "")}


# --- Mirrors VideoHistory.aspx.vb ---
#
# The customer picker previously mirrored the source's own query (customers
# who already have VideoTrainingModeTrack rows, via an inner join) — but
# that meant the ONLY customer ever selectable was whichever ones happened
# to already have history, making it impossible to search for or check a
# customer who has none yet. Switched to the same general-purpose Top-100/
# search picker used by TroubleTicket (get_customers in support.py) so any
# customer can be searched and selected; get_video_history below naturally
# returns an empty list for a customer with no training history, which is
# the correct, expected result rather than an error.


@router.get("/video-history", response_model=list[VideoHistoryRow])
def get_video_history(cust_id: int) -> list[VideoHistoryRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT VTM.MenuID, VTM.SrNo, VTM.MenuName, VTM.VideoDescription, C.Name AS Client, VTM.Date,
                   VTM.VideoLinkURL, VMS.Name AS VideoTrainingModeStatusName, VTM.UserName, VTM.CounterName
            FROM VideoTrainingModeTrack VTM
            INNER JOIN CustomerMaster C ON C.CustID = VTM.ClientID
            INNER JOIN VideoTrainingModeStatusMaster VMS ON VMS.VideoTrainingModeStatusId = VTM.VideoTrainingModeStatusID
            WHERE VTM.ClientID = ?
            """,
            cust_id,
        )
        rows = _row(cursor)

    return [
        VideoHistoryRow(
            menu_id=r["MenuID"], sr_no=r["SrNo"], menu_name=r["MenuName"] or "",
            video_description=r["VideoDescription"] or "", client=r["Client"] or "",
            date=r["Date"].isoformat() if r["Date"] else None, video_link_url=r["VideoLinkURL"] or "",
            video_training_mode_status=r["VideoTrainingModeStatusName"] or "",
            user_name=r["UserName"] or "", counter_name=r["CounterName"] or "",
        )
        for r in rows
    ]


# -------------------- SupportCoOrdinatorDashboard --------------------
# Part of the report-engine follow-up task (see feedback_report_engine_simplification
# memory). Source (createGrid() in SupportCoOrdinatorDashboard.aspx.vb) reads
# RSPL_SupportCoOrdinatorDashboard (Report_ID/Report_Title/Report_Query,
# WHERE Report_Enabled=1) and runs each row's Report_Query as a tile; despite
# the column name, every live Report_Query value is a bare parameterless
# stored-proc name (e.g. RSPL_SupportDashborad_RepeatCalls), not a raw SQL
# string, so each tile is executed via EXEC. Confirmed live: 3 of 8 tiles
# work, 4 depend on the missing `synapsecdr` database (same gap as
# Section_Support's Call/SIP family — CallHistory/MissedCall/etc.), and one
# (CustomerwiseMissedCall) has an unrelated pre-existing bug inside the proc
# body itself ("insufficient number of arguments" despite taking none) —
# each tile's EXEC is wrapped individually so one failing/blocked tile
# doesn't take down the rest of the dashboard, matching the source's own
# empty Catch-and-continue behavior.


class DashboardTile(BaseModel):
    report_id: int
    title: str
    rows: list[dict]


@router.get("/coordinator-dashboard", response_model=list[DashboardTile])
def get_support_coordinator_reports() -> list[DashboardTile]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Report_ID, Report_Title, Report_Query FROM RSPL_SupportCoOrdinatorDashboard WHERE Report_Enabled = 1"
        )
        tiles = rows_to_dicts(cursor)

    result = []
    for t in tiles:
        proc = (t.get("Report_Query") or "").strip()
        rows: list[dict] = []
        if proc:
            try:
                with get_cursor() as cursor:
                    cursor.execute(f"EXEC {proc}")
                    rows = rows_to_dicts(cursor)
            except Exception:
                rows = []
        result.append(DashboardTile(report_id=t["Report_ID"], title=t["Report_Title"] or "", rows=rows))
    return result
