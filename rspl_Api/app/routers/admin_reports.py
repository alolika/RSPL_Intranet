"""Mirrors Section_Admin/FeedbackRegister.aspx.vb, MyCallerHistory.aspx.vb
(class Section_Admin_MyCallerHistory — distinct from Section_Support's
MyCaller/MyCallerHistory in general_mycaller.py, a different real proc:
WebProc_GetMobileCallhistory reading RSPL_CallHistory), ReferralAnalysis.aspx.vb
(class Section_Marketing_ReferralAnalysis, filed under Admin per the
migration checklist), and SendEmail.aspx.vb (despite the name, just appends
a call-remark string to CustomerAttributes — no actual email is sent).
"""

from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])


class LookupOption(BaseModel):
    label: str
    value: int


class FeedbackRow(BaseModel):
    feedback_id: int
    date: str
    name: str
    email: str
    city: str
    subject: str
    message: str
    user_name: str


class DynamicRows(BaseModel):
    columns: list[str]
    rows: list[dict]


class ReferralAnalysisRow(BaseModel):
    referred_by: str
    referred_cust_id: int
    referred_city: str
    cust_id: int
    new_customer: str
    city: str
    enquiry_date: str | None
    balance_amt: float
    amount: float
    referral_card_no: str
    order_no: int | None
    order_date: str | None
    order_by: str
    introduced_by: str


class ReferralAnalysisFilters(BaseModel):
    cities: list[str]
    referred_by: list[str]
    referral_names: list[str]
    order_by: list[str]


@router.get("/feedback", response_model=list[FeedbackRow])
def get_feedback_rows() -> list[FeedbackRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT f.*, u.name username FROM web_feedback f, UserMaster U WHERE f.userid = u.userid")
        rows = rows_to_dicts(cursor)
    return [
        FeedbackRow(
            feedback_id=r["FeedBackID"], date=r["Date"].isoformat() if r["Date"] else "", name=r["Name"] or "",
            email=r["Email"] or "", city=r["City"] or "", subject=r["Subject"] or "", message=r["Message"] or "",
            user_name=r["username"] or "",
        )
        for r in rows
    ]


@router.get("/my-caller-users", response_model=list[LookupOption])
def get_my_caller_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT userID, Name FROM userMaster WHERE UserID IN (SELECT DISTINCT userID FROM RSPL_CallHistory) ORDER BY Name"
        )
        return [LookupOption(label=r["Name"], value=r["userID"]) for r in rows_to_dicts(cursor)]


@router.get("/my-caller-history", response_model=DynamicRows)
def get_my_caller_history(user_id: int, from_date: date, to_date: date) -> DynamicRows:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_GetMobileCallhistory ?, ?, ?", user_id, datetime.combine(from_date, datetime.min.time()),
            datetime.combine(to_date, datetime.max.time().replace(microsecond=0)),
        )
        rows = rows_to_dicts(cursor)

    columns = list(rows[0].keys()) if rows else ["Entity", "EntityName", "MobileNo", "callledTime", "CallType", "UserName"]
    out_rows = []
    for r in rows:
        row = dict(r)
        if isinstance(row.get("callledTime"), datetime):
            row["callledTime"] = row["callledTime"].isoformat()
        out_rows.append(row)
    return DynamicRows(columns=columns, rows=out_rows)


@router.get("/referral-analysis/filters", response_model=ReferralAnalysisFilters)
def get_referral_analysis_filters() -> ReferralAnalysisFilters:
    # ReferredBy (2676 rows)/ReferralNames (6594 rows) were unfiltered with no
    # cap at all — capped to top 100 here like every other lookup in the app;
    # re-searched as the user types via the dedicated endpoints below.
    with get_cursor() as cursor:
        cursor.execute("SELECT TOP 100 ReferredCity FROM VW_Customer_Refferal_Dtls GROUP BY ReferredCity ORDER BY ReferredCity")
        cities = [r["ReferredCity"] for r in rows_to_dicts(cursor) if r["ReferredCity"]]
        cursor.execute("SELECT TOP 100 ReferredBy FROM VW_Customer_Refferal_Dtls GROUP BY ReferredBy ORDER BY ReferredBy")
        referred_by = [r["ReferredBy"] for r in rows_to_dicts(cursor) if r["ReferredBy"]]
        cursor.execute("SELECT TOP 100 NewCustomer FROM VW_Customer_Refferal_Dtls GROUP BY NewCustomer ORDER BY NewCustomer")
        referral_names = [r["NewCustomer"] for r in rows_to_dicts(cursor) if r["NewCustomer"]]
        cursor.execute("SELECT Orderby FROM vw_customer_Refferal_Dtls WHERE Orderby IS NOT NULL GROUP BY Orderby ORDER BY Orderby")
        order_by = [r["Orderby"] for r in rows_to_dicts(cursor) if r["Orderby"]]
    return ReferralAnalysisFilters(cities=cities, referred_by=referred_by, referral_names=referral_names, order_by=order_by)


@router.get("/referral-analysis/cities", response_model=list[str])
def get_referral_analysis_cities(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 ReferredCity FROM VW_Customer_Refferal_Dtls WHERE ReferredCity LIKE ? "
                "GROUP BY ReferredCity ORDER BY ReferredCity",
                f"%{search}%",
            )
        else:
            cursor.execute("SELECT TOP 100 ReferredCity FROM VW_Customer_Refferal_Dtls GROUP BY ReferredCity ORDER BY ReferredCity")
        return [r["ReferredCity"] for r in rows_to_dicts(cursor) if r["ReferredCity"]]


@router.get("/referral-analysis/referred-by", response_model=list[str])
def get_referral_analysis_referred_by(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["ReferredBy LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                f"SELECT TOP 100 ReferredBy FROM VW_Customer_Refferal_Dtls WHERE {where_clause} "
                "GROUP BY ReferredBy ORDER BY ReferredBy",
                *params,
            )
        else:
            cursor.execute("SELECT TOP 100 ReferredBy FROM VW_Customer_Refferal_Dtls GROUP BY ReferredBy ORDER BY ReferredBy")
        return [r["ReferredBy"] for r in rows_to_dicts(cursor) if r["ReferredBy"]]


@router.get("/referral-analysis/referral-names", response_model=list[str])
def get_referral_analysis_referral_names(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["NewCustomer LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                f"SELECT TOP 100 NewCustomer FROM VW_Customer_Refferal_Dtls WHERE {where_clause} "
                "GROUP BY NewCustomer ORDER BY NewCustomer",
                *params,
            )
        else:
            cursor.execute("SELECT TOP 100 NewCustomer FROM VW_Customer_Refferal_Dtls GROUP BY NewCustomer ORDER BY NewCustomer")
        return [r["NewCustomer"] for r in rows_to_dicts(cursor) if r["NewCustomer"]]


@router.get("/referral-analysis", response_model=list[ReferralAnalysisRow])
def get_referral_analysis_rows(
    status: int = 0, order_by: str = "", referred_by: str = "", referral_name: str = "", city: str = "",
    referral_card_no: str = "", order_no: str = "", from_date: date | None = None, to_date: date | None = None,
) -> list[ReferralAnalysisRow]:
    where = " WHERE 1 = 1"
    params: list = []
    if status == 1:
        where += " AND OrderNo > 0"
    elif status == 2:
        where += " AND OrderNo IS NULL"
    if from_date and to_date:
        where += " AND convert(datetime, EnquiryDate) BETWEEN ? AND ?"
        params += [from_date, to_date]
    if order_by:
        where += " AND Orderby = ?"
        params.append(order_by)
    if referred_by:
        where += " AND ReferredBy = ?"
        params.append(referred_by)
    if referral_name:
        where += " AND NewCustomer = ?"
        params.append(referral_name)
    if city:
        where += " AND ReferredCity = ?"
        params.append(city)
    if referral_card_no:
        where += " AND Referralcardno = ?"
        params.append(referral_card_no)
    if order_no:
        where += " AND OrderNo = ?"
        params.append(order_no)

    with get_cursor() as cursor:
        cursor.execute(f"SELECT * FROM VW_Customer_Refferal_Dtls{where} ORDER BY ReferredBy", *params)
        rows = rows_to_dicts(cursor)

    return [
        ReferralAnalysisRow(
            referred_by=r["ReferredBy"] or "", referred_cust_id=r["Referredcustid"] or 0,
            referred_city=r["ReferredCity"] or "", cust_id=r["Custid"] or 0, new_customer=r["NewCustomer"] or "",
            city=r["City"] or "", enquiry_date=r["EnquiryDate"].isoformat() if r["EnquiryDate"] else None,
            balance_amt=float(r["Balanceamt"] or 0), amount=float(r["Amount"] or 0),
            referral_card_no=r["Referralcardno"] or "", order_no=r["OrderNo"], order_date=r["OrderDate"].isoformat() if r["OrderDate"] else None,
            order_by=r["Orderby"] or "", introduced_by=r["IntrodcuedBy"] or "",
        )
        for r in rows
    ]


@router.post("/customer-call-remark/{cust_id}")
def save_call_remark(cust_id: int, remark: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Isnull(CallRemark,'') FROM CustomerAttributes WHERE CustID = ? AND AttributeID = 4 AND depID = 1",
            cust_id,
        )
        row = cursor.fetchone()
        existing = row[0] if row else ""
        new_value = f"{existing},{remark}" if existing else remark
        cursor.execute(
            "UPDATE CustomerAttributes SET CallRemark = ? WHERE CustID = ? AND AttributeID = 4 AND depID = 1",
            new_value, cust_id,
        )
    return {"success": True}


# -------------------- CRMReview / CRMReviewdetail --------------------
# Part of the report-engine follow-up task (see feedback_report_engine_simplification
# memory) — a dynamic-column report whose columns after index 2 the source
# renders as drill-down links (into EXEC CRMDetails for
# TotalCustid/CreationEntrycount/ActionEntryCount/CompletionEntryCount, or
# EXEC SynapseDetails for SynapseCallDone/CallAttempted); any other column
# name has no configured drill-down in the source and returns empty here too.
#
# Confirmed live: the CRMReview stored proc itself is a single query UNIONing
# 6 branches, 3 of which read `SynapseCDR.dbo.SIPEventRegisterCDR` — a
# database confirmed missing in this environment (same gap as
# support_callsip.py and several SupportCoOrdinatorDashboard tiles below).
# Since every UNION ALL branch has to resolve for the query to run at all,
# the proc 500s outright — there's no partial-success path, and the whole
# report was permanently blank as a result.
#
# get_crm_review_rows below bypasses the proc with the equivalent inline
# query for the proc's OTHER 3 branches (CreationEntryCount/
# ActionEntryCount/CompletionEntryCount, all backed by Web_EnquiryAction —
# no missing dependency), verified live to return identical, real data.
# SynapseCallMin/SynapseCallDone/CallAttempted are kept as always-0 columns
# (matching the proc's original column layout/order, so linkColumns slicing
# on the frontend is unaffected) since that data genuinely isn't obtainable
# here; `CRMDetails` has no such dependency and works fully against real data.


@router.get("/crm-review-users", response_model=list[LookupOption])
def get_crm_review_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("Select UserID,name from usermaster where enabled=1 and UserType in (1,6,3,5) order by name")
        return [LookupOption(label=r["name"] or "", value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/crm-review", response_model=DynamicRows)
def get_crm_review_rows(user_id: int = 0, from_date: date | None = None, to_date: date | None = None) -> DynamicRows:
    fd = from_date or date(2000, 1, 1)
    td = to_date or date.today()
    sql = """
        With a as (
          Select U.UserID, U.Name, W.Creationdate as EntryDate, Count(W.Custid) CreationEntryCount, 0 ActionEntryCount, 0 CompletionEntryCount
          from usermaster U Left Outer Join Web_EnquiryAction W on U.UserID = W.CreatedBy
          where U.UserID = ? and W.Custid <> 0 and W.CreationDate >= ? and W.CreationDate <= ?
          group by U.UserID, U.Name, W.Creationdate
          Union all
          Select U.UserID, U.Name, W.ActionDate as EntryDate, 0, Count(W.Custid), 0
          from usermaster U Left Outer Join Web_EnquiryAction W on U.UserID = W.AssignedTo
          where U.UserID = ? and W.Custid <> 0 and W.Actiondate >= ? and W.Actiondate <= ?
          group by U.UserID, U.Name, W.ActionDate
          Union all
          Select U.UserID, U.Name, W.CompletionCreationDate as EntryDate, 0, 0, Count(W.Custid)
          from usermaster U Left Outer Join Web_EnquiryAction W on U.UserID = W.completedby
          where U.UserID = ? and W.Custid <> 0 and W.CompletionCreationDate >= ? and W.CompletionCreationDate <= ?
          group by U.UserID, U.Name, W.CompletionCreationDate
        )
        select t.UserID, t.Name, convert(varchar, EntryDate, 106) CreationDate, vc.TotalCustid,
          sum(CreationEntrycount) CreationEntrycount, Sum(ActionEntryCount) ActionEntryCount, Sum(CompletionEntryCount) CompletionEntryCount,
          0 SynapseCallMin, 0 SynapseCallDone, 0 CallAttempted
        From a t inner join GSTIN_vwCustomerCRM vc on t.userid = vc.userid and t.EntryDate = vc.Date
        where EntryDate >= ? and EntryDate <= ?
        group by t.UserID, t.Name, EntryDate, vc.TotalCustid
        order by EntryDate
    """
    with get_cursor() as cursor:
        cursor.execute(sql, user_id, fd, td, user_id, fd, td, user_id, fd, td, fd, td)
        rows = rows_to_dicts(cursor)
    columns = list(rows[0].keys()) if rows else []
    return DynamicRows(columns=columns, rows=rows)


_CRM_DETAILS_STAGES = {"TotalCustid", "CreationEntrycount", "ActionEntryCount", "CompletionEntryCount"}
_SYNAPSE_DETAILS_STAGES = {"SynapseCallDone", "CallAttempted"}


@router.get("/crm-review-detail", response_model=DynamicRows)
def get_crm_review_detail_rows(
    action_stage_name: str, user_id: int = 0, from_date: date | None = None, to_date: date | None = None,
) -> DynamicRows:
    if action_stage_name in _CRM_DETAILS_STAGES:
        proc = "CRMDetails"
    elif action_stage_name in _SYNAPSE_DETAILS_STAGES:
        proc = "SynapseDetails"
    else:
        return DynamicRows(columns=[], rows=[])
    with get_cursor() as cursor:
        cursor.execute(f"EXEC {proc} ?, ?, ?, ?", user_id, from_date or "", to_date or "", action_stage_name)
        rows = rows_to_dicts(cursor)
    columns = list(rows[0].keys()) if rows else []
    return DynamicRows(columns=columns, rows=rows)
