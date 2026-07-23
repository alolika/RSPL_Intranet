"""Mirrors Section_Admin/CustLedger.aspx.vb ("Customer Module Report"),
CustomerInfo.aspx.vb (read-only popup), CustTINFollowup.aspx.vb,
CustomerAns.aspx.vb, CustomerGracePeriod.aspx.vb, and
CustomerLicenseViolation.aspx.vb.

Three of these six pages are blocked by missing cross-server dependencies
on this SQL instance (confirmed via live errors, not guesswork) — written
correctly against the source's referenced schema/proc signatures anyway,
per the established project pattern (see GST/Support-Modules routers):
  - CustomerAns: vw_web_clientAnswerSheet needs linked server
    'sharedmssql5.znetindia.net,1234' (not configured here).
  - CustomerGracePeriod: vw_web_AMCGracePeriodCustomer needs the same
    'sharedmssql5.znetindia.net,1234' linked server, AND its join to
    vw_web_softwaremaster needs the already-known-missing '[License]'
    linked server (see gst.py).
  - CustomerLicenseViolation: both WebProc_GetCustVoilationDetails and
    webproc_GetDrillDownCustLicenceVoilation read from
    vw_Web_CustomerLicensePiracy, which also needs '[License]'. Note also
    that WebProc_GetCustVoilationDetails runs a cursor loop that calls
    EXEC proc_CustProductDebitCredit as a side effect (posts ledger
    entries) even though the source only ever calls it from a "read" grid
    refresh — not executed live here even once the linked server exists,
    to avoid an accidental real financial posting; review that proc's
    behavior with the user before ever calling this endpoint against
    production data.
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/admin/customers", tags=["admin-customers"])


class LookupOption(BaseModel):
    label: str
    value: int


class CustLedgerRow(BaseModel):
    customer_id: int
    customer_name: str
    area: str
    city_name: str
    email: str
    mobile_no: str
    amc: str | None
    yt_date: str | None
    yt_status: str
    yt_remark: str
    exe_status: str
    yt_user: str


class CustLedgerFilters(BaseModel):
    software: list[LookupOption]
    yt_statuses: list[LookupOption]
    cities: list[str]
    areas: list[str]
    groups: list[LookupOption]
    customers: list[str]


class CustLedgerSaleRow(BaseModel):
    customer_id: int
    voucher_no: int
    date: str
    expiry: str | None
    sr_no: int
    product_name: str
    qty: float
    rate: float
    amount: float


class UpdateYtInfoRequest(BaseModel):
    yt_status: str
    yt_date: date | None
    yt_remark: str
    exe_status: str


class CustomerInfoRow(BaseModel):
    cust_id: int
    customer_name: str
    group_name: str
    address: str
    contact_person: str
    mobile_no: str
    phone_no: str
    email: str


class CustTinFollowupRow(BaseModel):
    cust_id: int
    software: str
    display_name: str
    class_name: str
    amc_expiry: str | None
    sales_person: str
    mobile_no: str
    email: str
    tin_no: str
    followup_remark: str


class UpdateCustTinRequest(BaseModel):
    tin_no: str
    email: str
    followup_remark: str


class LicenseViolationRow(BaseModel):
    cust_id: int
    display_name: str
    city: str
    amc_due_date: str | None
    amc_amount: float
    ledger_balance: float
    pirated_qty: int
    piracy_amt: float
    first_bill: str | None
    first_salesman: str


class LicenseModuleRow(BaseModel):
    pirated: bool
    product_id: int
    product_name: str
    active_qty: float
    billed_qty: float
    pirated_qty: float
    piracy_amt: float
    answer_time: str


class LicenseActionRow(BaseModel):
    action_id: int
    creation_date: str | None
    creation_time: str
    follow_up_by: str
    follow_up_remark: str
    assigned_to: str
    action_description: str
    action_remark: str
    next_action: str


# -------------------- CustLedger --------------------


@router.get("/cust-ledger/filters", response_model=CustLedgerFilters)
def get_cust_ledger_filters() -> CustLedgerFilters:
    # City (3039 rows)/Area (8313 rows)/Customer (previously TOP 1000) were all
    # loaded unfiltered/near-unfiltered on every page open with no search at
    # all. Capped to top 100 here like every other lookup in the app;
    # re-searched as the user types via the dedicated endpoints below.
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT AttrValue FROM customerattributes WHERE AttributeID = 1 AND AttrValue <> '' ORDER BY AttrValue"
        )
        software = [LookupOption(label=r["AttrValue"], value=i + 1) for i, r in enumerate(rows_to_dicts(cursor))]

        cursor.execute("SELECT YTStatusID, YTStatus FROM YTStatus ORDER BY YTStatus")
        yt_statuses = [LookupOption(label=r["YTStatus"], value=r["YTStatusID"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT TOP 100 City FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
            "WHERE AttributeID = 1) AND disabled = 0 AND City <> '' ORDER BY City"
        )
        cities = [r["City"] for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT TOP 100 Area FROM CustomerMaster WHERE Area <> '' AND Custid IN (SELECT Custid FROM "
            "CustomerAttributes WHERE AttributeID = 1) AND Disabled = 0 ORDER BY Area"
        )
        areas = [r["Area"] for r in rows_to_dicts(cursor)]

        # LedgerGroupMaster lists every group ever defined (802 rows) — most
        # (366 of them) have ZERO AttributeID=1 customers actually assigned to
        # them (customermaster.LedgerGroupId is 0/unset for 65,452 of 68,654
        # customers in this set), so picking most groups from an unfiltered
        # list silently returned an empty grid. Joined against real customers
        # here, like City/Area/Customer already are, so every listed group is
        # guaranteed to have at least one match.
        cursor.execute(
            "SELECT DISTINCT TOP 100 LGM.LedgerGroupID, LGM.Name FROM LedgerGroupMaster LGM "
            "INNER JOIN CustomerMaster CM ON CM.LedgerGroupId = LGM.LedgerGroupID "
            "WHERE CM.custid IN (SELECT custid FROM customerattributes WHERE AttributeID = 1) AND CM.disabled = 0 "
            "ORDER BY LGM.Name"
        )
        groups = [LookupOption(label=r["Name"], value=r["LedgerGroupID"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM "
            "customerattributes WHERE AttributeID = 1) AND disabled = 0 AND groupid = 1 ORDER BY DisplayName"
        )
        customers = [r["DisplayName"] or "" for r in rows_to_dicts(cursor)]

    return CustLedgerFilters(software=software, yt_statuses=yt_statuses, cities=cities, areas=areas, groups=groups, customers=customers)


@router.get("/cust-ledger/cities", response_model=list[str])
def get_cust_ledger_cities(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT DISTINCT TOP 100 City FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
                "WHERE AttributeID = 1) AND disabled = 0 AND City <> '' AND City LIKE ? ORDER BY City",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT DISTINCT TOP 100 City FROM customermaster WHERE custid IN (SELECT custid FROM customerattributes "
                "WHERE AttributeID = 1) AND disabled = 0 AND City <> '' ORDER BY City"
            )
        return [r["City"] for r in rows_to_dicts(cursor)]


@router.get("/cust-ledger/areas", response_model=list[str])
def get_cust_ledger_areas(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT DISTINCT TOP 100 Area FROM CustomerMaster WHERE Area <> '' AND Custid IN (SELECT Custid FROM "
                "CustomerAttributes WHERE AttributeID = 1) AND Disabled = 0 AND Area LIKE ? ORDER BY Area",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT DISTINCT TOP 100 Area FROM CustomerMaster WHERE Area <> '' AND Custid IN (SELECT Custid FROM "
                "CustomerAttributes WHERE AttributeID = 1) AND Disabled = 0 ORDER BY Area"
            )
        return [r["Area"] for r in rows_to_dicts(cursor)]


@router.get("/cust-ledger/ledger-groups", response_model=list[LookupOption])
def get_cust_ledger_ledger_groups(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT DISTINCT TOP 100 LGM.LedgerGroupID, LGM.Name FROM LedgerGroupMaster LGM "
                "INNER JOIN CustomerMaster CM ON CM.LedgerGroupId = LGM.LedgerGroupID "
                "WHERE CM.custid IN (SELECT custid FROM customerattributes WHERE AttributeID = 1) AND CM.disabled = 0 "
                "AND LGM.Name LIKE ? ORDER BY LGM.Name",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT DISTINCT TOP 100 LGM.LedgerGroupID, LGM.Name FROM LedgerGroupMaster LGM "
                "INNER JOIN CustomerMaster CM ON CM.LedgerGroupId = LGM.LedgerGroupID "
                "WHERE CM.custid IN (SELECT custid FROM customerattributes WHERE AttributeID = 1) AND CM.disabled = 0 "
                "ORDER BY LGM.Name"
            )
        return [LookupOption(label=r["Name"], value=r["LedgerGroupID"]) for r in rows_to_dicts(cursor)]


@router.get("/cust-ledger/customers", response_model=list[str])
def get_cust_ledger_customers(search: str = "") -> list[str]:
    # Selection is only ever converted back to a name for the row query
    # (DisplayName = ?), never used by CustID — a plain string list (like
    # City/Area) avoids the same "selection no longer in the re-searched
    # list" problem an ID-keyed dropdown would have here.
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT DISTINCT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM "
                "customerattributes WHERE AttributeID = 1) AND disabled = 0 AND groupid = 1 AND DisplayName LIKE ? ORDER BY DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT DISTINCT TOP 100 DisplayName FROM customermaster WHERE custid IN (SELECT custid FROM "
                "customerattributes WHERE AttributeID = 1) AND disabled = 0 AND groupid = 1 ORDER BY DisplayName"
            )
        return [r["DisplayName"] or "" for r in rows_to_dicts(cursor)]


@router.get("/cust-ledger", response_model=list[CustLedgerRow])
def get_cust_ledger_rows(
    software: str = "", city: str = "", yt_status: str = "", customer: str = "", area: str = "", ledger_group_id: int = 0
) -> list[CustLedgerRow]:
    where = ""
    params: list = []
    software_filter = ""
    if software:
        software_filter = " AND attrvalue = ?"
        params.append(software)
    if city:
        where += " AND City = ?"
        params.append(city)
    if yt_status:
        where += " AND YTStatus = ?"
        params.append(yt_status)
    if customer:
        where += " AND DisplayName = ?"
        params.append(customer)
    if area:
        where += " AND Area = ?"
        params.append(area)
    if ledger_group_id:
        where += " AND LedgerGroupId = ?"
        params.append(ledger_group_id)

    # No default filters narrow this query in the source either — capped to
    # 500 rows (ordered same as source) since the AttributeID=1 customer set
    # is 68k+ rows and each row runs a correlated AMC-expiry subquery.
    sql = (
        "SELECT TOP 500 CX.CustID CustomerID, CX.DisplayName CustomerName, CX.Area, CX.City CityName, CX.Email, "
        "CX.MobileNo, CX.YTDate, CX.YTStatus, CX.YTRemark, CX.YTUser, CX.EXEStatus, "
        "(SELECT Max(Expiry) FROM (SELECT cm.custid, (SELECT sum(cast(value AS money)) FROM udfdetail ud1 "
        "WHERE ud1.udfid = 1 AND ud1.voucherno = sm.voucherno) Value, (SELECT cast(udf.Value AS DateTime) FROM "
        "udfdetail udf WHERE udf.udfid = 5 AND udf.voucherno = sm.voucherno AND udf.value <> '') Expiry "
        "FROM udfdetail ud, salesmaster sm, customermaster cm WHERE cm.custid = sm.custid AND ud.voucherno = "
        "sm.voucherno AND ud.udfid = 5 AND ud.transtypeid = 1 AND cm.custid = CX.CustID) a GROUP BY custid) AMC "
        f"FROM customermaster CX WHERE custid IN (SELECT custid FROM customerattributes WHERE AttributeID = 1{software_filter}) "
        f"AND disabled = 0{where} ORDER BY CustomerName"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        CustLedgerRow(
            customer_id=r["CustomerID"], customer_name=r["CustomerName"] or "", area=r["Area"] or "",
            city_name=r["CityName"] or "", email=r["Email"] or "", mobile_no=r["MobileNo"] or "",
            amc=r["AMC"].isoformat() if r["AMC"] else None, yt_date=r["YTDate"].isoformat() if r["YTDate"] else None,
            yt_status=r["YTStatus"] or "", yt_remark=r["YTRemark"] or "", exe_status=r["EXEStatus"] or "",
            yt_user=r["YTUser"] or "",
        )
        for r in rows
    ]


@router.get("/cust-ledger/{customer_id}/sale-detail", response_model=list[CustLedgerSaleRow])
def get_cust_ledger_sale_detail(customer_id: int, with_amc: bool = False) -> list[CustLedgerSaleRow]:
    excl_returned = (
        " AND sd.ProductID NOT IN (SELECT sRd.ProductID FROM SalesReturnMaster SRM INNER JOIN SalesReturnDetail SRD "
        "ON SRM.VoucherNo = SRD.VoucherNo WHERE CustID = ?)"
    )
    excl_lob = (
        " AND SD.ProductID NOT IN (SELECT x.productid FROM lobattributevalue x WHERE x.lobid = 1 AND x.attributeid = 1 AND x.valueid = '16')"
    )
    sql = (
        "SELECT SM.CustID CustomerID, SM.VoucherNo, SM.Date, (SELECT X.value FROM udfdetail X WHERE X.udfid = 5 "
        "AND X.voucherno = Sm.VoucherNo) Expiry, Sd.SrNo, P.ProductName, Sd.Qty, Sd.Rate, Sd.Amount "
        "FROM SalesMaster SM INNER JOIN SalesDetail Sd ON Sm.VoucherNo = Sd.VoucherNo "
        "INNER JOIN Products P ON P.ProductID = SD.ProductID WHERE SM.CustID = ?"
    )
    if not with_amc:
        sql += excl_lob
    sql += excl_returned + " ORDER BY SM.Date DESC"
    with get_cursor() as cursor:
        cursor.execute(sql, customer_id, customer_id)
        rows = rows_to_dicts(cursor)
    return [
        CustLedgerSaleRow(
            customer_id=r["CustomerID"], voucher_no=r["VoucherNo"], date=r["Date"].isoformat() if r["Date"] else "",
            expiry=str(r["Expiry"]) if r["Expiry"] else None, sr_no=r["SrNo"], product_name=r["ProductName"] or "",
            qty=float(r["Qty"] or 0), rate=float(r["Rate"] or 0), amount=float(r["Amount"] or 0),
        )
        for r in rows
    ]


@router.post("/cust-ledger/{customer_id}/yt-info")
def update_cust_ledger_yt_info(customer_id: int, body: UpdateYtInfoRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE CustomerMaster SET EXEStatus = ?, YTDate = ?, YTRemark = ?, YTStatus = ? WHERE CustID = ?",
            body.exe_status, body.yt_date, body.yt_remark, body.yt_status, customer_id,
        )
    return {"success": True}


# -------------------- CustomerInfo --------------------


@router.get("/info/{cust_id}", response_model=CustomerInfoRow | None)
def get_customer_info(cust_id: int) -> CustomerInfoRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT C.CustID, G.Name GroupName, C.Name AS ShopName, "
            "FlatNo + '' + Floor + '' + BldgName + '' + Road + '' + Area + ',' + City AS Address, "
            "Landmark AS ContactPerson, MobileNo, PhNo, Email FROM CustomerMaster C "
            "INNER JOIN CustomerGroupmaster G ON C.GroupID = G.GroupID WHERE C.CustID = ?",
            cust_id,
        )
        rows = rows_to_dicts(cursor)
    if not rows:
        return None
    r = rows[0]
    return CustomerInfoRow(
        cust_id=r["CustID"], customer_name=r["ShopName"] or "", group_name=r["GroupName"] or "",
        address=r["Address"] or "", contact_person=r["ContactPerson"] or "", mobile_no=r["MobileNo"] or "",
        phone_no=r["PhNo"] or "", email=r["Email"] or "",
    )


# -------------------- CustTINFollowup --------------------


@router.get("/tin-followup/filters")
def get_cust_tin_followup_filters() -> dict:
    # Was 3 separate `SELECT DISTINCT ... FROM vw_CustomerTINFollowup`
    # queries. The view's own VATCST derived join (SalesMaster/SalesDetail
    # since 01-Apr-2015, grouped per customer) makes every hit against this
    # view ~0.5-1.3s regardless of how little it returns — querying it 3x
    # for 3 different distinct columns paid that cost 3 times over. One pass
    # over the view instead, distinct sets computed in Python; the view
    # itself is already bounded to a small row count by its own WHERE clause
    # (only customers with a blank/pending TIN status), so pulling every row
    # once is cheap.
    with get_cursor() as cursor:
        cursor.execute("SELECT Software, SalesPerson, FollowUpRemark FROM vw_CustomerTINFollowup")
        rows = rows_to_dicts(cursor)
    salesmen = sorted({r["SalesPerson"] or "" for r in rows})
    software = sorted({r["Software"] or "" for r in rows})
    followup_remarks = sorted({r["FollowUpRemark"] for r in rows if r["FollowUpRemark"]})
    return {"salesmen": salesmen, "software": software, "followup_remarks": followup_remarks}


@router.get("/tin-followup", response_model=list[CustTinFollowupRow])
def get_cust_tin_followup_rows(software: str = "", salesman: str = "", followup_remark: str = "") -> list[CustTinFollowupRow]:
    # Filters are exact values chosen from get_cust_tin_followup_filters()'s
    # own distinct lists, not free text — matching them with LIKE '%value%'
    # was both slower (a leading-wildcard LIKE can't use an index, forcing a
    # scan of the already-expensive view) and wrong (selecting salesman "RAM"
    # would also match "SHRIRAM"/"RAMESH"). Exact equality instead, and the
    # predicate is omitted entirely when a filter isn't set.
    with get_cursor() as cursor:
        sql = "SELECT * FROM vw_CustomerTINFollowup WHERE 1=1"
        params: list = []
        if software:
            sql += " AND Software = ?"
            params.append(software)
        if salesman:
            sql += " AND SalesPerson = ?"
            params.append(salesman)
        if followup_remark:
            sql += " AND FollowUpRemark = ?"
            params.append(followup_remark)
        sql += " ORDER BY FollowUpRemark, AMCExpiry DESC, DisplayName"
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        CustTinFollowupRow(
            cust_id=r["CustID"], software=r["Software"] or "", display_name=r["DisplayName"] or "",
            class_name=r["ClassName"] or "", amc_expiry=r["AMCExpiry"].isoformat() if r["AMCExpiry"] else None,
            sales_person=r["SalesPerson"] or "", mobile_no=r["MobileNo"] or "", email=r["Email"] or "",
            tin_no=r["TINNo"] or "", followup_remark=r["FollowupRemark"] or "",
        )
        for r in rows
    ]


@router.post("/tin-followup/{cust_id}")
def update_cust_tin(cust_id: int, body: UpdateCustTinRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webProc_UpdateCustTINNo ?, ?, ?, ?, 0", cust_id, body.tin_no, body.email, body.followup_remark
        )
    return {"success": True}


# -------------------- CustomerAns (blocked — see module docstring) --------------------


@router.get("/ans/filters")
def get_customer_ans_filters() -> dict:
    # Customer used to be a DISTINCT ClientName list pulled from this same
    # blocked view — replaced with the standard Top-100/search customer
    # picker (get_customers in admin_vouchers.py, sourced from CustomerMaster
    # directly) so the picker itself works and stays fast regardless of this
    # view's linked-server gap. Questions still comes from the view since
    # there's no other source for the real question text.
    with get_cursor() as cursor:
        cursor.execute("SELECT DISTINCT Question FROM vw_web_clientAnswerSheet ORDER BY Question")
        questions = [r["Question"] for r in rows_to_dicts(cursor)]
    return {"questions": questions}


@router.get("/ans")
def get_customer_ans_rows(customer_id: int = 0, question: str = "") -> list[dict]:
    where = ""
    params: list = []
    if customer_id:
        where += " AND ClientID = ?"
        params.append(customer_id)
    if question:
        where += " AND Question = ?"
        params.append(question)
    with get_cursor() as cursor:
        cursor.execute(f"SELECT * FROM vw_web_clientAnswerSheet WHERE 1 = 1{where}", *params)
        rows = rows_to_dicts(cursor)
    return [
        {
            "client_id": r["ClientID"], "client_name": r["ClientName"] or "",
            "amc_expiry_date": r["AMCExpiryDate"].isoformat() if r.get("AMCExpiryDate") else None,
            "question": r["Question"] or "", "answer": r["Answer"] or "",
            "answer_date": r["AnswerDate"].isoformat() if r.get("AnswerDate") else None,
            "answer_time": str(r.get("AnswerTime") or ""), "user_name": r.get("UserName") or "",
            "machine_name": r.get("MachineName") or "",
        }
        for r in rows
    ]


# -------------------- CustomerGracePeriod (blocked — see module docstring) --------------------


@router.get("/grace-period/software", response_model=list[LookupOption])
def get_grace_period_software() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT SoftwareID, SoftwareName FROM vw_web_softwaremaster")
        return [LookupOption(label=r["SoftwareName"], value=r["SoftwareID"]) for r in rows_to_dicts(cursor)]


@router.get("/grace-period")
def get_grace_period_rows(software_id: int = 0) -> list[dict]:
    where = " WHERE A.SoftwareID = ?" if software_id else ""
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT SM.SoftwareName, A.ClientID, A.ClientName, A.City, A.AMCDueDate, A.AMCDueDelayDays, "
            f"A.AMCGraceRemark, A.Disabled FROM vw_web_AMCGracePeriodCustomer A "
            f"INNER JOIN vw_web_softwaremaster SM ON Sm.SoftwareID = A.Softwareid{where}",
            *([software_id] if software_id else []),
        )
        rows = rows_to_dicts(cursor)
    return [
        {
            "software_name": r["SoftwareName"] or "", "client_id": r["ClientID"], "client_name": r["ClientName"] or "",
            "city": r["City"] or "", "amc_due_date": r["AMCDueDate"].isoformat() if r.get("AMCDueDate") else None,
            "amc_due_delay_days": r["AMCDueDelayDays"] or 0, "amc_grace_remark": r["AMCGraceRemark"] or "",
            "disabled": bool(r["Disabled"]),
        }
        for r in rows
    ]


# -------------------- CustomerLicenseViolation (blocked — see module docstring) --------------------


@router.get("/license-violation", response_model=list[LicenseViolationRow])
def get_license_violation_rows(customer_id: int = 0) -> list[LicenseViolationRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC WebProc_GetCustVoilationDetails ?", customer_id)
        rows = rows_to_dicts(cursor)
    return [
        LicenseViolationRow(
            cust_id=r["CustID"], display_name=r["Displayname"] or "", city=r["City"] or "",
            amc_due_date=r["AMCDueDate"].isoformat() if r.get("AMCDueDate") else None,
            amc_amount=float(r["AMCAmount"] or 0), ledger_balance=float(r["Ledgerbalance"] or 0),
            pirated_qty=int(r["PiratedQty"] or 0), piracy_amt=float(r["PiracyAmt"] or 0),
            first_bill=r["FirstBill"].isoformat() if r.get("FirstBill") else None,
            first_salesman=r.get("FirstSalesMan") or "",
        )
        for r in rows
    ]


@router.get("/license-violation/{cust_id}/modules", response_model=list[LicenseModuleRow])
def get_license_violation_modules(cust_id: int) -> list[LicenseModuleRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_GetDrillDownCustLicenceVoilation ?", cust_id)
        rows = rows_to_dicts(cursor)
    return [
        LicenseModuleRow(
            pirated=bool(r["P"]), product_id=r["ProductID"], product_name=r["ProductName"] or "",
            active_qty=float(r["ActiveQty"] or 0), billed_qty=float(r["BilledQty"] or 0),
            pirated_qty=float(r["PiratedQty"] or 0), piracy_amt=float(r["PiracyAmt"] or 0),
            answer_time=r["AnswerTime"].isoformat() if r["AnswerTime"] else "",
        )
        for r in rows
    ]


@router.get("/license-violation/{cust_id}/actions", response_model=list[LicenseActionRow])
def get_license_violation_actions(cust_id: int) -> list[LicenseActionRow]:
    # Same underlying proc/shape as support_amc.py's get_action_history, filtered
    # to ActionStageID=28 (source: `dt.Select("ActionStageID=28")`).
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_showenquiryaction ?, 0", cust_id)
        rows = [r for r in rows_to_dicts(cursor) if r["ActionStageID"] == 28]

    if not rows or rows[-1]["NextAction"] == "Closed":
        # Source's synthetic fallback row when there's no open ActionStageID=28
        # history yet — keeps the "Add" next-step link available.
        return [
            LicenseActionRow(
                action_id=0, creation_date=None, creation_time="", follow_up_by="", follow_up_remark="",
                assigned_to="", action_description="", action_remark="", next_action="Add",
            )
        ]
    return [
        LicenseActionRow(
            action_id=r["ActionId"], creation_date=r["CreationDate"].isoformat() if r["CreationDate"] else None,
            creation_time=str(r["CreationTime"] or ""), follow_up_by=r["CreatedBy"] or "",
            follow_up_remark=r["ActionRemark"] or "", assigned_to=r["AssignedTo"] or "",
            action_description=r["ActionDescription"] or "", action_remark=r["ActionRemark"] or "",
            next_action=r["NextAction"] or "",
        )
        for r in rows
    ]
