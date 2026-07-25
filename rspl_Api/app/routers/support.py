"""Mirrors Section_Support/TroubleTicket.aspx.vb, IssueDetail.aspx.vb,
TTAction.aspx.vb (the trouble-ticket core group).

NOTE: `webproc_AddTTAction`'s underlying stored proc unconditionally calls
`Proc_TTSendCallBackEMail`, which sends a real email via `PROC_SENDEMAIL`
whenever the action being saved has the "call back" flag set — this is core,
intended functionality of that checkbox (call back a specific, dynamically
selected action-owner), not an incidental broadcast to hardcoded staff like
the leave-workflow emails were, so it is NOT stubbed here. Be aware that
POSTing to /support/tt-action with call_back=true will send a real email to
whichever user is selected — this write path was not live-tested for that
reason.

NOTE: Trouble Ticket attachments — implemented for real (not stubbed).
The source (TroubleTicket.aspx.vb's UploadFiles, called after
WebProc_AddTTMaster returns the new VoucherNo) saved uploaded files
straight to C:\\Retailware\\TTAttachments on the IIS server's own local
disk — never in the database, and confirmed to have a latent bug where the
built-up attachments path string was never actually saved to any column
(passed to WebProc_AddTTMaster's @Attachments parameter before UploadFiles
had populated it, i.e. always empty). No other page in the source was ever
found to read TT attachments back either. Given this app's own backend
process runs on this same class of Windows machine (C:\\Retailware already
exists here, just not the TTAttachments subfolder), local-disk storage is
kept — same base convention, one ticket per subfolder rather than the
source's flat `{ttNo}(n).ext` naming — but retrieval is done by listing
each ticket's own subfolder directly instead of relying on the source's
broken/never-populated database string.
"""

import re
from datetime import date, datetime, time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/support", tags=["support"])

# Mirrors the source's own C:\Retailware convention (that root already
# exists on this machine) rather than inventing an unrelated path.
ATTACHMENTS_ROOT = Path(r"C:\Retailware\TTAttachments")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def _safe_ticket_dir(voucher_no: int) -> Path:
    ticket_dir = ATTACHMENTS_ROOT / str(voucher_no)
    ticket_dir.mkdir(parents=True, exist_ok=True)
    return ticket_dir


def _safe_filename(original_name: str) -> str:
    # Path(...).name strips any directory components a malicious/unexpected
    # filename could carry (e.g. "../../evil.jpg") — combined with the
    # extension allow-list below, the saved name can never escape the
    # per-ticket folder or write a non-image file.
    name = Path(original_name).name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or '(none)'}. Only images are allowed.")
    # Strips anything that isn't alnum/dot/dash/underscore/space, so the
    # final on-disk name is always predictable regardless of what the
    # browser sent.
    stem = re.sub(r"[^\w\-. ]", "_", Path(name).stem) or "attachment"
    return f"{stem}{ext}"


def _unique_path(ticket_dir: Path, filename: str) -> Path:
    candidate = ticket_dir / filename
    if not candidate.exists():
        return candidate
    stem, ext = Path(filename).stem, Path(filename).suffix
    n = 1
    while (ticket_dir / f"{stem} ({n}){ext}").exists():
        n += 1
    return ticket_dir / f"{stem} ({n}){ext}"


class LookupOption(BaseModel):
    label: str
    value: int


class CustInfoItem(BaseModel):
    field: str
    description: str


class CustomerInfo(BaseModel):
    general: list[CustInfoItem]
    status: list[CustInfoItem]
    report: list[CustInfoItem]
    disabled: bool = False


class TicketSummaryRow(BaseModel):
    voucher_no: int
    product_name: str
    date: str
    user_name: str
    closed_narration: str | None = None


class IssueDetailRow(BaseModel):
    module: str
    priority: str
    what_next: str
    issue: str
    action: str
    exe_version_no: str
    closed: bool
    # Mirrors IssueDetail.aspx's RcbClosedby — a UserMaster picker (same
    # query as /users), not free text. Only ever actually persists to
    # TTMaster.closedBy (TTDetails has no ClosedBy column at all), and only
    # on whichever save happens to close every one of the ticket's issue
    # rows at once — see WebProc_AddModifyTTDetails's closing check.
    closed_by: int = 0
    # Mirrors IssueDetail.aspx's rcbNarration — sourced from the same
    # /closed-narrations list as the standalone IssueDetail popup. The
    # source also let rcbNarration accept free text and auto-create a new
    # RSPL_TTClosedNarrationMaster row via webProc_AddTTCloseNarration; not
    # carried forward here, same as closed_by staying a strict picklist
    # rather than free text — narration selection follows that same
    # existing-options-only pattern for consistency.
    closed_narration_id: int = 0


class CreateTicketRequest(BaseModel):
    cust_id: int
    received_by: int
    next_person: int
    called_by: str
    issues: list[IssueDetailRow]


class TicketDetailIssue(BaseModel):
    module: str
    priority: str
    what_next: str
    issue: str
    action: str
    exe_version_no: str
    closed: bool
    # Sourced from TM.closedBy (joined in per row by webProc_TTDetails) —
    # same as DisplayInfoForEdit's own RcbClosedby.SelectedValue =
    # dt.Rows(0).Item("ClosedBy"). Not a genuine per-row value (TTDetails
    # has no ClosedBy column), just the ticket-level value the source
    # itself already reads the same way.
    closed_by: int
    # Same story as closed_by — sourced from TM.closedID (TTDetails has no
    # ClosedID column either), matching DisplayInfoForEdit's own
    # rcbNarration.SelectedValue = dt.Rows(0).Item("ClosedID").
    closed_narration_id: int
    # Display text for closed_narration_id — webProc_TTDetails already joins
    # this in per row. Needed because /closed-narrations is now capped to
    # TOP 100: a narration outside that slice would otherwise have no label
    # to show once selected, so the frontend splices {closed_narration_text,
    # closed_narration_id} back into its options list when it's missing.
    closed_narration_text: str


class TicketDetailResponse(BaseModel):
    cust_id: int
    customer_name: str
    received_by: int
    next_person: int
    called_by: str
    issues: list[TicketDetailIssue]


class UpdateIssueRow(BaseModel):
    module: str
    priority: str
    what_next: str
    issue: str
    action: str
    exe_version_no: str
    closed: bool
    closed_by: int = 0
    closed_narration_id: int = 0
    is_existing: bool
    # The module name as it was when this row was loaded from
    # get_ticket_detail — only meaningful when is_existing is True. Lets
    # update_ticket detect a rename (module != original_module) and match
    # the row to delete by its old key instead of the new one.
    original_module: str | None = None


class UpdateTicketRequest(BaseModel):
    called_by: str
    issues: list[UpdateIssueRow]


class IssueDetailFull(BaseModel):
    module: str
    priority: str
    what_next: str
    issue: str
    action: str
    exe_version_no: str
    closed: bool
    closed_by: int
    closed_narration_id: int
    # Same reason as TicketDetailIssue.closed_narration_text: /closed-narrations
    # is now capped to TOP 100, so this popup's own narration picker needs the
    # saved narration's text to splice back in when it falls outside that slice.
    closed_narration_text: str


class SaveIssueDetailRequest(BaseModel):
    tt_no: int
    module: str
    priority: str
    what_next: str
    issue: str
    action: str
    exe_version_no: str
    closed: bool
    closed_by: int
    closed_narration_id: int


class TicketBrief(BaseModel):
    customer: str
    issue: str


class AddTtActionRequest(BaseModel):
    tt_no: int
    action_details: str
    action_taken_by: int
    call_back: bool
    action_on: int
    call_back_date: date
    call_back_time: str


class TtActionRow(BaseModel):
    sr_no: int
    tt_date: str
    received_by: str
    issue: str
    action_id: int
    action_date: str
    action_details: str
    action_by: str
    action_on: str


# --- Shared lookups ---


@router.get("/customers", response_model=list[LookupOption])
def get_customers(search: str = "") -> list[LookupOption]:
    # Was an unfiltered TOP 500 load on every TroubleTicket page visit — capped
    # to TOP 100 with server-side search instead, same load-on-demand shape as
    # Advance/CustomerAns's customer picker (admin_vouchers.get_customers).
    with get_cursor() as cursor:
        sql = (
            "SELECT TOP 100 CustID, LTRIM(RTRIM(UPPER(DisplayName))) AS DisplayName "
            "FROM CustomerMaster WHERE Groupid = 1 AND CustID <> 0 AND DisplayName <> '' "
        )
        params: list = []
        search = search.strip()
        if search:
            # Staff commonly know a customer by their numeric CustID ("customer
            # code") as readily as by name — match either so the same search
            # box works for both instead of requiring name-only text. For the
            # name side, split what was typed into words and require each one
            # to appear somewhere in DisplayName, in any order — e.g. "maha
            # ino" matches "Mahaveer Inovation Pvt. Ltd." — instead of the old
            # single LIKE '%whole string%' (exact substring, in order only).
            # The CustID side still matches the whole typed string as-is,
            # since a customer code search is always a single token anyway.
            keywords = search.split()
            name_clause = " AND ".join(["DisplayName LIKE ?"] * len(keywords))
            sql += f"AND (({name_clause}) OR CAST(CustID AS NVARCHAR(20)) LIKE ?) "
            params.extend(f"%{kw}%" for kw in keywords)
            params.append(f"%{search}%")
        sql += "ORDER BY DisplayName"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["DisplayName"], value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/salesmen", response_model=list[LookupOption])
def get_salesmen() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT SalesmanId, Name FROM Salesman WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["SalesmanId"]) for r in rows_to_dicts(cursor)]


@router.get("/next-persons", response_model=list[LookupOption])
def get_next_persons() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name FROM UserMaster WHERE Email <> '' AND Email IS NOT NULL ORDER BY Name"
        )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 AND Email <> '' AND Email IS NOT NULL ORDER BY Name"
        )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


# --- Mirrors TroubleTicket.aspx.vb ---


@router.get("/customer-info", response_model=CustomerInfo)
def get_customer_info(cust_id: int) -> CustomerInfo:
    with get_cursor() as cursor:
        cursor.execute("EXEC webProc_TTCustInfo ?", cust_id)
        rows = rows_to_dicts(cursor)

        # Mirrors TroubleTicket.aspx.vb's disabled-customer check (source
        # matched by DisplayName text; keyed on CustID here, which is more
        # reliable). Blocks TT creation for disabled customers the same way
        # the legacy page does.
        disabled = False
        if cust_id:
            cursor.execute("SELECT Disabled FROM CustomerMaster WHERE CustID = ?", cust_id)
            disabled_rows = rows_to_dicts(cursor)
            disabled = bool(disabled_rows[0]["Disabled"]) if disabled_rows else False

    def items(info_type: int) -> list[CustInfoItem]:
        return [
            CustInfoItem(field=r["Field"], description=str(r["Description"] or ""))
            for r in rows
            if r["InfoType"] == info_type
        ]

    return CustomerInfo(general=items(1), status=items(2), report=items(3), disabled=disabled)


def _tt_details_rows(tt_no: int, cust_id: int, closed: int) -> list[dict]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webProc_TTDetails ?, ?, ?, '', 0", tt_no, cust_id, closed)
        return rows_to_dicts(cursor)


@router.get("/pending-tickets", response_model=list[TicketSummaryRow])
def get_pending_tickets(cust_id: int) -> list[TicketSummaryRow]:
    rows = _tt_details_rows(0, cust_id, 2)
    return [
        TicketSummaryRow(
            voucher_no=r["Voucherno"], product_name=r["Productname"] or "",
            date=r["Date"].isoformat(), user_name=r["UserName"] or "",
            closed_narration=r.get("ClosedNarration"),
        )
        for r in rows
    ]


@router.get("/closed-tickets", response_model=list[TicketSummaryRow])
def get_closed_tickets(cust_id: int) -> list[TicketSummaryRow]:
    rows = _tt_details_rows(0, cust_id, 1)
    return [
        TicketSummaryRow(
            voucher_no=r["Voucherno"], product_name=r["Productname"] or "",
            date=r["Date"].isoformat(), user_name=r["UserName"] or "",
            closed_narration=r.get("ClosedNarration"),
        )
        for r in rows
    ]


# Mirrors the source's rcbTTNo combo (bound to "Select Top 100 Voucherno
# from ttMaster order by Voucherno desc" in ClearControl) for the Edit-mode
# Ticket No. picker — narrowed to pending (Closed=0) tickets only, and
# queried directly against TTMaster/CustomerMaster instead of reusing
# webProc_TTDetails, since that proc returns one row per issue-detail row
# and would show the same ticket number repeated for every issue attached
# to it.
@router.get("/pending-tickets/all", response_model=list[LookupOption])
def get_all_pending_tickets() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TM.VoucherNo AS VoucherNo, LTRIM(RTRIM(CM.DisplayName)) AS DisplayName "
            "FROM TTMaster TM INNER JOIN CustomerMaster CM ON CM.CustID = TM.CustID "
            "WHERE TM.Closed = 0 ORDER BY TM.VoucherNo DESC"
        )
        return [
            LookupOption(label=f"{r['VoucherNo']} — {r['DisplayName'] or ''}", value=r["VoucherNo"])
            for r in rows_to_dicts(cursor)
        ]


@router.get("/tickets/{tt_no}", response_model=TicketDetailResponse | None)
def get_ticket_detail(tt_no: int) -> TicketDetailResponse | None:
    """Feeds the Edit-mode auto-populate: mirrors ShowTTdetails() (master
    fields via WebProc_GetTTDetails) plus webproc_InsertTempTTDetails (the
    ticket's existing issue rows) — both folded into one call here since
    webProc_TTDetails already returns the joined master+detail shape."""
    rows = _tt_details_rows(tt_no, 0, 0)
    if not rows:
        return None
    first = rows[0]
    return TicketDetailResponse(
        cust_id=first["CustID"],
        customer_name=(first.get("displayName") or "").strip(),
        received_by=first["Receivedby"] or 0,
        next_person=first["nextPersonid"] or 0,
        called_by=first.get("Calledby") or "",
        issues=[
            TicketDetailIssue(
                module=r["Productname"] or "", priority=r["Priority"] or "", what_next=r["whatnext"] or "",
                issue=r["Issue"] or "", action=r["Action"] or "", exe_version_no=r["ExeVersionNo"] or "",
                closed=r["Closed"] == "Yes", closed_by=r.get("closedBy") or 0,
                closed_narration_id=r.get("closedID") or 0,
                closed_narration_text=r.get("ClosedNarration") or "",
            )
            for r in rows
        ],
    )


@router.put("/tickets/{tt_no}")
def update_ticket(
    tt_no: int, body: UpdateTicketRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, object]:
    """Mirrors btnSubmit_Click's rbbtnEdit branch: resaves every issue row
    against the selected ticket. Rows loaded from get_ticket_detail (
    is_existing=True) go through WebProc_AddModifyTTDetails with @Add=0,
    which UPDATEs the existing TTDetails row matched by (VoucherNo,
    ProductName) — same matching the source relied on. Rows added fresh in
    this edit session (is_existing=False) go through with @Add=1 to INSERT,
    since no existing row could match them.

    A renamed Module (module != original_module on an existing row) can't
    go through that same @Add=0 path: the proc's UPDATE uses the single
    @ProductName parameter both to SET the new name and to match the WHERE
    clause, so it has no way to look up the old row by its old name and
    rename it in one call. TTDetails has no identity column — confirmed
    (VoucherNo, ProductName) is genuinely the only key anything (including
    the proc itself) ever matches this table by, and nothing has a FK to
    it — so a rename is deleted by its old key and re-inserted (@Add=1)
    under the new one instead of updated in place."""
    if not body.issues:
        raise HTTPException(status_code=400, detail="Please add at least one issue detail before updating.")
    with get_cursor() as cursor:
        for issue in body.issues:
            renamed = issue.is_existing and issue.original_module is not None and issue.original_module != issue.module
            if renamed:
                cursor.execute(
                    "DELETE FROM TTDetails WHERE Voucherno = ? AND ProductName = ?",
                    tt_no, issue.original_module,
                )
            add_flag = 0 if (issue.is_existing and not renamed) else 1
            cursor.execute(
                "EXEC WebProc_AddModifyTTDetails ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
                add_flag, tt_no, issue.module, issue.issue, issue.action, issue.closed,
                issue.priority, issue.what_next, body.called_by, current_user.user_id,
                issue.exe_version_no or "0", issue.closed_by, issue.closed_narration_id,
            )
    return {"voucherNo": tt_no, "message": f"Ticket {tt_no} updated successfully."}


@router.post("/tickets")
def create_ticket(
    body: CreateTicketRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, object]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_AddTTMaster ?, ?, ?, 0, ?, '', ?",
            body.cust_id, current_user.user_id, body.received_by, body.next_person, body.called_by,
        )
        master_rows = rows_to_dicts(cursor)
        if not master_rows:
            return {"voucherNo": 0, "message": "Failed to create ticket"}
        voucher_no = master_rows[0]["VoucherNo"]
        message = master_rows[0]["ResultMessage"]

        for issue in body.issues:
            cursor.execute(
                "EXEC WebProc_AddModifyTTDetails 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
                voucher_no, issue.module, issue.issue, issue.action, issue.closed,
                issue.priority, issue.what_next, body.called_by, current_user.user_id,
                issue.exe_version_no or "0", issue.closed_by, issue.closed_narration_id,
            )

    return {"voucherNo": voucher_no, "message": message}


@router.get("/attachments")
def get_attachments(voucher_no: int) -> list[dict]:
    ticket_dir = ATTACHMENTS_ROOT / str(voucher_no)
    if not ticket_dir.is_dir():
        return []
    return [
        {"filename": f.name, "url": f"/support/tickets/{voucher_no}/attachments/{f.name}"}
        for f in sorted(ticket_dir.iterdir())
        if f.is_file()
    ]


@router.post("/tickets/{voucher_no}/attachments")
def upload_attachments(
    voucher_no: int, files: list[UploadFile] = File(...), current_user: CurrentUser = Depends(get_current_user)
) -> list[dict]:
    if voucher_no <= 0:
        raise HTTPException(status_code=400, detail="A ticket must be saved before attachments can be uploaded.")
    ticket_dir = _safe_ticket_dir(voucher_no)
    saved = []
    for upload in files:
        filename = _safe_filename(upload.filename or "attachment")
        dest = _unique_path(ticket_dir, filename)
        with dest.open("wb") as f:
            f.write(upload.file.read())
        saved.append({"filename": dest.name, "url": f"/support/tickets/{voucher_no}/attachments/{dest.name}"})
    return saved


@router.get("/tickets/{voucher_no}/attachments/{filename}")
def download_attachment(voucher_no: int, filename: str) -> FileResponse:
    # Path(...).name (not the raw path param) so a filename like
    # "../../../windows/win.ini" can't escape the per-ticket folder.
    safe_name = Path(filename).name
    file_path = ATTACHMENTS_ROOT / str(voucher_no) / safe_name
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return FileResponse(file_path)


# --- Mirrors IssueDetail.aspx.vb ---


@router.get("/priorities", response_model=list[str])
def get_priorities() -> list[str]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT RTRIM(LTRIM(Priority)) AS Priority FROM TTDetails WHERE Priority <> '' "
            "ORDER BY RTRIM(LTRIM(Priority))"
        )
        return [r["Priority"] for r in rows_to_dicts(cursor)]


# TTDetails.ProductName ("Module") itself is unfiltered free text with 6,000+
# accumulated junk values (typos, dates, one-off notes) — not usable as a
# picklist. TTProducts is a maintained, much smaller (~460-row) list of real
# module/feature names (Accounting, Advance, Aging Report, ...) and is the
# closest real equivalent to a module master for this field, even though the
# legacy TroubleTicket/IssueDetail pages themselves never wired a combo to it
# and left Module as a raw textbox.
@router.get("/modules", response_model=list[str])
def get_tt_modules() -> list[str]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT RTRIM(LTRIM(ProductName)) AS ProductName FROM TTProducts "
            "WHERE ProductName IS NOT NULL AND RTRIM(LTRIM(ProductName)) <> '' "
            "ORDER BY RTRIM(LTRIM(ProductName))"
        )
        return [r["ProductName"] for r in rows_to_dicts(cursor)]


@router.get("/closed-narrations", response_model=list[LookupOption])
def get_closed_narrations(search: str = "") -> list[LookupOption]:
    # RSPL_TTClosedNarrationMaster has 4,488 rows (4,453 distinct
    # narrations) — loading all of them unfiltered was the slow/hanging
    # Closing Narration picker. Capped to TOP 100, re-queried by text as the
    # user types — same load-on-demand shape as the Customer picker
    # (get_customers) and Module list.
    with get_cursor() as cursor:
        sql = "SELECT DISTINCT TOP 100 ClosedNarration, ClosedID FROM RSPL_TTClosedNarrationMaster "
        params: list = []
        search = search.strip()
        if search:
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " AND ".join(["ClosedNarration LIKE ?"] * len(keywords))
            sql += f"WHERE {where_clause} "
            params.extend(f"%{kw}%" for kw in keywords)
        sql += "ORDER BY ClosedNarration"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["ClosedNarration"], value=r["ClosedID"]) for r in rows_to_dicts(cursor)]


@router.get("/issue-detail/{tt_no}", response_model=IssueDetailFull | None)
def get_issue_detail_full(tt_no: int) -> IssueDetailFull | None:
    rows = _tt_details_rows(tt_no, 0, 0)
    if not rows:
        return None
    r = rows[0]
    return IssueDetailFull(
        module=r["Productname"] or "", priority=r["Priority"] or "", what_next=r["whatnext"] or "",
        issue=r["Issue"] or "", action=r["Action"] or "", exe_version_no=r["ExeVersionNo"] or "",
        closed=r["Closed"] == "Yes", closed_by=r.get("closedBy") or 0, closed_narration_id=r.get("closedID") or 0,
        closed_narration_text=r.get("ClosedNarration") or "",
    )


@router.post("/issue-detail")
def save_issue_detail(
    body: SaveIssueDetailRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_AddModifyTTDetails 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
            body.tt_no, body.module, body.issue, body.action, body.closed,
            body.priority, body.what_next, "", current_user.user_id,
            body.exe_version_no or "0", body.closed_by, body.closed_narration_id,
        )
    return {"success": True}


# --- Mirrors TTAction.aspx.vb ---


@router.get("/tt-action/brief/{tt_no}", response_model=TicketBrief | None)
def get_ticket_brief(tt_no: int) -> TicketBrief | None:
    rows = _tt_details_rows(tt_no, 0, 0)
    if not rows:
        return None
    return TicketBrief(customer=rows[0].get("displayName") or "", issue=rows[0]["Issue"] or "")


@router.post("/tt-action")
def add_tt_action(
    body: AddTtActionRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    # webproc_AddTTAction itself overrides @CallBackDate/@CallBackTime with
    # BillDate whenever @IsCallBack=0, so their value is a no-op in that
    # case — chkCallBack.Checked hides the source's own date/time pickers
    # the same way (PnlClose-style conditional visibility), meaning the
    # common "log an action, no call back" path never has a real time
    # string to parse. Parsing unconditionally (previously
    # datetime.strptime(body.call_back_time, "%H:%M")) threw ValueError on
    # that empty string every time — an unhandled 500 that left the
    # frontend's save button stuck on its loading spinner forever, since
    # there was nothing to catch it client-side either.
    if body.call_back:
        try:
            call_back_time = datetime.strptime(body.call_back_time, "%H:%M").time()
        except ValueError:
            raise HTTPException(status_code=400, detail="Call Back Time must be in HH:mm format.")
        call_back_dt = datetime.combine(body.call_back_date, call_back_time)
    else:
        call_back_dt = datetime.combine(body.call_back_date, time.min)
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_AddTTAction ?, ?, ?, ?, ?, ?, ?, ?",
            body.tt_no, body.action_details, body.action_taken_by, current_user.user_id,
            body.call_back, body.action_on, call_back_dt, call_back_dt,
        )
    return {"success": True}


@router.get("/tt-action/history", response_model=list[TtActionRow])
def get_tt_actions(tt_no: int, cust_id: int) -> list[TtActionRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_GetTTAction ?, ?", tt_no, cust_id)
        rows = rows_to_dicts(cursor)

    return [
        TtActionRow(
            sr_no=r["Actionid"], tt_date=r["TTDate"].isoformat(), received_by=r["Receivedby"] or "",
            issue=r["Issue"] or "", action_id=r["Actionid"], action_date=r["ActionDate"].isoformat(),
            action_details=r["ActionDetails"] or "", action_by=r["ActionBy"] or "", action_on=r["ActionOn"] or "",
        )
        for r in rows
    ]
