"""Mirrors Section_Support/VisitLog.aspx.vb, VisitLogRegister.aspx.vb,
VisitLogReport.aspx.vb. The source's many ViewState-driven control-visibility
toggles (Disable()/enable()/visibility() with "Load"/"New"/"Modify" string
flags) were already dropped at the Angular layer in favor of one `mode`
signal — this router just backs the DB reads/writes that drive it.
"""

from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/support/visit-log", tags=["support-visitlog"])


class LookupOption(BaseModel):
    label: str
    value: int


class VisitLogRecord(BaseModel):
    log_id: int
    ref_no: int
    client_id: int
    client_representative: str
    salesman_id: int
    product_id: int
    date: str
    cause_id: int
    support_reason: str
    modules: str
    lock: int
    lock_sr_no: str
    other_modules: str
    remark: str
    within_warranty: bool
    amc: bool
    chargeable: bool
    charges: float
    core_products_tested: bool
    reports_tested: bool
    client_address: str


class SaveVisitLogRequest(BaseModel):
    record: VisitLogRecord
    is_new: bool


class PendingIssueRow(BaseModel):
    narration: str
    employee_name: str
    responded_by: str
    responded_to: str
    responded_date: str | None
    closed: bool
    response: str


class VisitLogRegisterRow(BaseModel):
    log_id: int
    date: str
    ref_no: str
    client_name: str
    product_name: str
    cause_of_visit: str
    support_reason: str
    modules: str
    other_modules: str
    within_warranty: bool
    amc: bool
    attended_by: str
    remark: str


## Every lookup below used to load its FULL unbounded list on every page
## visit with no cap and no search — Products (2,222 rows), CauseMaster
## (219), the free-text SupportReason history (5,700 distinct values),
## VisitLogMaster's log IDs (23,615), and worst of all Client Name
## (CustDependents, 86,433 rows). That's what was actually "hanging" —
## not a rendering glitch, five separate dropdowns each shipping and
## rendering thousands to tens-of-thousands of DOM options at once. All
## five now follow the same TOP 100 + optional search pattern already
## used by /support/customers and /support/modules elsewhere in this app.


@router.get("/products", response_model=list[LookupOption])
def get_products(search: str = "", id: int = 0) -> list[LookupOption]:
    with get_cursor() as cursor:
        # `id` is used when loading an existing record for Modify: that
        # record's ProductID may not be among the top 100 (or any) name
        # match, and without this it would render as an unselected blank
        # dropdown even though the real value is intact.
        if id:
            cursor.execute("SELECT TOP 1 ProductID, ProductName FROM Products WHERE ProductID = ?", id)
            return [LookupOption(label=r["ProductName"], value=r["ProductID"]) for r in rows_to_dicts(cursor)]
        sql = "SELECT TOP 100 ProductID, ProductName FROM Products WHERE ProductName IS NOT NULL AND ProductName <> '' "
        params: list = []
        search = search.strip()
        if search:
            sql += "AND ProductName LIKE ? "
            params.append(f"%{search}%")
        sql += "ORDER BY ProductName"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["ProductName"], value=r["ProductID"]) for r in rows_to_dicts(cursor)]


@router.get("/causes", response_model=list[LookupOption])
def get_causes(search: str = "", id: int = 0) -> list[LookupOption]:
    with get_cursor() as cursor:
        if id:
            cursor.execute("SELECT TOP 1 CauseID, Name FROM CauseMaster WHERE CauseID = ?", id)
            return [LookupOption(label=r["Name"], value=r["CauseID"]) for r in rows_to_dicts(cursor)]
        sql = "SELECT TOP 100 CauseID, Name FROM CauseMaster WHERE Name IS NOT NULL AND Name <> '' "
        params: list = []
        search = search.strip()
        if search:
            sql += "AND Name LIKE ? "
            params.append(f"%{search}%")
        sql += "ORDER BY Name"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["Name"], value=r["CauseID"]) for r in rows_to_dicts(cursor)]


@router.get("/support-reasons")
def get_support_reasons(search: str = "") -> list[str]:
    with get_cursor() as cursor:
        sql = "SELECT DISTINCT TOP 100 SupportReason FROM VisitLogMaster WHERE SupportReason <> '' "
        params: list = []
        search = search.strip()
        if search:
            sql += "AND SupportReason LIKE ? "
            params.append(f"%{search}%")
        sql += "ORDER BY SupportReason"
        cursor.execute(sql, *params)
        return [r["SupportReason"] for r in rows_to_dicts(cursor)]


@router.get("/clients", response_model=list[LookupOption])
def get_clients(search: str = "", id: int = 0) -> list[LookupOption]:
    with get_cursor() as cursor:
        if id:
            cursor.execute("SELECT TOP 1 CustID, Name FROM CustDependents WHERE DepID = 1 AND CustID = ?", id)
            return [LookupOption(label=r["Name"], value=r["CustID"]) for r in rows_to_dicts(cursor)]
        sql = "SELECT DISTINCT TOP 100 CustID, Name FROM CustDependents WHERE DepID = 1 AND Name IS NOT NULL AND Name <> '' "
        params: list = []
        search = search.strip()
        if search:
            sql += "AND Name LIKE ? "
            params.append(f"%{search}%")
        sql += "ORDER BY Name"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["Name"], value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/client-address")
def get_client_address(client_id: int) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ISNULL(Road,'') + ' ' + ISNULL(Area,'') + ' ' + ISNULL(City,'') + ' ' + ISNULL(Pincode,'') "
            "FROM CustomerMaster WHERE CustID = ?",
            client_id,
        )
        row = cursor.fetchone()
    return (row[0] or "").strip() if row else ""


@router.get("/last-log-id")
def get_last_log_id() -> int | None:
    with get_cursor() as cursor:
        cursor.execute("SELECT MAX(Logid) FROM VisitLogMaster")
        row = cursor.fetchone()
    return row[0] if row and row[0] else None


@router.get("/log-ids", response_model=list[LookupOption])
def get_log_ids(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        sql = "SELECT TOP 100 Logid FROM VisitLogMaster "
        params: list = []
        search = search.strip()
        if search:
            sql += "WHERE CAST(Logid AS NVARCHAR(20)) LIKE ? "
            params.append(f"%{search}%")
        sql += "ORDER BY Logid DESC"
        cursor.execute(sql, *params)
        return [LookupOption(label=str(r["Logid"]), value=r["Logid"]) for r in rows_to_dicts(cursor)]


@router.get("/{log_id}", response_model=VisitLogRecord | None)
def get_visit_log(log_id: int) -> VisitLogRecord | None:
    with get_cursor() as cursor:
        cursor.execute("SELECT * FROM VisitLogMaster WHERE logid = ?", log_id)
        rows = rows_to_dicts(cursor)
        if not rows:
            return None
        r = rows[0]

        cursor.execute(
            "SELECT mm.Name FROM visittestdetail vt INNER JOIN ModuleMaster mm ON vt.ModuleID = mm.ModuleID WHERE vt.logid = ?",
            log_id,
        )
        tested_modules = {t["Name"] for t in rows_to_dicts(cursor)}

        cursor.execute(
            "SELECT ISNULL(Road,'') + ' ' + ISNULL(Area,'') + ' ' + ISNULL(City,'') + ' ' + ISNULL(Pincode,'') "
            "FROM CustomerMaster WHERE CustID = ?",
            r["Clientid"],
        )
        addr_row = cursor.fetchone()
        address = (addr_row[0] or "").strip() if addr_row else ""

    return VisitLogRecord(
        log_id=r["logid"], ref_no=int(r["RefNo"] or 0), client_id=r["Clientid"],
        client_representative=r["ClientRepresentative"] or "", salesman_id=r["SalesmanID"] or 0,
        product_id=r["ProductID"] or 0, date=r["Date"].isoformat(), cause_id=r["CauseID"] or 0,
        support_reason=r["SupportReason"] or "", modules=r["Modules"] or "", lock=r["Lock"] or 3,
        lock_sr_no=r["LockSrNo"] or "", other_modules=r["Othermodules"] or "", remark=r["Remark"] or "",
        within_warranty=bool(r["WithinWarranty"]), amc=bool(r["AMC"]), chargeable=bool(r["Chargeable"]),
        charges=float(r["Charges"] or 0), core_products_tested="Core Products" in tested_modules,
        reports_tested="Reports" in tested_modules, client_address=address,
    )


def _save_tested_modules(cursor, log_id: int, core_products_tested: bool, reports_tested: bool) -> None:
    for tested, name in ((core_products_tested, "Core Products"), (reports_tested, "Reports")):
        if not tested:
            continue
        cursor.execute("SELECT ModuleID FROM ModuleMaster WHERE Name = ?", name)
        row = cursor.fetchone()
        if row:
            cursor.execute("INSERT INTO visittestdetail (LogID, ModuleID, Tested) VALUES (?, ?, 1)", log_id, row[0])


@router.post("")
def save_visit_log(body: SaveVisitLogRequest) -> dict[str, bool]:
    # This used to call Proc_AddUpdateVisitlog directly. Confirmed live and
    # via the proc's own definition (OBJECT_DEFINITION) that it declares
    # `@clientid smallint` — but VisitLogMaster.Clientid is `bigint` and real
    # CustDependents.CustID values now run past 96,000 (smallint tops out at
    # 32,767), so saving almost any real client raised
    # "Arithmetic overflow error converting int to data type smallint"
    # before the proc's own body (and its TRY/CATCH) ever ran. The legacy
    # source only avoids this for new records by bypassing the proc entirely
    # with a raw INSERT — its Modify path calls this same proc and would hit
    # the identical overflow for any client above 32,767. Reimplemented both
    # branches directly here (still one atomic transaction via get_cursor)
    # instead of routing through the proc at all, using real parameter
    # binding against the table's actual (bigint) column types. Also fixes
    # a second, separate bug already present in the proc's Insert branch: it
    # computes @newlogid internally for the visitlogmaster row but then
    # inserts visittestdetail rows under the caller-supplied @logid (0 for a
    # genuinely new record) instead of @newlogid, silently orphaning the
    # Core Products/Reports "tested" flags from the row they were meant to
    # describe.
    r = body.record
    with get_cursor() as cursor:
        if body.is_new:
            cursor.execute("SELECT ISNULL(MAX(logid), 0) + 1 FROM visitlogmaster")
            new_log_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO visitlogmaster
                    (logid, refno, clientid, clientrepresentative, salesmanid, productid, date, Causeid,
                     supportreason, modules, lock, locksrno, othermodules, remark, WithinWarranty, Amc,
                     chargeable, charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                new_log_id, str(r.ref_no), r.client_id, r.client_representative, r.salesman_id, r.product_id,
                r.date, r.cause_id, r.support_reason, r.modules, r.lock, r.lock_sr_no, r.other_modules,
                r.remark, r.within_warranty, r.amc, r.chargeable, r.charges,
            )
            _save_tested_modules(cursor, new_log_id, r.core_products_tested, r.reports_tested)
        else:
            cursor.execute(
                """
                UPDATE visitlogmaster
                SET clientid=?, clientrepresentative=?, salesmanid=?, productid=?, date=?, Causeid=?,
                    supportreason=?, modules=?, lock=?, locksrno=?, othermodules=?, remark=?,
                    WithinWarranty=?, Amc=?, chargeable=?, charges=?
                WHERE logid=?
                """,
                r.client_id, r.client_representative, r.salesman_id, r.product_id, r.date, r.cause_id,
                r.support_reason, r.modules, r.lock, r.lock_sr_no, r.other_modules, r.remark,
                r.within_warranty, r.amc, r.chargeable, r.charges, r.log_id,
            )
            cursor.execute("DELETE FROM visittestdetail WHERE logid = ?", r.log_id)
            _save_tested_modules(cursor, r.log_id, r.core_products_tested, r.reports_tested)
    return {"success": True}


@router.get("/{log_id}/pending-issues", response_model=list[PendingIssueRow])
def get_pending_issues(log_id: int) -> list[PendingIssueRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT vpd.Narration, sm.Name, vpd.RespondedBy, vpd.RespondedTo, vpd.RespondedDate, vpd.Closed, vpd.Response
            FROM VisitPendingDetail vpd INNER JOIN Salesman sm ON sm.SalesmanId = vpd.SalesmanId
            WHERE vpd.logid = ?
            """,
            log_id,
        )
        rows = rows_to_dicts(cursor)

    return [
        PendingIssueRow(
            narration=r["Narration"] or "", employee_name=r["Name"] or "", responded_by=r["RespondedBy"] or "",
            responded_to=r["RespondedTo"] or "", responded_date=r["RespondedDate"].isoformat() if r["RespondedDate"] else None,
            closed=bool(r["Closed"]), response=r["Response"] or "",
        )
        for r in rows
    ]


def _visit_log_join_rows(where: str, params: list) -> list[dict]:
    # Source page (VisitLogReport.aspx.vb) runs this fully unfiltered — fine on
    # a same-LAN DB server, but a 23k+ row, 4-way-joined scan over this remote
    # connection took >60s and never returned. Capped to the most recent 1000
    # rows for practicality; VisitLogRegister's actual filters make this cap
    # rarely relevant there.
    query = f"""
        SELECT TOP 1000 V.LogID, V.Date, V.RefNo, D.Name AS ClientName, P.ProductName, C.Name AS CauseOfVisit,
               V.SupportReason, V.Modules, V.OtherModules, V.WithinWarranty, V.AMC, S.Name AS AttendedBy, V.Remark
        FROM VisitLogMaster V
        INNER JOIN Products P ON P.ProductID = V.ProductID
        INNER JOIN CauseMaster C ON C.CauseID = V.CauseID
        INNER JOIN CustDependents D ON D.CustID = V.ClientID
        INNER JOIN Salesman S ON S.SalesmanID = V.SalesmanID
        WHERE D.DepID = 1 {where}
        ORDER BY V.Date DESC
    """
    with get_cursor() as cursor:
        cursor.execute(query, *params)
        return rows_to_dicts(cursor)


def _to_register_row(r: dict) -> VisitLogRegisterRow:
    return VisitLogRegisterRow(
        log_id=r["LogID"], date=r["Date"].isoformat(), ref_no=r["RefNo"] or "", client_name=r["ClientName"] or "",
        product_name=r["ProductName"] or "", cause_of_visit=r["CauseOfVisit"] or "", support_reason=r["SupportReason"] or "",
        modules=r["Modules"] or "", other_modules=r["OtherModules"] or "", within_warranty=bool(r["WithinWarranty"]),
        amc=bool(r["AMC"]), attended_by=r["AttendedBy"] or "", remark=r["Remark"] or "",
    )


@router.get("-register", response_model=list[VisitLogRegisterRow])
def get_visit_log_register(
    narration: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
    client_id: int | None = None,
    person_id: int | None = None,
    product_id: int | None = None,
    cause_id: int | None = None,
    from_ref: int | None = None,
    to_ref: int | None = None,
) -> list[VisitLogRegisterRow]:
    where = ""
    params: list = []
    if narration.strip():
        # Was unconditional (`LIKE '%' + narration + '%'` even when narration
        # was empty) — harmless today since no row has a NULL SupportReason,
        # but a NULL would silently fail LIKE and vanish from every search
        # regardless of whether a narration filter was ever set. Only apply
        # the clause when there's actually something to filter on.
        where += " AND V.SupportReason LIKE ?"
        params.append(f"%{narration}%")
    if from_date and to_date:
        where += " AND V.Date >= ? AND V.Date <= ?"
        params += [from_date, to_date]
    if client_id:
        where += " AND D.CustID = ?"
        params.append(client_id)
    if person_id:
        where += " AND S.SalesmanID = ?"
        params.append(person_id)
    if product_id:
        where += " AND P.ProductID = ?"
        params.append(product_id)
    if cause_id:
        where += " AND C.CauseID = ?"
        params.append(cause_id)
    # RefNo is stored as nvarchar, so a plain `RefNo >= ?` comparison sorts
    # lexicographically, not numerically — confirmed live: searching the
    # numeric range [9, 99] returned 764 rows by string comparison vs the
    # correct 88 by numeric comparison (e.g. "10" sorts before "9" as text).
    # TRY_CAST compares as real integers and safely evaluates to NULL (no
    # match) for the handful of blank RefNo values instead of erroring.
    if from_ref is not None and to_ref is not None:
        where += " AND TRY_CAST(V.RefNo AS INT) >= ? AND TRY_CAST(V.RefNo AS INT) <= ?"
        params += [from_ref, to_ref]
    elif from_ref is not None:
        where += " AND TRY_CAST(V.RefNo AS INT) = ?"
        params.append(from_ref)
    elif to_ref is not None:
        where += " AND TRY_CAST(V.RefNo AS INT) = ?"
        params.append(to_ref)

    rows = _visit_log_join_rows(where, params)
    return [_to_register_row(r) for r in rows]


@router.get("-report", response_model=list[VisitLogRegisterRow])
def get_visit_log_report() -> list[VisitLogRegisterRow]:
    rows = _visit_log_join_rows("", [])
    return [_to_register_row(r) for r in rows]
