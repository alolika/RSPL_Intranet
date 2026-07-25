"""Mirrors Section_Support/TTRegister.aspx.vb (VW_TT_Report /
VW_TT_And_Action_Report views) and TroubleTicket_Modify.aspx.vb ("Pending TT
Register" — inline-editable Priority/InternalDeadLine grid). The source's
per-row Edit-button gating in TroubleTicket_Modify (enabled only for a
user's reporting-chain seniors over a pending ticket's current owner, via
GetParentEmpid) is not replicated — already simplified to always-editable at
the Angular layer, see that component's doc comment.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/support", tags=["support-tt-register"])


class LookupOption(BaseModel):
    label: str
    value: str


class UserOption(BaseModel):
    label: str
    value: int


class FilterOptions(BaseModel):
    products: list[LookupOption]
    action_taken_by: list[LookupOption]
    received_by: list[LookupOption]
    latest_owner: list[LookupOption]
    what_next: list[LookupOption]


class TtRegisterRow(BaseModel):
    voucher_no: int
    customer: str
    called_by: str
    date: str | None
    latest_owner: str
    what_next: str
    issue: str
    action_date: str | None
    action_taken_by: str
    action_details: str
    action_on: str | None
    tt_entered_by: str
    next_person: str
    due_days_by_tt: int
    due_days_by_action: int


class TtDetailRow(BaseModel):
    voucher_no: int
    product_name: str
    issue: str
    action: str
    what_next: str
    called_by: str
    closing_date: str
    closing_time: str


class OwnerHistoryRow(BaseModel):
    sr_no: int
    previous_owner: str
    date: str | None
    latest_owner: str


class TtModifyFilters(BaseModel):
    products: list[str]
    users: list[UserOption]


class TtModifyRow(BaseModel):
    tt_no: int
    name: str
    date: str | None
    description: str
    rw_remarks: str
    latest_owner: str
    priority: str
    internal_dead_line: str | None
    product_name: str
    status: str
    internal_reference_user: str


class UpdateTtModifyRequest(BaseModel):
    priority: str
    internal_dead_line: date | None


@router.get("/tt-register/filter-options", response_model=FilterOptions)
def get_filter_options() -> FilterOptions:
    # Customer used to be its own TOP-500 raw list here with no server-side
    # search — the frontend now reuses the shared /support/customers picker
    # (top-100 + type-to-search, already used by Trouble Ticket and Pending
    # TT Register) instead of duplicating a second, worse implementation.
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT AttrValue FROM CustomerAttributes CA INNER JOIN CustomerMaster Cm ON Ca.Custid = Cm.Custid "
            "WHERE Cm.Disabled = 0 AND Ca.attributeid = 1 AND Ca.depid = 1 AND AttrValue <> '' "
            "AND Ca.Custid IN (SELECT DISTINCT custid FROM Salesmaster) ORDER BY AttrValue"
        )
        products = [LookupOption(label=r["AttrValue"], value=r["AttrValue"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT Name FROM UserMaster um INNER JOIN TTActionMaster T ON Um.UserID = T.ActionUserID "
            "WHERE Enabled = 1 ORDER BY Name"
        )
        action_taken_by = [LookupOption(label=r["Name"], value=r["Name"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT Name FROM Salesman um INNER JOIN TTMaster T ON Um.Salesmanid = T.ReceivedBy "
            "WHERE Enabled = 1 ORDER BY Name"
        )
        received_by = [LookupOption(label=r["Name"], value=r["Name"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DISTINCT Name FROM UserMaster um INNER JOIN TTMaster T ON Um.UserID = T.LatestOwnerID "
            "WHERE Enabled = 1 ORDER BY Name"
        )
        latest_owner = [LookupOption(label=r["Name"], value=r["Name"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT DISTINCT WhatNExt FROM TTDetails WHERE WhatNext <> '' ORDER BY WhatNExt")
        what_next = [LookupOption(label=r["WhatNExt"], value=r["WhatNExt"]) for r in rows_to_dicts(cursor)]

    return FilterOptions(
        products=products, action_taken_by=action_taken_by, received_by=received_by,
        latest_owner=latest_owner, what_next=what_next,
    )


# Ledger Group had 786 distinct values, all loaded in one shot as part of
# get_filter_options and rendered into a single <p-select> with no
# virtualization — the DB query itself was fast (~0.04s), but opening the
# dropdown meant the browser laying out 786 DOM option nodes at once, which
# is what actually felt slow. Same top-100 + type-to-search treatment as
# the Customer picker, rather than shipping the whole list up front.
@router.get("/tt-register/ledger-groups", response_model=list[LookupOption])
def get_ledger_groups(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        sql = "SELECT TOP 100 Name FROM LedgerGroupMaster WHERE Enabled = 1 "
        params: list = []
        search = search.strip()
        if search:
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["Name LIKE ?"] * len(keywords))
            sql += f"AND {where_clause} "
            params.extend(f"%{kw}%" for kw in keywords)
        sql += "ORDER BY Name"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["Name"], value=r["Name"]) for r in rows_to_dicts(cursor)]


@router.get("/tt-register/search", response_model=list[TtRegisterRow])
def search_tt_register(
    product: str = "ALL", action_taken_by: str = "ALL", received_by: str = "ALL", tt_status: str = "ALL",
    latest_owner: str = "ALL", ledger_group: str = "ALL", cust_id: int = 0,
    from_date: date | None = None, to_date: date | None = None, ticket_no: str = "",
    exclude_action: bool = False,
    # A bare `list[str] | None = None` annotation silently does NOT bind to
    # repeated ?what_next=... query params in this FastAPI version — it
    # always resolves to None, so this filter never actually applied,
    # confirmed live (selecting a single WhatNext value still returned
    # tickets with every other WhatNext value). `Annotated[..., Query()]`
    # is the documented fix for FastAPI to treat this as a repeatable param.
    what_next: Annotated[list[str] | None, Query()] = None,
) -> list[TtRegisterRow]:
    where = " WHERE 1 = 1"
    params: list = []
    if product != "ALL":
        where += " AND Vw.Product = ?"
        params.append(product)
    if action_taken_by != "ALL":
        where += " AND Vw.ActionTakenBy = ?"
        params.append(action_taken_by)
    if received_by != "ALL":
        where += " AND Vw.ReceivedBy = ?"
        params.append(received_by)
    if tt_status == "Open":
        where += " AND Vw.closed = 0"
    elif tt_status == "Closed":
        where += " AND Vw.closed = 1"
    if latest_owner != "ALL":
        where += " AND Vw.LatestOwner = ?"
        params.append(latest_owner)
    if ledger_group != "ALL":
        where += " AND Vw.LedgerGroup = ?"
        params.append(ledger_group)
    if cust_id:
        # Exact CustID match (the view exposes Custid directly) rather than
        # a display-name match — avoids ambiguity across similarly-named
        # customers, same reasoning as Pending TT Register's customer filter.
        where += " AND Vw.Custid = ?"
        params.append(cust_id)
    if from_date and to_date:
        where += " AND Vw.Date >= ? AND Vw.Date <= ?"
        params += [from_date, to_date]
    if ticket_no:
        where += " AND Vw.Voucherno = ?"
        params.append(ticket_no)
    if what_next:
        where += f" AND Vw.Whatnext IN ({','.join('?' for _ in what_next)})"
        params += what_next

    # Source runs this fully unfiltered by default (no required filters) —
    # fine on a same-LAN DB server, but VW_TT_And_Action_Report has 150k+
    # rows and an unfiltered scan over this remote connection times out.
    # Capped to the most recent 2000 rows, same fix as VisitLogReport.
    view = "VW_TT_Report" if exclude_action else "VW_TT_And_Action_Report"
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT TOP 2000 * FROM {view} Vw{where} ORDER BY Date DESC, Voucherno DESC, Actiondate DESC",
            *params,
        )
        rows = rows_to_dicts(cursor)

    return [
        TtRegisterRow(
            voucher_no=r["Voucherno"], customer=r["Customer"] or "", called_by=r["CalledBy"] or "",
            date=r["Date"].isoformat() if r["Date"] else None, latest_owner=r["LatestOwner"] or "",
            what_next=r["whatnext"] or "", issue=r["Issue"] or "",
            action_date=r["ActionDate"].isoformat() if r["ActionDate"] else None,
            action_taken_by=r["ActionTakenBy"] or "", action_details=r["ActionDetails"] or "",
            action_on=r["ActionOn"] or None, tt_entered_by=r["TTEnteredBy"] or "",
            next_person=r["NextPerson"] or "", due_days_by_tt=r["DuedaysBYTT"] or 0, due_days_by_action=r["days1"] or 0,
        )
        for r in rows
    ]


@router.get("/tt-register/ticket-detail/{voucher_no}", response_model=list[TtDetailRow])
def get_ticket_detail(voucher_no: int) -> list[TtDetailRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Voucherno, Productname, Issue, action, Whatnext, CalledBy, ClosingDate, ClosingTime "
            "FROM ttdetails WHERE voucherno = ?",
            voucher_no,
        )
        rows = rows_to_dicts(cursor)

    return [
        TtDetailRow(
            voucher_no=r["Voucherno"], product_name=r["Productname"] or "", issue=r["Issue"] or "",
            action=r["action"] or "", what_next=r["Whatnext"] or "", called_by=r["CalledBy"] or "",
            closing_date=r["ClosingDate"].isoformat() if r["ClosingDate"] else "",
            closing_time=str(r["ClosingTime"] or ""),
        )
        for r in rows
    ]


@router.get("/tt-register/owner-history/{voucher_no}", response_model=list[OwnerHistoryRow])
def get_owner_history(voucher_no: int) -> list[OwnerHistoryRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT 'TTEntered' AS PreviousOwner, Tm.Date,
                   CASE WHEN NextPersonid > 0 THEN Um.Name WHEN ReceivedBy > 0 THEN Sm.Name END AS LatestOwner
            FROM TTMaster Tm
            INNER JOIN Usermaster Um ON Tm.NextPersonID = Um.USerid
            INNER JOIN Salesman SM ON Tm.ReceivedBy = sm.Salesmanid
            WHERE voucherno = ?
            UNION ALL
            SELECT Um1.Name AS PreviousOwner, ActionDate, Um.Name AS LatestOwner
            FROM TTActionmaster TA
            INNER JOIN Usermaster Um ON Ta.ActiononUserid = Um.USerID
            INNER JOIN Usermaster Um1 ON Ta.ActionUserid = Um1.USerID
            WHERE TTVoucherno = ?
            ORDER BY Date
            """,
            voucher_no, voucher_no,
        )
        rows = rows_to_dicts(cursor)

    return [
        OwnerHistoryRow(
            sr_no=i + 1, previous_owner=r["PreviousOwner"] or "",
            date=r["Date"].isoformat() if r["Date"] else None, latest_owner=r["LatestOwner"] or "",
        )
        for i, r in enumerate(rows)
    ]


# --- Mirrors TroubleTicket_Modify.aspx.vb ---


@router.get("/tt-modify/filters", response_model=TtModifyFilters)
def get_tt_modify_filters() -> TtModifyFilters:
    with get_cursor() as cursor:
        # Raw DISTINCT ProductName (untrimmed) was ~6,700 rows, including
        # whitespace-only duplicates of the same value — trimming and
        # deduping doesn't change the underlying data but at least halves
        # the noise; the frontend adds search + virtual scroll on top since
        # even a few thousand clean entries is still too many for a plain
        # dropdown to render at once without hanging the page.
        cursor.execute(
            "SELECT DISTINCT RTRIM(LTRIM(ProductName)) AS ProductName FROM TTDetails "
            "WHERE ProductName IS NOT NULL AND RTRIM(LTRIM(ProductName)) <> '' "
            "ORDER BY RTRIM(LTRIM(ProductName))"
        )
        products = [r["ProductName"] for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT UserID, Name FROM UserMaster ORDER BY Name")
        users = [UserOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]

    return TtModifyFilters(products=products, users=users)


@router.get("/tt-modify/rows", response_model=list[TtModifyRow])
def get_tt_modify_rows(
    cust_id: int = 0, product_name: str = "All", latest_owner_id: int = 0, internal_reference_user_id: int = 0,
    priority: str = "All", status: str = "Pending", from_date: date | None = None, to_date: date | None = None,
) -> list[TtModifyRow]:
    where = ""
    params: list = []
    if cust_id:
        # An exact CustID match (picked from the same top-100/search customer
        # picker used elsewhere) rather than a DisplayName LIKE — a fuzzy name
        # match could silently span multiple similarly-named customers.
        where += " AND TM.CustID = ?"
        params.append(cust_id)
    if product_name != "All":
        where += " AND TD.ProductName = ?"
        params.append(product_name)
    if latest_owner_id:
        where += " AND TM.LatestOwnerID = ?"
        params.append(latest_owner_id)
    if internal_reference_user_id:
        where += " AND TM.InternalReferenceID = ?"
        params.append(internal_reference_user_id)
    if priority != "All":
        where += " AND TD.Priority = ?"
        params.append(priority)
    if status == "Pending":
        where += " AND TM.Closed = 0"
    elif status == "Closed":
        where += " AND TM.Closed = 1"
    if from_date:
        where += " AND TM.Date >= ?"
        params.append(from_date)
    if to_date:
        where += " AND TM.Date <= ?"
        params.append(to_date)

    with get_cursor() as cursor:
        # Unfiltered/loosely-filtered (e.g. Status=All or Status=Closed with
        # nothing else set) joins across TTMaster+TTDetails+CustomerMaster
        # match 130,000+ rows — confirmed live to take 30+ seconds and return
        # a 50+ MB payload, which is what actually made the page "hang", not
        # any single dropdown. Capped to the most recent 2000 matching
        # tickets, same TOP-N-ordered-by-Date-DESC pattern already used by
        # this file's own get_tt_register for the identical class of
        # unbounded historical query.
        cursor.execute(
            f"""
            SELECT TOP 2000 TM.VoucherNo AS TTNo, CM.Name, TM.Date, TD.Issue AS Description, TD.Action AS RWRemarks,
                   CASE TM.LatestOwnerID WHEN 0 THEN 'Not Assigned' ELSE U.Name END AS LatestOwner,
                   TD.Priority, TD.InternalDeadLine, TD.ProductName,
                   CASE TM.Closed WHEN 0 THEN 'Pending' ELSE 'Closed' END AS Status,
                   CASE TM.InternalReferenceID WHEN 0 THEN 'Not Assigned' ELSE U1.Name END AS InternalReferenceUser
            FROM TTMaster TM
            INNER JOIN TTDetails TD ON TM.VoucherNo = TD.VoucherNo
            INNER JOIN CustomerMaster CM ON CM.CustID = TM.CustID AND Cm.disabled = 0
            LEFT OUTER JOIN UserMaster U ON U.UserID = TM.LatestOwnerID
            LEFT OUTER JOIN UserMaster U1 ON U1.UserID = TM.InternalReferenceID
            WHERE 1 = 1 {where}
            ORDER BY TM.Date DESC, TM.VoucherNo DESC
            """,
            *params,
        )
        rows = rows_to_dicts(cursor)

    return [
        TtModifyRow(
            tt_no=r["TTNo"], name=r["Name"] or "", date=r["Date"].isoformat() if r["Date"] else None,
            description=r["Description"] or "", rw_remarks=r["RWRemarks"] or "", latest_owner=r["LatestOwner"] or "",
            priority=r["Priority"] or "", internal_dead_line=r["InternalDeadLine"].isoformat() if r["InternalDeadLine"] else None,
            product_name=r["ProductName"] or "", status=r["Status"] or "", internal_reference_user=r["InternalReferenceUser"] or "",
        )
        for r in rows
    ]


@router.put("/tt-modify/{tt_no}")
def update_tt_modify_row(tt_no: int, body: UpdateTtModifyRequest) -> dict[str, bool]:
    with get_cursor() as cursor:
        if body.internal_dead_line:
            cursor.execute(
                "UPDATE TTDetails SET Priority = ?, InternalDeadLine = ? WHERE VoucherNo = ?",
                body.priority, body.internal_dead_line, tt_no,
            )
        else:
            cursor.execute("UPDATE TTDetails SET Priority = ? WHERE VoucherNo = ?", body.priority, tt_no)
    return {"success": True}
