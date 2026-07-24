"""Mirrors Section_General's ad-hoc report engine (webDashboard.aspx.vb /
ReportNew.aspx.vb / ReportViewer.aspx.vb, ~1970-2275 lines each of runtime
WebForms control generation) plus Section_Admin's CRMReview/CRMReviewdetail
and Section_Support's SupportCoOrdinatorDashboard — all deferred into this
one follow-up task as the same "config-driven ad-hoc report" family. Per
feedback_report_engine_simplification memory, rebuilt as a normal reactive
form + grid against the real Analytic_Report* metadata tables, not a
replication of the dynamic control-tree generation.

`Analytic_ReportMaster` defines each report's SelectClause/FromClause/
GroupByClause/OrderByClause (or a ProcToExec stored proc instead) and
`Analytic_ReportColumnMaster` defines its filterable columns. Drill-down
(DetailSelect/DetailFrom/...) and named filter templates
(`analytic_reportTemplatemaster`) are implemented for real too, though in
the live data only 2 of 38 reports have DrillDownEnabled=1 and both have
blank Detail* clauses (a pre-existing, never-finished feature in the
source) — drill-down degrades to an empty result when unconfigured rather
than erroring. The per-report ProcessMaster/GroupTrans/UserGroupDetail
permission gate in DynamicReports.aspx.vb's menu builder is not replicated
(same centralized-authGuard-instead-of-per-page-checks simplification used
throughout this app) — every report is listed for any logged-in user.
"""

import logging
import re
from typing import Any

import pyodbc
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/general/reports", tags=["general-reports"])


class ReportSummary(BaseModel):
    report_type_id: int
    report_name: str
    report_menu_name: str


class ReportFilterField(BaseModel):
    column_id: int
    column_name: str
    display_name: str
    data_type: str
    auto_populate: bool = False


class RunReportRequest(BaseModel):
    filters: dict[str, str] = {}


class DrillDownRequest(BaseModel):
    row: dict[str, Any]


class FilterTemplate(BaseModel):
    name: str
    filters: dict[str, str]


class SaveTemplateRequest(BaseModel):
    name: str
    filters: dict[str, str]


def _data_type(raw: str | None, column_name: str | None = None) -> str:
    raw = (raw or "").strip().lower()
    if raw in ("date", "datetime"):
        return "date"
    # Analytic_ReportColumnMaster.ColumnDataType is unreliable for date
    # columns -- confirmed live that 11 of 20 date-named filter columns
    # across the 38 reports are mistyped as 'bigint' (e.g. report 25 "SO and
    # Sale Details": ColumnName='Date', ColumnDataType='bigint'). Without
    # this, those columns fell through to "number", which meant no date
    # picker in the UI (a plain textbox instead) AND naive exact-string SQL
    # matching in _proc_filter_conditions below instead of its date-aware
    # branch -- so date filtering on these reports was silently broken, not
    # just visually. Trusting the column's own name over the bad metadata
    # fixes both at once.
    if column_name and "date" in column_name.lower():
        return "date"
    if raw in ("bigint", "int", "numeric", "float", "decimal"):
        return "number"
    return "string"


# ReportID 36 ("API Merchant Summary", Proc_GetAPIRequestData_summary) always
# 503s -- its proc reads from linked server '103.25.126.228,14189', which
# isn't configured on this SQL instance (confirmed live via sys.servers, same
# category of gap as the [License]/SynapseCDR/sharedmssql5 linked-server
# issues elsewhere in this app). Hidden from the picker rather than left
# visible-but-always-broken; re-add once that linked server exists.
_HIDDEN_REPORT_IDS = {36}


@router.get("", response_model=list[ReportSummary])
def get_reports() -> list[ReportSummary]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT RM.ReportID, RM.ReportName, ISNULL(MM.ReportMenuName, 'Other') MenuName "
            "FROM Analytic_ReportMaster RM LEFT OUTER JOIN Analytic_ReportMenuMaster MM "
            "ON RM.ReportMenuID = MM.ReportMenuID ORDER BY MenuName, RM.ReportName"
        )
        rows = rows_to_dicts(cursor)
        rows = [r for r in rows if r["ReportID"] not in _HIDDEN_REPORT_IDS]
    return [
        ReportSummary(report_type_id=r["ReportID"], report_name=r["ReportName"] or "", report_menu_name=r["MenuName"])
        for r in rows
    ]


@router.get("/{report_id}/filter-fields", response_model=list[ReportFilterField])
def get_filter_fields(report_id: int) -> list[ReportFilterField]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ColumnId, ColumnName, ISNULL(DisplayColumnName, ColumnName) DisplayName, ColumnDataType, "
            "AutoPopulate, LEN(QryForFilter) QryLen "
            "FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND IsFilter = 1 ORDER BY OrderIndex",
            report_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ReportFilterField(
            column_id=r["ColumnId"], column_name=r["ColumnName"] or "",
            display_name=r["DisplayName"] or r["ColumnName"] or "", data_type=_data_type(r["ColumnDataType"], r["ColumnName"]),
            auto_populate=bool(r["AutoPopulate"]) and (r["QryLen"] or 0) > 0,
        )
        for r in rows
    ]


# Analytic_ReportColumnMaster.QryForFilter (37 filter columns across the 38
# reports use it, e.g. Salesman/City/Customer/Co-Ordinator/LedgerGroup
# dropdowns) defines the exact "distinct value list" query the legacy
# WebForms filter control used to populate itself -- this engine never read
# it at all, so every AutoPopulate=1 filter rendered as an empty text box
# instead of a populated dropdown (reported live: Cancel Order Details'
# Salesman filter). QryForFilter is admin-authored config data (same trust
# level as SelectClause/FromClause elsewhere in this file), not user input,
# so executing it directly is consistent with how the rest of this engine
# already treats these metadata-driven queries.
@router.get("/{report_id}/filter-fields/{column_id}/options", response_model=list[str])
def get_filter_field_options(report_id: int, column_id: int) -> list[str]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT QryForFilter FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND ColumnId = ? AND AutoPopulate = 1",
            report_id, column_id,
        )
        row = cursor.fetchone()
        query = (row[0] or "").strip() if row else ""
        if not query:
            return []
        try:
            cursor.execute(query)
            values = {str(r[0]).strip() for r in cursor.fetchall() if r[0] not in (None, "")}
        except pyodbc.Error:
            logger.exception("Report %s filter-field %s options query failed", report_id, column_id)
            return []
    return sorted(values)[:500]


def _report_master(cursor, report_id: int) -> dict | None:
    cursor.execute(
        "SELECT SelectClause, FromClause, GroupByClause, OrderByClause, ProcToExec, Pagesize, "
        "DetailSelect, DetailFrom, DetailGroupBy, DetailOrderBy, DetailKey "
        "FROM Analytic_ReportMaster WHERE ReportID = ?", report_id,
    )
    rows = rows_to_dicts(cursor)
    return rows[0] if rows else None


def _build_where(cursor, report_id: int, filters: dict[str, str]) -> tuple[str, list]:
    if not filters:
        return "", []
    cursor.execute(
        "SELECT ColumnName, FieldName, ColumnDataType FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND IsFilter = 1",
        report_id,
    )
    field_by_name = {r["ColumnName"]: r for r in rows_to_dicts(cursor)}
    clauses = []
    params: list = []
    for column_name, value in filters.items():
        if not value:
            continue
        col = field_by_name.get(column_name)
        if not col:
            continue
        field = col["FieldName"] or column_name
        if _data_type(col["ColumnDataType"], col["ColumnName"]) == "string":
            clauses.append(f"{field} LIKE ?")
            params.append(f"%{value}%")
        else:
            clauses.append(f"{field} = ?")
            params.append(value)
    if not clauses:
        return "", []
    return " AND ".join(clauses), params


def _append_where(from_clause: str, where_sql: str) -> str:
    joiner = " AND " if re.search(r"\bwhere\b", from_clause, re.IGNORECASE) else " WHERE "
    return f"{from_clause}{joiner}{where_sql}"


def _has_userid_param(cursor, proc_name: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.PARAMETERS WHERE SPECIFIC_NAME = ? AND PARAMETER_NAME = '@userid'",
        proc_name,
    )
    return cursor.fetchone()[0] > 0


_UNAVAILABLE_DETAIL = "This report's data source is currently unavailable. Please contact support."

# Analytic_Proc_1stAnniversaryAMCList (ReportID 4) calls
# dbo.Fun_GetCustomerExpiry() -- a multi-statement table function that itself
# runs a per-customer correlated subquery over Udfdetail/Salesmaster (10k+
# customers, ~0.5-2.5s just to materialize) -- twice per output row, inside
# two separate correlated subqueries (NoOfTT/ReqTT), because MSTVFs can't be
# inlined/predicate-pushed by the optimizer. Confirmed live: this is the
# report's real bottleneck (small proc for a small result set, but 3-5s
# wall-clock). Rewritten here to call the function once (materialized in a
# CTE) and pre-aggregate the TT counts with a single GROUP BY instead of two
# repeated correlated calls. Verified field-for-field identical output
# against the live proc for every row before swapping this in -- same
# customer set, same NoOfTT/ReqTT/LedgerBalance/AMCPer/ASSValue/AMCAmount
# values, just faster. The production stored procedure itself is left
# untouched (still used by the legacy ASP.NET app) -- this is a
# Python-side-only substitution for this one report.
_ANNIVERSARY_AMC_PROC = "analytic_proc_1stanniversaryamclist"
_ANNIVERSARY_AMC_SQL = """
;WITH Expiry AS (
    SELECT CustId, ExpiryDate FROM dbo.Fun_GetCustomerExpiry()
),
TTAgg AS (
    SELECT TT.CustId,
           COUNT(*) AS NoOfTT,
           COUNT(DISTINCT CASE WHEN TD.WhatNext = 'Requirement' AND TT.Closed = 0 THEN TD.VoucherNo END) AS ReqTT
    FROM TTMaster TT
    INNER JOIN Expiry F ON TT.CustId = F.CustId AND TT.Date >= F.ExpiryDate
    LEFT JOIN TTDetails TD ON TT.VoucherNo = TD.VoucherNo
    GROUP BY TT.CustId
),
Eligible AS (
    SELECT cm.Custid, Cm.Displayname AS Customer, Cm.MobileNo, Cm.Email, cm.City,
           sm.Voucherno, Sm.date AS InvoiceDate,
           VAMC.AMCDueDate AS ExpiryDate, VAMC.Ledgerbalance AS LedgerBalance,
           VAMC.AMCPer, VAMC.ASSValue, VAMC.AMCAmount
    FROM Salesmaster SM
    INNER JOIN Salesdetail sd ON sm.Voucherno = sd.Voucherno
    INNER JOIN Customermaster cm ON Sm.Custid = cm.Custid
    INNER JOIN Products P ON P.ProductID = sd.ProductID
    INNER JOIN VWAMCForAllCustomers VAMC ON Sm.custid = VAMC.Custid
    WHERE P.ProductName IN ('CENTRALIZED DATA CONSOLIDATION ON REPORTS (ONLY FOR HO)','RETAILWARE WITH REPORTS FOR SINGLE USER','RETAILWARE FOR F&B WITH REPORTS[SINGLE USER]')
      AND Cm.Disabled = 0 AND cm.groupid = 1
      AND sm.date BETWEEN CONVERT(date, DATEADD(month, -13, GETDATE())) AND CONVERT(date, DATEADD(month, -11, GETDATE()))
      AND NOT EXISTS (
            SELECT 1 FROM customerattributes ca
            WHERE ca.custid = cm.custid AND ca.attributeid = 29 AND ca.attrvalue <> ''
      )
    GROUP BY cm.Custid, Cm.Displayname, cm.City, Sm.Date, sm.Voucherno, Cm.MobileNo, Cm.Email,
             VAMC.AMCPer, VAMC.ASSValue, VAMC.AMCAmount, VAMC.AMCDueDate, VAMC.Ledgerbalance
)
SELECT E.*,
       ISNULL(TTA.NoOfTT, 0) AS NoOfTT,
       ISNULL(TTA.ReqTT, 0) AS ReqTT,
       (SELECT TOP 1 date FROM AMCIssueMaster WHERE Custid = E.Custid ORDER BY date DESC) AS LastLetterSentDate
FROM Eligible E
LEFT JOIN TTAgg TTA ON TTA.CustId = E.Custid
ORDER BY E.ExpiryDate
"""

# Analytic_Proc_NonAMCCustomerList (ReportID 3, "Non AMC Customer List") has
# the exact same Fun_GetCustomerExpiry()-called-twice-per-row bottleneck as
# the anniversary report above, plus a genuinely broken filter: it has a
# "City" filter configured in Analytic_ReportColumnMaster, but the proc
# itself is parameterless (confirmed via INFORMATION_SCHEMA.PARAMETERS) and
# only reads filter conditions back out of the Analytic_ReportFilterQuery
# table -- which this generic engine never wrote to, so City was silently
# ignored no matter what the user typed. Rewritten the same way as the
# anniversary report (single materialization of the expiry function) with
# the City filter applied as a normal parameterized clause instead of
# routing through that stateful table. Verified field-for-field identical
# to the live proc's output (14/14 rows, no mismatches) before swapping in,
# and confirmed the City filter actually narrows results afterward.
_NON_AMC_PROC = "analytic_proc_nonamccustomerlist"
_NON_AMC_SQL_TEMPLATE = """
;WITH Expiry AS (
    SELECT CustId, ExpiryDate FROM dbo.Fun_GetCustomerExpiry()
),
TTAgg AS (
    SELECT TT.CustId,
           COUNT(*) AS NoOfTT,
           COUNT(DISTINCT CASE WHEN TD.WhatNext = 'Requirement' AND TT.Closed = 0 THEN TD.VoucherNo END) AS ReqTT
    FROM TTMaster TT
    INNER JOIN Expiry F ON TT.CustId = F.CustId AND TT.Date >= F.ExpiryDate
    LEFT JOIN TTDetails TD ON TT.VoucherNo = TD.VoucherNo
    GROUP BY TT.CustId
),
Eligible AS (
    SELECT cm.Custid, Cm.Displayname AS Customer, Cm.MobileNo, Cm.Email, cm.City,
           Sm.date AS InvoiceDate, VAMC.AMCDueDate AS ExpiryDate,
           VAMC.AMCPer, (VAMC.Assvalue - VAMC.SRAssvalue) AS AssValue, VAMC.AMCAmount,
           VAMC.Ledgerbalance AS LedgerBalance
    FROM Salesmaster SM
    INNER JOIN Salesdetail sd ON sm.Voucherno = sd.Voucherno
    INNER JOIN Customermaster cm ON Sm.Custid = cm.Custid
    INNER JOIN Products P ON P.ProductID = sd.ProductID
    INNER JOIN VWAMCForAllCustomers VAMC ON Sm.custid = VAMC.Custid
    WHERE P.ProductName IN ('CENTRALIZED DATA CONSOLIDATION ON REPORTS (ONLY FOR HO)','RETAILWARE WITH REPORTS FOR SINGLE USER','RETAILWARE FOR F&B WITH REPORTS[SINGLE USER]')
      AND Sm.Custid NOT IN (
            SELECT DISTINCT sm1.Custid FROM Salesmaster sm1
            INNER JOIN Salesdetail sd1 ON Sm1.Voucherno = sd1.Voucherno
            WHERE sd1.ProductID IN (490, 491)
      )
      AND Cm.Disabled = 0 AND cm.groupid = 1
      {city_filter}
      AND sm.date NOT BETWEEN (
            SELECT CASE WHEN MONTH(Billdate) > 3
                        THEN CAST(CAST(YEAR(Billdate) AS varchar) + '-04-01' AS date)
                        ELSE CAST(CAST(YEAR(Billdate) - 1 AS varchar) + '-04-01' AS date) END
            FROM Options
      ) AND (
            SELECT CASE WHEN MONTH(Billdate) > 3
                        THEN CAST(CAST(YEAR(Billdate) + 1 AS varchar) + '-03-31' AS date)
                        ELSE CAST(CAST(YEAR(Billdate) AS varchar) + '-03-31' AS date) END
            FROM Options
      )
      AND VAMC.AMCDueDate < (SELECT Billdate FROM Options)
    GROUP BY cm.Custid, Cm.Displayname, Sm.Date, cm.City, Cm.MobileNo, Cm.Email,
             VAMC.Ledgerbalance, VAMC.AMCDueDate, VAMC.AMCPer, VAMC.Assvalue, VAMC.SRAssvalue, VAMC.AMCAmount
)
SELECT E.*,
       ISNULL(TTA.NoOfTT, 0) AS NoOfTT,
       ISNULL(TTA.ReqTT, 0) AS ReqTT,
       (SELECT TOP 1 date FROM AMCIssueMaster WHERE Custid = E.Custid ORDER BY date DESC) AS LastLetterSentDate
FROM Eligible E
LEFT JOIN TTAgg TTA ON TTA.CustId = E.Custid
ORDER BY E.ExpiryDate ASC
"""


def _run_non_amc_customer_list(cursor, filters: dict[str, str]) -> list[dict]:
    city = (filters.get("City") or "").strip()
    if city:
        cursor.execute(_NON_AMC_SQL_TEMPLATE.format(city_filter="AND cm.City LIKE ?"), f"%{city}%")
    else:
        cursor.execute(_NON_AMC_SQL_TEMPLATE.format(city_filter=""))
    return rows_to_dicts(cursor)


# Proc_ActivityStatus_Report (ReportID 19, "ActivityStatus") builds its
# result (a cross-source feed of same-day cash/cheque Advance/Receipt/
# Sale/Order/BankReceipt entries) via `select * into Temp_Proc_ActivityStatus_Report
# from (...)`  -- a real, permanent table it drops and recreates on every
# run, not a session-scoped #temp table -- then does one more SELECT off
# of that table to format Amount/Voucherno for display and append a
# "ZZZTotal" summary row. Custid exists on the intermediate table but is
# dropped from that final formatting SELECT, so every row this report
# returns has no customer identifier at all -- the "Details" button's
# generic Custid-based fallback (see _row_cust_id/_customer_detail_rows)
# had nothing to key off of and always showed empty.
#
# Rather than re-deriving this proc's 6-way UNION (Advance/Receipts/
# Salesmaster/CustOrderMaster/PaymentDetails/PaymentRegister) by hand, this
# reads the proc's own leftover Temp_Proc_ActivityStatus_Report table right
# after EXEC and joins the Custid back onto each returned row by
# (Type, Voucherno) -- confirmed live that every (Type, Voucherno) group in
# that table maps to exactly one Custid (multiple rows only occur when one
# voucher was paid across several instruments, e.g. part-cash/part-cheque,
# and they all agree on the same customer), so this join-back is exact, not
# a best-effort guess.
def _activity_status_custid_map(cursor) -> dict[tuple[str, str], int]:
    cursor.execute("SELECT Type, Voucherno, custid FROM Temp_Proc_ActivityStatus_Report")
    return {(str(t or ""), str(v or "")): c for t, v, c in cursor.fetchall()}


def _run_activity_status_report(cursor, proc: str) -> list[dict]:
    cursor.execute(f"EXEC {proc}")
    rows = rows_to_dicts(cursor)
    custid_by_key = _activity_status_custid_map(cursor)
    for row in rows:
        custid = custid_by_key.get((str(row.get("Type") or ""), str(row.get("Voucherno") or "")))
        if custid is not None:
            row["Custid"] = custid
    return rows


_ACTIVITY_STATUS_PROC = "proc_activitystatus_report"


# WebProc_HO_AMC_By_USerwise (ReportID 7, "HO AMC Followup Report") and
# Analytic_Proc_NonAMCCustomerList both belong to a family of legacy procs
# that don't accept SQL parameters at all -- instead they read their filter
# conditions back out of the Analytic_ReportFilterQuery table (matched by
# ProcToExec name), which the *web app* is expected to populate by
# delete-then-insert just before calling the proc (confirmed by reading
# ReportNew.aspx.vb/ReportViewer.aspx.vb's FillGridView, which does exactly
# that on every "Show"/filter-apply click). NonAMCCustomerList was
# rewritten above to sidestep this entirely with a real parameterized
# query, but WebProc_HO_AMC_By_USerwise's source view (VW_ForHOAMCUserwise)
# is too deep to safely re-derive here, so this replicates the actual
# handshake instead: write the current filter values into
# Analytic_ReportFilterQuery, run the proc, then delete them again.
#
# This also fixes a confirmed live data-integrity bug: report 7 had two
# contradictory leftover rows in that table from 2015/2018 testing
# (`ExpiryDate Between '01-Apr-2006' And '30-Nov-2015'` AND, separately,
# `ExpiryDate='01-Apr-2018'`, ANDed together by the proc's own cursor loop
# since it matches on proc name only, not report/user) -- an impossible
# combination that has been silently zeroing out every run of this report,
# including in the live legacy app, since nothing ever cleaned those rows
# up. Clearing the table before every run permanently fixes that.
_PROC_FILTER_REPORTS = {"webproc_ho_amc_by_userwise"}


def _proc_filter_conditions(cursor, report_id: int, filters: dict[str, str]) -> list[tuple[int, str, str]]:
    if not filters:
        return []
    cursor.execute(
        "SELECT ColumnId, ColumnName, FieldName, ColumnDataType FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND IsFilter = 1",
        report_id,
    )
    field_by_name = {r["ColumnName"]: r for r in rows_to_dicts(cursor)}
    conditions = []
    for column_name, value in filters.items():
        if not value:
            continue
        col = field_by_name.get(column_name)
        if not col:
            continue
        field = col["FieldName"] or column_name
        escaped = value.replace("'", "''")
        if _data_type(col["ColumnDataType"], col["ColumnName"]) == "date":
            date_part = escaped[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", escaped) else escaped
            sql = f"({field} = '{date_part}')"
        elif _data_type(col["ColumnDataType"], col["ColumnName"]) == "string":
            sql = f"({field} LIKE '%{escaped}%')"
        else:
            sql = f"({field} = '{escaped}')"
        conditions.append((col["ColumnId"], sql, value))
    return conditions


def _run_proc_with_filters(cursor, report_id: int, proc: str, filters: dict[str, str], user_id: int) -> list[dict]:
    conditions = _proc_filter_conditions(cursor, report_id, filters)
    cursor.execute("DELETE FROM Analytic_ReportFilterQuery WHERE Reportid = ?", report_id)
    for column_id, condition_sql, raw_value in conditions:
        cursor.execute(
            "INSERT INTO Analytic_ReportFilterQuery (Reportid, Columnid, Value1, Enabled, Query, Condition, userid, IsDashboard) "
            "VALUES (?, ?, ?, 1, ?, 'Is Exactly', ?, 0)",
            report_id, column_id, raw_value, condition_sql, user_id,
        )
    try:
        cursor.execute(f"EXEC {proc}")
        return rows_to_dicts(cursor)
    finally:
        cursor.execute("DELETE FROM Analytic_ReportFilterQuery WHERE Reportid = ?", report_id)


# Proc_AVGSalesRevenue (ReportID 37, "AVG Annual Sales Revenue") has the
# same two problems already fixed above for other legacy procs:
#
# 1. It's parameterless and reads its SoftwareName filter back out of
#    Analytic_ReportFilterQuery -- and a stale leftover row from a past
#    session (`SoftwareName='JEWELSOFT'`) was silently restricting every
#    run (this app's AND the live legacy app's) to just 142 of the real
#    405 matching rows. Fixed the same way as WebProc_HO_AMC_By_USerwise:
#    write-then-exec-then-cleanup via _run_proc_with_filters, which also
#    clears the stale row.
# 2. Its output (softwarename, Customer, [Avg Yearly], TTPending) drops
#    the identifying key entirely -- some rows are one real customer
#    (Custid, sourced from vwAjit_CustomerSalesAverageYearly) and others
#    are a whole ledger group of customers aggregated together
#    (LedgerGroupId, sourced from vwAjit_CustomerGroupSalesAverageYearly)
#    -- so "Details" had nothing to key off of and always came back
#    empty. Rather than re-deriving the proc's revenue-aggregation math by
#    hand, this reads Custid/LedgerGroupId back from those same two
#    source views (which the proc's underlying vwAjit_HNICustomerTT is
#    directly built from) and joins by (Customer, SoftwareName,
#    [Avg Yearly]) -- confirmed live that this triple is a unique key in
#    both source views (2333/2333 and 386/386 rows respectively, zero
#    collisions), so the join-back is exact, not a best-effort guess.
_AVG_SALES_REVENUE_PROC = "proc_avgsalesrevenue"


def _avg_sales_revenue_lookup(cursor) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str, str], int]]:
    cursor.execute("SELECT custid, Displayname, Softwarename, [Avg Yearly] FROM vwAjit_CustomerSalesAverageYearly")
    individual = {(str(name or ""), str(sw or ""), str(avg)): cid for cid, name, sw, avg in cursor.fetchall()}
    cursor.execute("SELECT LedgerGroupId, Name, Softwarename, [Avg Yearly] FROM vwAjit_CustomerGroupSalesAverageYearly")
    groups = {(str(name or ""), str(sw or ""), str(avg)): gid for gid, name, sw, avg in cursor.fetchall()}
    return individual, groups


def _run_avg_sales_revenue(cursor, report_id: int, proc: str, filters: dict[str, str], user_id: int) -> list[dict]:
    rows = _run_proc_with_filters(cursor, report_id, proc, filters, user_id)
    individual, groups = _avg_sales_revenue_lookup(cursor)
    for row in rows:
        key = (str(row.get("Customer") or ""), str(row.get("softwarename") or ""), str(row.get("Avg Yearly")))
        cid = individual.get(key)
        if cid is not None:
            row["Custid"] = cid
            continue
        gid = groups.get(key)
        if gid is not None:
            row["LedgerGroupId"] = gid
    return rows


# Proc_CancelOrderDetails (ReportID 29, "Cancel Order Details") has the same
# two problems already fixed above:
#
# 1. Parameterless, reads OrderDate/Salesman filters back out of
#    Analytic_ReportFilterQuery -- two stale leftover rows
#    (`OrderDate Between '01-Apr-25' and '08-Aug-25'` and
#    `Slm.Salesmanid='4'`, i.e. Atish Vanjare) were silently restricting
#    every run to that one salesman and date window. The Salesman filter
#    also needs special handling: its FieldName (Slm.Salesmanid) is a
#    numeric id column, but the filter UI collects the salesman's *name* --
#    the one real historical filter row already shows the legacy app
#    resolving name to id before writing the condition (`Salesmanid='4'`
#    for 'ATISH VANJARE'), so this does the same lookup rather than
#    building a LIKE match against an int column (which would silently
#    match nothing, or worse, match the wrong id by numeric substring).
#    OrderDate is date-range-only in this proc (it only extracts
#    From/To when Condition='Between', which this engine's single-date
#    filter input can't produce) -- an OrderDate filter attempt here is a
#    no-op (falls back to the proc's own "today" default) rather than
#    silently applying a wrong condition; still strictly better than the
#    stale hard-coded 2025 window it was permanently stuck on before.
# 2. Its 4-way UNION (Cancelled Order/Closed Order/Order Refund from
#    CustOrderMaster, Sales Return from SalesReturnmaster) drops Custid
#    from the final output, so "Details" had nothing to key off. Unlike
#    the AVG-sales-revenue report this needs no fuzzy text matching --
#    "Orderno" is CustOrderMaster's real primary key for the first three
#    Types, and SalesReturnmaster's real primary key (a distinct id
#    space) for "Sales Return" -- confirmed live both are exactly 1:1
#    unique (16150/16150 and 1178/1178 rows), so joining straight back on
#    that key is exact.
_CANCEL_ORDER_PROC = "proc_cancelorderdetails"


def _cancel_order_filter_conditions(cursor, report_id: int, filters: dict[str, str]) -> list[tuple[int, str, str]]:
    if not filters:
        return []
    cursor.execute(
        "SELECT ColumnId, ColumnName, FieldName, ColumnDataType FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND IsFilter = 1",
        report_id,
    )
    field_by_name = {r["ColumnName"]: r for r in rows_to_dicts(cursor)}
    conditions: list[tuple[int, str, str]] = []
    for column_name, value in filters.items():
        if not value:
            continue
        col = field_by_name.get(column_name)
        if not col:
            continue
        if column_name == "Salesman":
            cursor.execute("SELECT SalesmanId FROM Salesman WHERE Name = ?", value)
            found = cursor.fetchone()
            if found is None:
                continue
            conditions.append((col["ColumnId"], f"({col['FieldName']}={found[0]})", value))
            continue
        field = col["FieldName"] or column_name
        escaped = value.replace("'", "''")
        if _data_type(col["ColumnDataType"], col["ColumnName"]) == "string":
            conditions.append((col["ColumnId"], f"({field} LIKE '%{escaped}%')", value))
        else:
            conditions.append((col["ColumnId"], f"({field} = '{escaped}')", value))
    return conditions


def _cancel_order_custid_lookup(cursor, order_nos: set[int], voucher_nos: set[int]) -> tuple[dict[int, int], dict[int, int]]:
    order_map: dict[int, int] = {}
    if order_nos:
        placeholders = ",".join("?" for _ in order_nos)
        cursor.execute(f"SELECT OrderNo, Custid FROM CustOrderMaster WHERE OrderNo IN ({placeholders})", *order_nos)
        order_map = dict(cursor.fetchall())
    voucher_map: dict[int, int] = {}
    if voucher_nos:
        placeholders = ",".join("?" for _ in voucher_nos)
        cursor.execute(f"SELECT Voucherno, Custid FROM SalesReturnmaster WHERE Voucherno IN ({placeholders})", *voucher_nos)
        voucher_map = dict(cursor.fetchall())
    return order_map, voucher_map


def _run_cancel_order_details(cursor, report_id: int, proc: str, filters: dict[str, str], user_id: int) -> list[dict]:
    conditions = _cancel_order_filter_conditions(cursor, report_id, filters)
    cursor.execute("DELETE FROM Analytic_ReportFilterQuery WHERE Reportid = ?", report_id)
    for column_id, condition_sql, raw_value in conditions:
        cursor.execute(
            "INSERT INTO Analytic_ReportFilterQuery (Reportid, Columnid, Value1, Enabled, Query, Condition, userid, IsDashboard) "
            "VALUES (?, ?, ?, 1, ?, 'Is Exactly', ?, 0)",
            report_id, column_id, raw_value, condition_sql, user_id,
        )
    try:
        cursor.execute(f"EXEC {proc}")
        rows = rows_to_dicts(cursor)
    finally:
        cursor.execute("DELETE FROM Analytic_ReportFilterQuery WHERE Reportid = ?", report_id)

    order_nos = {
        int(r["Orderno"]) for r in rows
        if r.get("Type") in ("Cancelled Order", "Closed Order", "Order Refund") and r.get("Orderno") is not None
    }
    voucher_nos = {int(r["Orderno"]) for r in rows if r.get("Type") == "Sales Return" and r.get("Orderno") is not None}
    order_map, voucher_map = _cancel_order_custid_lookup(cursor, order_nos, voucher_nos)
    for row in rows:
        orderno = row.get("Orderno")
        if orderno is None:
            continue
        cid = voucher_map.get(int(orderno)) if row.get("Type") == "Sales Return" else order_map.get(int(orderno))
        if cid is not None:
            row["Custid"] = cid
    return rows


@router.post("/{report_id}/run")
def run_report(report_id: int, body: RunReportRequest, current_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    with get_cursor() as cursor:
        master = _report_master(cursor, report_id)
        if not master:
            return []
        # Analytic_ReportMaster.Pagesize is a legacy GridView per-page-size
        # setting (often as small as 50), not a "how much data exists" cap --
        # using it directly as a hard result-set truncation was silently
        # dropping the vast majority of some reports' rows (confirmed live:
        # AMC30D-PendingTT had 179 matching rows, "AMC followup Pending
        # Actions" had 5642, both cut down to 50). Floored to a real minimum
        # so a small legacy Pagesize can no longer starve a report, while
        # still capping at 1000 as the deliberate safety ceiling this engine
        # already uses for genuinely unbounded reports.
        page_size = min(max(master["Pagesize"] or 500, 500), 1000)
        proc = (master["ProcToExec"] or "").strip()
        if proc.lower() == _ANNIVERSARY_AMC_PROC:
            try:
                cursor.execute(_ANNIVERSARY_AMC_SQL)
                rows = rows_to_dicts(cursor)
            except pyodbc.Error:
                logger.exception("Report %s (optimized anniversary-amc query) failed", report_id)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc.lower() == _NON_AMC_PROC:
            try:
                rows = _run_non_amc_customer_list(cursor, body.filters)
            except pyodbc.Error:
                logger.exception("Report %s (optimized non-amc-list query) failed", report_id)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc.lower() in _PROC_FILTER_REPORTS:
            try:
                rows = _run_proc_with_filters(cursor, report_id, proc, body.filters, current_user.user_id)
            except pyodbc.Error:
                logger.exception("Report %s (proc=%s, filtered) failed", report_id, proc)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc.lower() == _ACTIVITY_STATUS_PROC:
            try:
                rows = _run_activity_status_report(cursor, proc)
            except pyodbc.Error:
                logger.exception("Report %s (activity-status, custid join-back) failed", report_id)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc.lower() == _AVG_SALES_REVENUE_PROC:
            try:
                rows = _run_avg_sales_revenue(cursor, report_id, proc, body.filters, current_user.user_id)
            except pyodbc.Error:
                logger.exception("Report %s (avg-sales-revenue, filtered+join-back) failed", report_id)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc.lower() == _CANCEL_ORDER_PROC:
            try:
                rows = _run_cancel_order_details(cursor, report_id, proc, body.filters, current_user.user_id)
            except pyodbc.Error:
                logger.exception("Report %s (cancel-order-details, filtered+join-back) failed", report_id)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return rows[:page_size]
        if proc:
            try:
                if _has_userid_param(cursor, proc):
                    cursor.execute(f"EXEC {proc} @userid = ?", current_user.user_id)
                else:
                    cursor.execute(f"EXEC {proc}")
                rows = rows_to_dicts(cursor)
            except pyodbc.Error:
                logger.exception("Report %s (proc=%s) failed", report_id, proc)
                raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            # Same Pagesize/1000-row cap the ad-hoc SQL path below applies via
            # SELECT TOP — a stored proc can't be wrapped in TOP, so the cap is
            # applied to the fetched result instead. Without this, procs with no
            # internal row limit (most of them) return every matching row
            # unbounded, which is what was making some reports take 20+ seconds
            # and return tens of thousands of rows the browser then has to render.
            return rows[:page_size]

        select_clause = (master["SelectClause"] or "").strip()
        from_clause = (master["FromClause"] or "").strip()
        if not select_clause or not from_clause:
            return []
        where_sql, params = _build_where(cursor, report_id, body.filters)
        if where_sql:
            from_clause = _append_where(from_clause, where_sql)
        select_clause = re.sub(r"^select\b", f"SELECT TOP {page_size}", select_clause, count=1, flags=re.IGNORECASE)
        sql = f"{select_clause} {from_clause} {master['GroupByClause'] or ''} {master['OrderByClause'] or ''}"
        try:
            cursor.execute(sql, *params)
            return rows_to_dicts(cursor)
        except pyodbc.Error:
            logger.exception("Report %s (ad-hoc SQL) failed", report_id)
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)


def _row_cust_id(row: dict[str, Any]) -> int | None:
    for key, value in row.items():
        if key.strip().lower() == "custid" and value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _customer_detail_rows(cursor, cust_id: int) -> list[dict]:
    """Real customer-detail lookup, same query as CustomerInfo.aspx's popup
    (see admin_customers.py's get_customer_info) -- used as the "Details"
    button's fallback for any report row that carries a Custid but has no
    DetailSelect/DetailFrom configured in Analytic_ReportMaster (36 of the
    38 reports, including 1st Anniversary AMC List -- a pre-existing gap in
    the source's own metadata, not something broken by this migration).
    Without this, "Details" always returned an empty list and the dialog
    showed "No drill-down data configured", indistinguishable from a
    customer that genuinely has no further info."""
    cursor.execute(
        "SELECT C.CustID, G.Name AS GroupName, C.Displayname AS CustomerName, "
        "FlatNo + ' ' + Floor + ' ' + BldgName + ' ' + Road + ' ' + Area + ', ' + City AS Address, "
        "Landmark AS ContactPerson, C.MobileNo, C.PhNo, C.Email FROM CustomerMaster C "
        "INNER JOIN CustomerGroupmaster G ON C.GroupID = G.GroupID WHERE C.CustID = ?",
        cust_id,
    )
    return rows_to_dicts(cursor)


def _row_ledger_group_id(row: dict[str, Any]) -> int | None:
    for key, value in row.items():
        if key.strip().lower() == "ledgergroupid" and value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _ledger_group_member_rows(cursor, ledger_group_id: int) -> list[dict]:
    """Fallback for rows that represent a whole ledger group rather than one
    customer (see _run_avg_sales_revenue) -- there's no single customer to
    show, so "Details" lists the group's real member customers instead of
    always coming back empty."""
    cursor.execute(
        "SELECT CustID, Displayname AS CustomerName, MobileNo, Email, City "
        "FROM CustomerMaster WHERE LedgerGroupId = ? AND Disabled = 0 ORDER BY Displayname",
        ledger_group_id,
    )
    return rows_to_dicts(cursor)


# Proc_UserwisePendingTT (ReportID 18, "Userwise PendingTT") groups by
# Username (a UNION of UserMaster.Name via TT.LatestOwnerID for the
# PendingTTCount/age-bucket columns, and Salesman.Name via TT.ReceivedBy
# for the separate TTEnterred column -- two different people-tables merged
# by matching display name), so there's no Custid/LedgerGroupId on these
# rows at all -- "Details" had nothing to key off of. What's actually
# useful here is the literal list of that user's still-open tickets making
# up the PendingTTCount/bucket totals, not a customer profile. Verified
# live that this exact WHERE (mirroring the proc's own PendingTTCount
# filter conditions, including UM.Enabled=1 -- a user with a disabled
# UserMaster account can still own an open ticket, but the proc's own
# branch-1 join requires Enabled=1 so it's excluded from PendingTTCount
# too; omitting that condition here caused a real mismatch during
# testing) returns precisely the same count as the row it's drilling
# into (32 rows for a row showing PendingTTCount=32, verified across 5
# different users), so this is an exact reconstruction, not an
# approximation. Rows whose Username only ever came from the
# TTEnterred/Salesman branch (no enabled UserMaster owner) correctly show
# no open tickets -- there genuinely aren't any counted -- so "Details"
# for those legitimately shows empty.
_USERWISE_PENDING_TT_REPORT_ID = 18


def _userwise_pending_tt_details(cursor, username: str) -> list[dict]:
    cursor.execute(
        "SELECT TT.VoucherNo, TT.Date, CM.Displayname AS Customer, CM.MobileNo, CM.City, "
        "DATEDIFF(day, TT.Date, (SELECT Billdate FROM Options)) AS DaysPending "
        "FROM TTMaster TT "
        "INNER JOIN UserMaster UM ON TT.LatestOwnerID = UM.UserID "
        "INNER JOIN CustomerMaster CM ON CM.CustID = TT.CustID "
        "WHERE UM.Name = ? AND UM.Enabled = 1 AND TT.Closed = 0 AND TT.CustID > 0 AND CM.Disabled = 0 "
        "ORDER BY TT.Date",
        username,
    )
    return rows_to_dicts(cursor)


@router.post("/{report_id}/drill-down")
def drill_down(report_id: int, body: DrillDownRequest) -> list[dict]:
    with get_cursor() as cursor:
        master = _report_master(cursor, report_id)
        if not master:
            return []
        detail_select = (master["DetailSelect"] or "").strip()
        detail_from = (master["DetailFrom"] or "").strip()
        detail_key = (master["DetailKey"] or "").strip()
        if not detail_select or not detail_from:
            if report_id == _USERWISE_PENDING_TT_REPORT_ID:
                username = body.row.get("Username")
                if not username:
                    return []
                try:
                    return _userwise_pending_tt_details(cursor, str(username))
                except pyodbc.Error:
                    logger.exception("Report %s userwise-pending-tt fallback failed", report_id)
                    raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            cust_id = _row_cust_id(body.row)
            if cust_id is not None:
                try:
                    return _customer_detail_rows(cursor, cust_id)
                except pyodbc.Error:
                    logger.exception("Report %s customer-detail fallback failed", report_id)
                    raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            ledger_group_id = _row_ledger_group_id(body.row)
            if ledger_group_id is not None:
                try:
                    return _ledger_group_member_rows(cursor, ledger_group_id)
                except pyodbc.Error:
                    logger.exception("Report %s ledger-group fallback failed", report_id)
                    raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
            return []
        params: list = []
        if detail_key and detail_key in body.row:
            detail_from = _append_where(detail_from, f"{detail_key} = ?")
            params.append(body.row[detail_key])
        sql = f"{detail_select} {detail_from} {master['DetailGroupBy'] or ''} {master['DetailOrderBy'] or ''}"
        try:
            cursor.execute(sql, *params)
            return rows_to_dicts(cursor)
        except pyodbc.Error:
            logger.exception("Report %s drill-down failed", report_id)
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)


@router.get("/{report_id}/templates", response_model=list[FilterTemplate])
def get_templates(report_id: int, current_user: CurrentUser = Depends(get_current_user)) -> list[FilterTemplate]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TemplateID, TemplateName, Field, Value FROM analytic_reportTemplatemaster "
            "WHERE Reportid = ? AND userid = ? ORDER BY TemplateID",
            report_id, current_user.user_id,
        )
        rows = rows_to_dicts(cursor)
    templates: dict[int, FilterTemplate] = {}
    for r in rows:
        t = templates.setdefault(r["TemplateID"], FilterTemplate(name=r["TemplateName"] or "", filters={}))
        t.filters[r["Field"]] = r["Value"] or ""
    return list(templates.values())


@router.post("/{report_id}/templates")
def save_template(report_id: int, body: SaveTemplateRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ColumnId, ColumnName FROM Analytic_ReportColumnMaster WHERE Reportid = ? AND IsFilter = 1",
            report_id,
        )
        col_by_name = {r["ColumnName"]: r["ColumnId"] for r in rows_to_dicts(cursor)}
        cursor.execute(
            "DELETE FROM analytic_reportTemplatemaster WHERE Reportid = ? AND userid = ? AND TemplateName = ?",
            report_id, current_user.user_id, body.name,
        )
        cursor.execute("SELECT ISNULL(MAX(TemplateID), 0) + 1 FROM analytic_reportTemplatemaster")
        new_id = cursor.fetchone()[0]
        for field, value in body.filters.items():
            if not value:
                continue
            column_id = col_by_name.get(field, 0)
            cursor.execute(
                "INSERT INTO analytic_reportTemplatemaster "
                "(Reportid, Columnid, Field, Value, Enabled, Query, Condition, TemplateID, TemplateName, userid) "
                "VALUES (?, ?, ?, ?, 1, '', '', ?, ?, ?)",
                report_id, column_id, field, value, new_id, body.name, current_user.user_id,
            )
    return {"success": True}


@router.delete("/{report_id}/templates/{name}")
def remove_template(report_id: int, name: str, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM analytic_reportTemplatemaster WHERE Reportid = ? AND userid = ? AND TemplateName = ?",
            report_id, current_user.user_id, name,
        )
    return {"success": True}
