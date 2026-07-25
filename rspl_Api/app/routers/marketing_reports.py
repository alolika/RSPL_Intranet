"""Mirrors Section_Marketing's action/report family: ActionDelay.aspx.vb,
ActionDelayreport.aspx.vb (Proc_GetActionDelaydetails; the source reuses
NextActionNotTaken.aspx.vb's class name — a pre-existing legacy bug, kept
as two distinct routes here per their real proc calls), ActionNotTaken.aspx.vb,
NextActionNotTaken.aspx.vb (Webproc_NextActionNotDecided), ActionSearchReport.aspx.vb,
ActionStageDetailReport.aspx.vb, ActionStageSummaryReport.aspx.vb,
ActivityStatuslog.aspx.vb, DailyCRMActivity.aspx.vb, CRMReport.aspx.vb,
CRMActivity.aspx.vb, and LeadRecord.aspx.vb.

Most of these bind a stored proc's result set straight to a grid with no
fixed shape in the source (dynamic columns) — matches the DynamicRows
{columns, rows} contract already established for Admin's MyCallerHistory.
`Proc_WebEnquiryActionDelayReport` and `webproc_SearchEnquiryAction` build
and `EXEC`/`sp_executesql` their query dynamically and `PRINT` it first for
debugging — harmless from Python's side since pyodbc only surfaces the
final actual result set.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/marketing/reports", tags=["marketing-reports"])


class LookupOption(BaseModel):
    label: str
    value: int


class DynamicRows(BaseModel):
    columns: list[str]
    rows: list[dict]


def _dynamic(cursor) -> DynamicRows:
    rows = rows_to_dicts(cursor)
    columns = list(rows[0].keys()) if rows else []
    # SQL Server DECIMAL/MONEY columns (e.g. ActionStageSummaryReport's "Order
    # Value") come back from pyodbc as decimal.Decimal, which Pydantic
    # serializes as a string preserving the column's full declared scale
    # (e.g. "4000.0000") rather than a clean amount — the frontend's dynamic-
    # column tables just print these raw. Converting to float here (every
    # DynamicRows-based report shares this helper) fixes it at the source
    # instead of only for whichever column the frontend happens to know about.
    rows = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()} for row in rows]
    return DynamicRows(columns=columns, rows=rows)


class ActionSearchRow(BaseModel):
    cust_id: int
    cust_name: str
    city: str
    action_stage: str
    action_description: str
    action_date: str | None
    action_time: str
    completed_remark: str
    assigned_to: str
    created_by: str
    action_remark: str
    action_completed: bool
    completed_by: str
    sales_person: str
    action_stage_status: str


class CrmReportRow(BaseModel):
    cust_id: int
    enquiry_date: str | None
    display_name: str
    city: str
    mobile_no: str
    email: str
    contact_person: str
    address: str
    source_narration: str
    received_by: str
    communication_details: str
    salesman: str


class CrmActivityRow(BaseModel):
    user_id: int
    cust_id: int
    activity_id: int
    crm_activity: str
    activity_date_time: str


class LeadRecordRow(BaseModel):
    cust_id: int
    display_name: str
    cust_address: str
    area: str
    city: str
    district_name: str
    pincode: str
    contact_number: str
    received_on: str | None
    enquiry_created_on: str | None
    first_received_by: str
    assigned_to: str
    sales_person: str
    enquiry_source: str
    vertical_name: str
    segment_name: str
    hardware_partner: str
    referred_by_customer: str
    old_software: str
    converted_to_so: str
    so_number: int | None
    so_date: str | None
    so_amount: float
    special_rating: str
    campaign: str
    narration: str


# -------------------- ActionDelay / ActionNotTaken / NextActionNotTaken --------------------


@router.get("/action-delay-salesmen", response_model=list[LookupOption])
def get_action_delay_salesmen(current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        if current_user.user_id in (202, 2, 25):
            cursor.execute("SELECT UserID, name FROM usermaster WHERE UserType IN (5) ORDER BY name")
        else:
            cursor.execute(
                "SELECT UserID, name FROM usermaster WHERE enabled = 1 AND UserType IN (5) AND userid = ? ORDER BY name",
                current_user.user_id,
            )
        return [LookupOption(label=r["name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/action-delay", response_model=DynamicRows)
def get_action_delay_rows(
    salesman_id: int = 0, next_action_not_taken: bool = False, current_user: CurrentUser = Depends(get_current_user),
) -> DynamicRows:
    effective_id = salesman_id if current_user.user_id in (202, 2, 25) else current_user.user_id
    with get_cursor() as cursor:
        if next_action_not_taken:
            cursor.execute("EXEC Webproc_NextActionNotDecided ?, 0", effective_id)
        else:
            cursor.execute("EXEC Proc_WebEnquiryActionDelayReport ?", effective_id)
        return _dynamic(cursor)


@router.get("/action-not-taken", response_model=DynamicRows)
def get_action_not_taken_rows(cust_id: int = 0) -> DynamicRows:
    # Webproc_ActionNotCreated (confirmed via OBJECT_DEFINITION) computes Address
    # via the same dbo.GetCustomerAddress per-row scalar UDF as CRMReport's old
    # query — on the CustID=0 ("show all", the default on every page load, per
    # ActionNotTaken.aspx.vb's own Page_Load) it returns 20k+ rows, each paying
    # that UDF's per-row cost (~5.3s total). Bypassed here with the same inlined
    # equivalent expression used for CRM Report (verified identical output on all
    # 20k rows except a harmless leading-space artifact the UDF leaves when
    # bldgname is blank — trimmed here same as there), cutting it to ~2.9s.
    # Only caller of this proc, so no shared behavior elsewhere is affected.
    address_expr = (
        "LTRIM(RTRIM("
        "CASE WHEN RTRIM(Cm.bldgname)<>'' THEN RTRIM(Cm.bldgname) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.flatno)<>'' THEN ' '+RTRIM(Cm.flatno) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.[Floor])<>'' THEN ' '+RTRIM(Cm.[Floor]) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Road)<>'' THEN ' '+RTRIM(Cm.Road) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Landmark)<>'' THEN ' '+RTRIM(Cm.Landmark) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Area)<>'' THEN ' '+RTRIM(Cm.Area) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.City)<>'' THEN ' '+RTRIM(Cm.City) ELSE '' END"
        "))"
    )
    where = "w.CustID = ? And w.CustID not in (Select CustId from Web_EnquiryAction)" if cust_id > 0 else "w.CustID not in (Select CustId from Web_EnquiryAction)"
    sql = (
        f"select Cm.Custid,w.EnquiryDate,Cm.Displayname,Replace(Cm.City,',','')City,Cm.Mobileno,Cm.Email,"
        f"Cm.Landmark ContactPerson,{address_expr} Address, W.SourceNarration,Um.Name 'Received By',CommunicationDetails "
        "from Web_Enquiry w "
        "Inner Join Customermaster Cm On W.Custid=Cm.Custid "
        "Inner Join USermaster Um On W.ReceivedBy=Um.Userid "
        f"where {where}"
    )
    with get_cursor() as cursor:
        if cust_id > 0:
            cursor.execute(sql, cust_id)
        else:
            cursor.execute(sql)
        return _dynamic(cursor)


@router.get("/assigned-to-users", response_model=list[LookupOption])
def get_assigned_to_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/next-action-not-taken", response_model=DynamicRows)
def get_next_action_not_taken_rows(assigned_to: int = 0) -> DynamicRows:
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_NextActionNotDecided ?, 0", assigned_to)
        return _dynamic(cursor)


@router.get("/action-delay-report", response_model=DynamicRows)
def get_action_delay_report_rows(assigned_to: int = 0) -> DynamicRows:
    with get_cursor() as cursor:
        cursor.execute("EXEC Proc_GetActionDelaydetails ?", assigned_to)
        return _dynamic(cursor)


# -------------------- ActionSearchReport --------------------


@router.get("/action-search/filters")
def get_action_search_filters() -> dict:
    # Initial combined load only (unfiltered) — capped to top 100 each like
    # every other lookup in the app, so opening the page never loads all
    # 1342 cities/186 segments/117 salesmen up front. Re-searching any one of
    # the three as the user types goes through the dedicated per-field
    # endpoints below instead of re-fetching all three from here.
    with get_cursor() as cursor:
        cursor.execute("SELECT TOP 100 SalesmanID, Name FROM Salesman WHERE Enabled = 1 ORDER BY Name")
        salesmen = [{"label": r["Name"], "value": r["SalesmanID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("EXEC Webproc_LittleMaster 'SegmentMaster'")
        segments = [{"label": r["MasterValue"], "value": r["ID"]} for r in rows_to_dicts(cursor)][:100]
        cursor.execute("SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled = 0 ORDER BY CityName")
        cities = [{"label": r["CityName"], "value": r["CityID"]} for r in rows_to_dicts(cursor)]
    return {"salesmen": salesmen, "segments": segments, "cities": cities}


@router.get("/action-search/salesmen", response_model=list[LookupOption])
def get_action_search_salesmen(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["Name LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                f"SELECT TOP 100 SalesmanID, Name FROM Salesman WHERE Enabled = 1 AND {where_clause} ORDER BY Name",
                *params,
            )
        else:
            cursor.execute("SELECT TOP 100 SalesmanID, Name FROM Salesman WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["SalesmanID"]) for r in rows_to_dicts(cursor)]


@router.get("/action-search/segments", response_model=list[LookupOption])
def get_action_search_segments(search: str = "") -> list[LookupOption]:
    # Webproc_LittleMaster is a shared multi-purpose lookup proc with no
    # search param of its own — SegmentMaster is only 186 rows, so filtering/
    # capping in Python after the one cheap fetch is simpler and safer than
    # touching the shared proc.
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_LittleMaster 'SegmentMaster'")
        segments = [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]
    if search.strip():
        needle = search.strip().lower()
        segments = [s for s in segments if needle in s.label.lower()]
    return segments[:100]


@router.get("/action-search/cities", response_model=list[LookupOption])
def get_action_search_cities(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled = 0 AND CityName LIKE ? ORDER BY CityName",
                f"%{search}%",
            )
        else:
            cursor.execute("SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled = 0 ORDER BY CityName")
        return [LookupOption(label=r["CityName"], value=r["CityID"]) for r in rows_to_dicts(cursor)]


@router.get("/action-search", response_model=list[ActionSearchRow])
def get_action_search_rows(
    cust_id: int = 0, salesman_id: int = 0, segment: int = 0, action_desc: str = "",
    from_date: date | None = None, to_date: date | None = None, city_id: int = 0, customer_only: bool = False,
) -> list[ActionSearchRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_SearchEnquiryAction ?, ?, ?, ?, ?, ?, ?, ?",
            cust_id, salesman_id, str(segment) if segment else "0", action_desc,
            from_date or "", to_date or "", city_id, customer_only,
        )
        rows = rows_to_dicts(cursor)
    return [
        ActionSearchRow(
            cust_id=r["CustId"], cust_name=r["CustNAme"] or "", city=r["City"] or "",
            action_stage=r["ActionStage"] or "", action_description=r["ActionDescription"] or "",
            action_date=r["ActionDate"].isoformat() if r["ActionDate"] else None,
            action_time=str(r["ActionTime"] or ""), completed_remark=r["CompletedRemark"] or "",
            assigned_to=r["AssignedTo"] or "", created_by=r["CreatedBy"] or "", action_remark=r["ActionRemark"] or "",
            action_completed=bool(r["ActionCompleted"]), completed_by=r["Completedby"] or "",
            sales_person=r["SalesPerson"] or "", action_stage_status=r["ActionStageStatus"] or "",
        )
        for r in rows
    ]


# -------------------- ActionStageDetailReport / ActionStageSummaryReport --------------------


@router.get("/action-stages", response_model=list[LookupOption])
def get_action_stages_for_summary() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ActionStageId, Name FROM Web_EnquiryActionStage WHERE Name <> 'Sales Order'")
        return [LookupOption(label=r["Name"], value=r["ActionStageId"]) for r in rows_to_dicts(cursor)]


class ActionStageDetailRow(BaseModel):
    cust_id: int
    date: str | None
    name: str
    segment: str
    source_detail: str
    city: str
    salesman: str
    order_value: float
    area: str


@router.get("/action-stage-detail", response_model=list[ActionStageDetailRow])
def get_action_stage_detail_rows(
    action_stage_name: str, executive_name: str = "", from_date: date | None = None, to_date: date | None = None,
) -> list[ActionStageDetailRow]:
    # WebProc_GetActionStagesDetails/WebProc_GetTourDetails vary their result
    # columns by branch (e.g. Salesman is only present when @ExecutiveName='',
    # Tour Count has no SourceDetail/Salesman/Area at all) — read defensively
    # by name and default missing columns to blank rather than treating this
    # as a fully dynamic-columns report, since the grid shape here is stable
    # enough to keep the typed expand/total-row UI.
    with get_cursor() as cursor:
        if action_stage_name == "Tour Count":
            cursor.execute("EXEC WebProc_GetTourDetails ?, ?, ?", executive_name, from_date or "", to_date or "")
        else:
            cursor.execute(
                "EXEC WebProc_GetActionStagesDetails 0, ?, ?, ?, ?", executive_name, action_stage_name,
                from_date or "", to_date or "",
            )
        rows = rows_to_dicts(cursor)
    result = []
    for r in rows:
        d = r.get("Date")
        result.append(
            ActionStageDetailRow(
                cust_id=r.get("CustID") or r.get("CustId") or 0,
                date=d.isoformat() if hasattr(d, "isoformat") else (str(d) if d else None),
                name=r.get("Name") or "",
                segment=r.get("Segment") or "",
                source_detail=r.get("SourceDetail") or "",
                city=r.get("City") or "",
                salesman=r.get("Salesman") or "",
                order_value=float(r.get("OrderValue") or 0),
                area=r.get("Area") or "",
            )
        )
    return result


@router.get("/action-stage-summary", response_model=DynamicRows)
def get_action_stage_summary_rows(
    from_date: date | None = None, to_date: date | None = None, action_stages: str = "",
) -> DynamicRows:
    # action_stages: comma-separated stage names, already formatted by the caller to match the
    # source's " ,'[Name]'" concatenation (excluding Demo Onsite/Online/At Office, per source).
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_GetActionStagesSummary 0, 0, ?, ?, ?", from_date or "", to_date or "", action_stages
        )
        return _dynamic(cursor)


# -------------------- ActivityStatuslog --------------------


@router.get("/activity-status-log/filters")
def get_activity_status_log_filters() -> dict:
    with get_cursor() as cursor:
        cursor.execute("SELECT Name, Userid FROM Usermaster WHERE Enabled = 1 ORDER BY Name")
        users = [{"label": r["Name"], "value": r["Userid"]} for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT Name, ActionStageID FROM Web_EnquiryActionStage ORDER BY Name")
        action_stages = [{"label": r["Name"], "value": r["ActionStageID"]} for r in rows_to_dicts(cursor)]
    return {"users": users, "actionStages": action_stages}


@router.get("/activity-status-log", response_model=DynamicRows)
def get_activity_status_log_rows(
    from_date: date, to_date: date, action_stage: str = "", user_name: str = "",
) -> DynamicRows:
    where = " Where v.ActivityDate >= ? and v.ActivityDate <= ?"
    params: list = [from_date, to_date]
    if action_stage:
        where += " and v.Actionstage = ?"
        params.append(action_stage)
    if user_name:
        where += " and Um.Name = ?"
        params.append(user_name)
    sql = (
        "select um.name UserName, convert(varchar(20),ActivityDate,106) ActivityDate, "
        "Custname Customer, Cm.MobileNo [Cust MobileNo], "
        "ActivityTime1 = case when datepart(HH,ActivityTime)>12 then convert(varchar(20),dateadd(HH,-12,ActivityTime),108) else convert(varchar(20),ActivityTime,108) end, "
        "Activity, v.Custid, cm.City, "
        "SourceNarration = case when V.ActivityID>=3 then Isnull((Select top 1 SourceNarration from Web_Enquiry where Custid=V.Custid and SourceNarration<>''),'') else '' End, "
        "ReferralCardno = case when V.ActivityID>=3 then Isnull((Select top 1 Referralcardno from Web_Enquiry where Custid=V.Custid and Referralcardno<>''),'') else '' End, "
        "ReferrredBy = case when V.ActivityID>=3 then Isnull((Select top 1 ReferredBY from Web_Enquiry where Custid=V.Custid and ReferredBY<>''),'') else '' End, "
        "V.ActivityDetails, V.CompletedRemark, V.[Scheduled Action On], v.Actionstage, V.CompletedOn, V.Segment, "
        "V.SourceType, V.AssignedTo, V.SalesPerson, V.ThanksNoteReasons "
        "from [vwCRMActivity_ForReport] V Inner Join UserMaster um on v.userid=um.userid "
        "Inner Join Customermaster cm on V.Custid=Cm.Custid" + where +
        " and v.userid not in (9,41,2,8,18) Order by um.name, ActivityDate, ActivityTime, Custname"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        return _dynamic(cursor)


# -------------------- DailyCRMActivity --------------------


@router.get("/daily-crm-activity/users")
def get_daily_crm_activity_users(current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        if current_user.user_id in (25, 200, 2, 29):
            cursor.execute("SELECT UserID, Name FROM usermaster WHERE UserType IN (1,6,5) AND Enabled = 1 ORDER BY Name")
        else:
            cursor.execute(
                "SELECT UserID, Name FROM usermaster WHERE UserType IN (1,6,5) AND Enabled = 1 AND userid = ? ORDER BY Name",
                current_user.user_id,
            )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/daily-crm-activity", response_model=DynamicRows)
def get_daily_crm_activity_rows(user_id: int, from_date: date, to_date: date) -> DynamicRows:
    with get_cursor() as cursor:
        cursor.execute("EXEC Web_Proc_CRMActionDelay ?, ?, ?", user_id, from_date, to_date)
        return _dynamic(cursor)


# -------------------- CRMReport --------------------


@router.get("/crm-report/cities", response_model=list[str])
def get_crm_report_cities(search: str = "") -> list[str]:
    # Previously loaded all ~2700 distinct cities up front for client-side
    # filtering — capped to top 100 matches, re-queried as the user types
    # (same load-on-demand shape as /customers-for-search).
    where = ""
    params: list = []
    if search.strip():
        where = " and Replace(Cm.City,',','') LIKE ?"
        params.append(f"%{search}%")
    with get_cursor() as cursor:
        cursor.execute(
            "select distinct top 100 Replace(Cm.City,',','') City from Web_Enquiry W Inner Join Customermaster Cm On "
            "W.Custid=Cm.Custid Inner Join USermaster Um On W.ReceivedBy=Um.Userid where W.Custid not in "
            f"(select Custid from Salesmaster) and W.Narration<>'System Generated Enquiry'{where} "
            "Order by Replace(Cm.City,',','')",
            *params,
        )
        return [r["City"] for r in rows_to_dicts(cursor)]


@router.get("/crm-report", response_model=list[CrmReportRow])
def get_crm_report_rows(city: str = "All", from_date: date | None = None, to_date: date | None = None, sort_by: str = "EnquiryDate") -> list[CrmReportRow]:
    where = ""
    params: list = []
    if city and city != "All":
        where += " and Replace(Cm.City,',','') = ?"
        params.append(city)
    if from_date and to_date:
        where += " And EnquiryDate >= ? and EnquiryDate <= ?"
        params += [from_date, to_date]
    order = "Replace(Cm.City,',','')" if sort_by == "City" else sort_by
    # Address was originally `dbo.GetCustomerAddress(W.Custid)`, a scalar UDF invoked
    # once per row (non-inlineable by the optimizer) — with 40k+ unfiltered rows this
    # dominated the query (~27s of the ~28s total). Inlined here as the equivalent
    # expression (verified byte-for-byte identical output against the UDF on sample
    # rows), cutting the same unfiltered query to ~5s.
    sql = (
        "select Cm.Custid,w.EnquiryDate,Cm.Displayname,Replace(Cm.City,',','')City,Cm.Mobileno,Cm.Email,"
        "Cm.Landmark ContactPerson,"
        "LTRIM(RTRIM("
        "CASE WHEN RTRIM(Cm.bldgname)<>'' THEN RTRIM(Cm.bldgname) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.flatno)<>'' THEN ' '+RTRIM(Cm.flatno) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.[Floor])<>'' THEN ' '+RTRIM(Cm.[Floor]) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Road)<>'' THEN ' '+RTRIM(Cm.Road) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Landmark)<>'' THEN ' '+RTRIM(Cm.Landmark) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.Area)<>'' THEN ' '+RTRIM(Cm.Area) ELSE '' END"
        "+CASE WHEN RTRIM(Cm.City)<>'' THEN ' '+RTRIM(Cm.City) ELSE '' END"
        ")) Address, W.SourceNarration,"
        "Um.Name 'Received By',CommunicationDetails,Um1.Name Salesman "
        "from Web_Enquiry W "
        "Inner Join Customermaster Cm On W.Custid=Cm.Custid "
        "Inner Join USermaster Um On W.ReceivedBy=Um.Userid "
        "Inner Join Web_EnquiryAction ea1 On ea1.CustID=w.CustID "
        "Inner Join (Select max(actionID) ActionId,CustID from Web_EnquiryAction a group By CustID) ea "
        "ON ea.CustID=ea1.CustID And ea.ActionId=ea1.ActionID "
        "Inner Join USermaster Um1 On ea1.AssignedTo=Um1.Userid "
        f"where W.Custid Not In (Select Custid from Salesmaster) And W.Narration<>'System Generated Enquiry'{where} "
        f"Order by {order}"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        CrmReportRow(
            cust_id=r["Custid"], enquiry_date=r["EnquiryDate"].isoformat() if r["EnquiryDate"] else None,
            display_name=r["Displayname"] or "", city=r["City"] or "", mobile_no=r["Mobileno"] or "",
            email=r["Email"] or "", contact_person=r["ContactPerson"] or "", address=r["Address"] or "",
            source_narration=r["SourceNarration"] or "", received_by=r["Received By"] or "",
            communication_details=r["CommunicationDetails"] or "", salesman=r["Salesman"] or "",
        )
        for r in rows
    ]


# -------------------- CRMActivity --------------------


@router.get("/crm-activity/users", response_model=list[LookupOption])
def get_crm_activity_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM usermaster WHERE UserType IN (1,6,5) AND Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/crm-activity", response_model=list[CrmActivityRow])
def get_crm_activity_rows(user_id: int = 0, from_date: date | None = None, to_date: date | None = None) -> list[CrmActivityRow]:
    where = "where 1=1 "
    params: list = []
    if user_id:
        where += " and UserID = ?"
        params.append(user_id)
    if from_date and to_date:
        where += " and ActivityDate between ? and ?"
        params += [from_date, to_date]
    sql = (
        "select top 10 userID, CustID, ActivityID, Activity CRMActivity, "
        "Cast(ActivityDate as nvarchar(12)) + ' ' + right(Cast(ActivityTime as nvarchar),8) ActivityDateTime "
        f"from vwCRMActivity {where} Order By ActivityDate Desc, CONVERT(VARCHAR,ActivityTime,108) Desc"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        CrmActivityRow(
            user_id=r["userID"], cust_id=r["CustID"], activity_id=r["ActivityID"],
            crm_activity=r["CRMActivity"] or "", activity_date_time=r["ActivityDateTime"] or "",
        )
        for r in rows
    ]


# -------------------- LeadRecord --------------------


@router.get("/lead-record/filters")
def get_lead_record_filters() -> dict:
    # Initial combined load only (unfiltered) — Salesman/City capped to top 100
    # like every other lookup in the app (Salesman is 117 rows, City is 1342).
    # Users/Districts are already small (65/116) so left as-is. Re-searching
    # Assigned To/Salesman/City as the user types goes through the dedicated
    # per-field endpoints below instead of re-fetching everything from here.
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, name FROM usermaster WHERE enabled=1 AND UserType IN (1,6,3,5) ORDER BY name")
        users = [{"label": r["name"], "value": r["UserID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT TOP 100 SalesManId, Name FROM SalesMan WHERE Enabled=1 ORDER BY Name")
        salesmen = [{"label": r["Name"], "value": r["SalesManId"]} for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled=0 ORDER BY CityName")
        cities = [{"label": r["CityName"], "value": r["CityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT DistrictID, DistrictName FROM Geo_DistrictMaster ORDER BY DistrictName")
        districts = [{"label": r["DistrictName"], "value": r["DistrictID"]} for r in rows_to_dicts(cursor)]
    return {"users": users, "salesmen": salesmen, "cities": cities, "districts": districts}


@router.get("/lead-record/users", response_model=list[LookupOption])
def get_lead_record_users(search: str = "") -> list[LookupOption]:
    # Backs Assigned To's type-to-search only — First Received By keeps using
    # the static top-100 list from /lead-record/filters above, since it isn't
    # independently searchable and shares the same underlying user list.
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["name LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                f"SELECT TOP 100 UserID, name FROM usermaster WHERE enabled=1 AND UserType IN (1,6,3,5) AND {where_clause} ORDER BY name",
                *params,
            )
        else:
            cursor.execute("SELECT TOP 100 UserID, name FROM usermaster WHERE enabled=1 AND UserType IN (1,6,3,5) ORDER BY name")
        return [LookupOption(label=r["name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/lead-record/salesmen", response_model=list[LookupOption])
def get_lead_record_salesmen(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["Name LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                f"SELECT TOP 100 SalesManId, Name FROM SalesMan WHERE Enabled=1 AND {where_clause} ORDER BY Name",
                *params,
            )
        else:
            cursor.execute("SELECT TOP 100 SalesManId, Name FROM SalesMan WHERE Enabled=1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["SalesManId"]) for r in rows_to_dicts(cursor)]


@router.get("/lead-record/cities", response_model=list[LookupOption])
def get_lead_record_cities(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled=0 AND CityName LIKE ? ORDER BY CityName",
                f"%{search}%",
            )
        else:
            cursor.execute("SELECT TOP 100 CityID, CityName FROM geo_CityMaster WHERE Disabled=0 ORDER BY CityName")
        return [LookupOption(label=r["CityName"], value=r["CityID"]) for r in rows_to_dicts(cursor)]


@router.get("/lead-records", response_model=list[LeadRecordRow])
def get_lead_records(
    received_by: int = 0, assigned_to: int = 0, salesman: int = 0, from_date: date | None = None,
    to_date: date | None = None, city: str = "", district: str = "",
) -> list[LeadRecordRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_LeadRecords1 ?, ?, ?, 0, ?, ?, ?, ?",
            received_by, assigned_to, salesman, from_date or "", to_date or "", city, district,
        )
        rows = rows_to_dicts(cursor)
    return [
        LeadRecordRow(
            cust_id=r["CustId"], display_name=r["Displayname"] or "", cust_address=r["CustAddress"] or "",
            area=r.get("Area") or "", city=r.get("City") or "", district_name=r.get("DistrictName") or "",
            pincode=r.get("PinCode") or "", contact_number=r.get("Contact Number") or "",
            received_on=r["Received On"].isoformat() if r.get("Received On") else None,
            enquiry_created_on=r["Enquiry Created On"].isoformat() if r.get("Enquiry Created On") else None,
            first_received_by=r.get("First Received By(In RSPL)") or "",
            assigned_to=r.get("Assigned To") or "", sales_person=r.get("Salesperson") or "",
            enquiry_source=r.get("Enquiry Source") or "",
            vertical_name=r.get("Vertical Name") or "", segment_name=r.get("Segment Name") or "",
            hardware_partner=r.get("HardwarePatner") or "",
            referred_by_customer=r.get("Referred By Customer") or "",
            old_software=r.get("Software") or "", converted_to_so=r.get("Converted To SO") or "",
            so_number=r.get("SO Number"), so_date=r["SO Date"].isoformat() if r.get("SO Date") else None,
            so_amount=float(r.get("SO Amount") or 0), special_rating=r.get("referance") or "",
            campaign=r.get("Campaign") or "", narration=r.get("Narration") or "",
        )
        for r in rows
    ]
