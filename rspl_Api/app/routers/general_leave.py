"""Mirrors the leave-workflow page family: Section_General/LeaveApplication.aspx.vb,
LeaveCancellation.aspx.vb, LeaveCancel.aspx.vb, LeaveSanction.aspx.vb,
LeaveSanctionCancellation.aspx.vb, CEO_LeaveSanction.aspx.vb.

NOTE: per user decision (2026-07-10), the source's PROC_SENDEMAIL /
PROC_SendWhatsApp calls (which target hardcoded real staff addresses like
ceo@retailware.info) were intentionally NOT called here. All DB writes are
real; the notification side effects were stubbed out until ready to run
against real inboxes.

Update (2026-07-27): per user request, submit_leave_application() now sends
real notifications to the selected HOD and every selected Inform-To user via
PROC_SENDEMAIL (confirmed live: it queues a row into SMSOUTBOX +
EMAILATTACHMENTS for a separate mail-sender service — EMAILENABLED=1 in this
DB, so this queues genuinely-sent email). The other endpoints in this module
(request_ho_cancel_leave, submit_ceo_sanction, submit_ho_cancel_sanction)
remain intentionally stubbed — out of scope for that request, revisit the
same way if asked.

Update (2026-07-27, same day): the HOD's email is distinct from Inform To's —
the HOD gets an action link (`{frontend_base_url}/leave-sanction?si={ArticleID}`,
the unauthenticated LeaveSanction page's `si` query param, see that route's
docstring) so they can sanction/reject directly from the email, matching the
source's emailed-link workflow. Inform-To recipients get an informational-only
copy with no link/action. If the HOD is also selected in Inform To, they only
get the action-link version (deduped by email) — not a second, action-less
copy.

LeaveSanction/LeaveSanctionCancellation/CEO_LeaveSanction have no
Page_PreInit session check in the source (reached via emailed links, not
requiring portal login) — their endpoints below likewise don't require auth.

Update (2026-07-27, later same day): per user decision, added a real
HORejected/HORejectedDate pair of columns to web_leaveapplication (this DB
had no rejected state at all before — HOSanctioned=0 meant "not yet decided"
and "explicitly rejected" were indistinguishable). submit_ho_sanction() now
takes a per-item decisions list (approve/reject, see SanctionDecision) instead
of a flat selected_sr_nos list, and always emails the applicant once per
submission with a subject that says Approved/Rejected/Status Update
(mixed), listing which item(s) fell into which bucket plus the HOD's remark
if any. Scoped to submit_ho_sanction only, per the request being specifically
about "when the HOD takes action" — submit_ceo_sanction/SubmitSanctionRequest
(the CEO's own, separate sanction step) is untouched and still uses the old
flat selected_sr_nos shape; revisit the same way if CEO notifications are
asked for later.
"""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/general/leave", tags=["general-leave"])


class LookupOption(BaseModel):
    label: str
    value: int


class StagedLeaveRow(BaseModel):
    """from_date/to_date arrive pre-formatted as dd-MMM-yyyy display text
    (e.g. "10-Jul-2026") from the Angular form, same as the source app's
    Format(..., "dd-MMM-yyyy") — not ISO. Passed straight through to SQL
    Server, which parses this format fine for datetime params."""

    user_id: int
    reason: str
    from_date: str
    to_date: str
    days: str
    half_days: str


class SubmitLeaveApplicationRequest(BaseModel):
    rows: list[StagedLeaveRow]
    ho: str
    ho_user_id: int | None = None
    inform_to_user_ids: list[int]


class MyLeaveRow(BaseModel):
    sr_no: int
    leave_id: int
    app_date: str
    for_days: str
    from_date: str
    to_date: str
    status: str
    cancellation: str


class RequestHoCancelRequest(BaseModel):
    leave_id: int
    from_date: date
    to_date: date
    remark: str


class PendingSanctionItem(BaseModel):
    sr_no: int
    description: str
    already_sanctioned: bool
    already_rejected: bool


class LeaveSanctionArticle(BaseModel):
    applicant_name: str
    ho_name: str
    ho_enabled: bool
    items: list[PendingSanctionItem]


class SubmitSanctionRequest(BaseModel):
    article_id: int
    selected_sr_nos: list[int]
    remark: str


class SanctionDecision(BaseModel):
    sr_no: int
    decision: Literal["approve", "reject"]


class SubmitHoSanctionRequest(BaseModel):
    article_id: int
    decisions: list[SanctionDecision]
    remark: str


class LeaveCancelSanctionRequest(BaseModel):
    applicant_name: str
    ho_name: str
    cancel_from_date: str | None
    cancel_to_date: str | None
    cancel_reason: str
    already_cancelled_by_ho: bool


class SubmitHoCancelSanctionRequest(BaseModel):
    leave_id: int
    user_id: int
    remark: str


# --- Shared lookups ---


@router.get("/ho-enabled")
def is_ho_enabled_for_user(current_user: CurrentUser = Depends(get_current_user)) -> bool:
    with get_cursor() as cursor:
        cursor.execute("SELECT HOEnabled FROM UserMaster WHERE UserID = ?", current_user.user_id)
        row = cursor.fetchone()
    return bool(row[0]) if row else False


@router.get("/ho-options", response_model=list[LookupOption])
def get_ho_options(current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT UserID, Name FROM UserMaster
            WHERE UserID = (SELECT ParentEmpID FROM UserMaster WHERE UserID = ?)
            ORDER BY Name
            """,
            current_user.user_id,
        )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/inform-to-options", response_model=list[LookupOption])
def get_inform_to_options() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


# --- Mirrors LeaveApplication.aspx.vb ---


@router.post("/application")
def submit_leave_application(
    body: SubmitLeaveApplicationRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    today = datetime.now()

    # UI restricts the From Date picker to no earlier than (today - 15 days) —
    # no upper bound, future dates are allowed — but that's client-side only;
    # re-validate here so a hand-crafted request can't submit an older date.
    earliest_allowed = today.date() - timedelta(days=15)
    for row in body.rows:
        try:
            from_date = datetime.strptime(row.from_date, "%d-%b-%Y").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid From Date: {row.from_date}")
        if from_date < earliest_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"From Date cannot be earlier than {earliest_allowed:%d-%b-%Y}.",
            )

    with get_cursor() as cursor:
        cursor.execute("SELECT ISNULL(MAX(ArticleId), 0) + 1 FROM Web_Article")
        article_id = cursor.fetchone()[0]

        for sr_no, row in enumerate(body.rows, start=1):
            description = (
                f"{current_user.username} waiting for leave approval for {row.days} days "
                f"from {row.from_date} to {row.to_date}"
            )
            cursor.execute(
                "SET QUOTED_IDENTIFIER OFF; EXEC webproc_WebArticleAdd ?, ?, ?, ?, ?, ?, ?, ?, ?",
                article_id,
                3,
                today,
                "Leave",
                "HR/Accounts",
                description,
                1,
                current_user.user_id,
                sr_no,
            )

            for_days = row.days + (f" ({row.half_days})" if row.half_days else "")
            cursor.execute(
                "SET QUOTED_IDENTIFIER OFF; EXEC webproc_LeaveApplicationAdd ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
                0,
                current_user.user_id,
                today,
                for_days,
                row.reason,
                row.from_date,
                row.to_date,
                body.ho,
                article_id,
                sr_no,
            )

        inform_to_emails: list[str] = []
        if body.inform_to_user_ids:
            placeholders = ", ".join("?" for _ in body.inform_to_user_ids)
            cursor.execute(
                f"SELECT Email FROM UserMaster WHERE UserID IN ({placeholders})", *body.inform_to_user_ids
            )
            inform_to_emails = [r["Email"] for r in rows_to_dicts(cursor) if r["Email"]]
            if inform_to_emails:
                cursor.execute(
                    "UPDATE web_leaveapplication SET InformTo = ? WHERE UserId = ? AND ArticleID = ?",
                    ",".join(inform_to_emails),
                    current_user.user_id,
                    article_id,
                )

        ho_email = None
        if body.ho_user_id:
            cursor.execute("SELECT Email FROM UserMaster WHERE UserID = ?", body.ho_user_id)
            row = cursor.fetchone()
            ho_email = row[0] if row and row[0] else None

        summary = "\n".join(
            f"{current_user.username} has applied for leave for {row.days} day(s)"
            f"{f' ({row.half_days})' if row.half_days else ''} from {row.from_date} to {row.to_date}. "
            f"Reason: {row.reason}."
            for row in body.rows
        )

        # The HOD gets the action-link email (they must sanction/reject); Inform To is
        # notification-only. If the HOD is also in Inform To, they only get the HOD
        # version — dedupe so they don't get a second, action-less copy.
        if ho_email:
            sanction_link = f"{settings.frontend_base_url}/leave-sanction?si={article_id}"
            ho_subject = f"Leave Application - {current_user.username} (Approval Required)"
            ho_message = (
                f"{summary}\n\n"
                f"Please click the link below to review and sanction/reject this leave request:\n"
                f"{sanction_link}"
            )
            cursor.execute(
                "EXEC PROC_SENDEMAIL @EMAILID=?, @SUBJECT=?, @MESSAGE=?, @USERID=?",
                ho_email,
                ho_subject,
                ho_message,
                current_user.user_id,
            )

        inform_only_emails = [e for e in dict.fromkeys(inform_to_emails) if e != ho_email]
        if inform_only_emails:
            inform_subject = f"Leave Application - {current_user.username} (For Your Information)"
            inform_message = f"{summary}\n\nThis is for your information only; no action is required from you."
            for email in inform_only_emails:
                cursor.execute(
                    "EXEC PROC_SENDEMAIL @EMAILID=?, @SUBJECT=?, @MESSAGE=?, @USERID=?",
                    email,
                    inform_subject,
                    inform_message,
                    current_user.user_id,
                )

    return {"success": True}


# --- Mirrors LeaveCancellation.aspx.vb ---


@router.get("/my-pending", response_model=list[MyLeaveRow])
def get_my_pending_leaves(current_user: CurrentUser = Depends(get_current_user)) -> list[MyLeaveRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT BillDate FROM Options")
        bill_date = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT SrNo, LeaveID, AppDate, ForDays, Reason, FromDate, ToDate,
                   CASE WHEN HOSanctioned = 0 THEN 'Pending' ELSE 'Sanction By HOD' END AS Status,
                   CASE WHEN HOSanctioned = 0 THEN 'Cancel' ELSE 'Send Request HOD To Cancel Leave' END AS Cancellation
            FROM web_leaveapplication
            WHERE CancelledByApplicant = 0 AND CancelledbyHO = 0 AND FromDate >= ? AND UserID = ?
            """,
            bill_date,
            current_user.user_id,
        )
        rows = rows_to_dicts(cursor)

    return [
        MyLeaveRow(
            sr_no=r["SrNo"],
            leave_id=r["LeaveID"],
            app_date=r["AppDate"].isoformat(),
            for_days=r["ForDays"] or "",
            from_date=r["FromDate"].isoformat(),
            to_date=r["ToDate"].isoformat(),
            status=r["Status"],
            cancellation=r["Cancellation"],
        )
        for r in rows
    ]


@router.post("/cancel-my/{leave_id}")
def cancel_my_leave(leave_id: int, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE web_leaveapplication SET CancelledByApplicant = 1 WHERE LeaveID = ? AND UserId = ?",
            leave_id,
            current_user.user_id,
        )
    return {"success": True}


# --- Mirrors LeaveCancel.aspx.vb ---


@router.post("/request-ho-cancel")
def request_ho_cancel_leave(
    body: RequestHoCancelRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT FromDate, ToDate FROM web_leaveapplication WHERE UserID = ? AND LeaveID = ?",
            current_user.user_id,
            body.leave_id,
        )
        row = cursor.fetchone()
        if row is None:
            return {"success": False}
        original_from, original_to = row[0].date(), row[1].date()
        if body.from_date < original_from or body.to_date > original_to:
            return {"success": False}

        cursor.execute(
            """
            UPDATE web_leaveapplication SET CancelFromDate = ?, CancelToDate = ?, CancelReason = ?
            WHERE Userid = ? AND LeaveID = ?
            """,
            body.from_date,
            body.to_date,
            body.remark,
            current_user.user_id,
            body.leave_id,
        )
        # NOTE: source emails CEO/HO here — intentionally stubbed, see module docstring.

    return {"success": True}


# --- Shared by LeaveSanction.aspx.vb and CEO_LeaveSanction.aspx.vb ---


def _get_sanction_article(article_id: int) -> LeaveSanctionArticle:
    with get_cursor() as cursor:
        cursor.execute("SELECT SrNo, Description, UserID FROM Web_Article WHERE ArticleID = ?", article_id)
        article_rows = rows_to_dicts(cursor)
        applicant_user_id = article_rows[0]["UserID"] if article_rows else 0

        cursor.execute(
            "SELECT Name, HOEnabled FROM UserMaster WHERE UserID = ?", applicant_user_id
        )
        user_row = cursor.fetchone()
        applicant_name = user_row[0] if user_row else ""
        ho_enabled = bool(user_row[1]) if user_row else False

        cursor.execute(
            "SELECT SrNo, HO, HOSanctioned, HORejected FROM web_leaveapplication WHERE UserID = ? AND ArticleID = ?",
            applicant_user_id,
            article_id,
        )
        leave_rows = {r["SrNo"]: r for r in rows_to_dicts(cursor)}

    ho_name = next(iter(leave_rows.values()))["HO"].strip() if leave_rows else ""

    items = [
        PendingSanctionItem(
            sr_no=r["SrNo"],
            description=r["Description"] or "",
            already_sanctioned=bool(leave_rows.get(r["SrNo"], {}).get("HOSanctioned")),
            already_rejected=bool(leave_rows.get(r["SrNo"], {}).get("HORejected")),
        )
        for r in article_rows
    ]

    return LeaveSanctionArticle(applicant_name=applicant_name, ho_name=ho_name, ho_enabled=ho_enabled, items=items)


@router.get("/sanction-article/{article_id}", response_model=LeaveSanctionArticle)
def get_leave_sanction_article(article_id: int) -> LeaveSanctionArticle:
    return _get_sanction_article(article_id)


@router.post("/ho-sanction")
def submit_ho_sanction(body: SubmitHoSanctionRequest) -> dict[str, bool]:
    today = datetime.now()
    approved_descriptions: list[str] = []
    rejected_descriptions: list[str] = []

    with get_cursor() as cursor:
        cursor.execute("SELECT SrNo, Description, UserID FROM Web_Article WHERE ArticleID = ?", body.article_id)
        article_rows = rows_to_dicts(cursor)
        desc_by_sr_no = {r["SrNo"]: r["Description"] or "" for r in article_rows}
        applicant_user_id = article_rows[0]["UserID"] if article_rows else 0

        for decision in body.decisions:
            if decision.decision == "approve":
                cursor.execute(
                    "UPDATE web_leaveapplication SET HoSanctioned = 1, HOSanctionedDate = ?, HORejected = 0 "
                    "WHERE ArticleID = ? AND SrNo = ?",
                    today,
                    body.article_id,
                    decision.sr_no,
                )
                approved_descriptions.append(desc_by_sr_no.get(decision.sr_no, ""))
            else:
                cursor.execute(
                    "UPDATE web_leaveapplication SET HORejected = 1, HORejectedDate = ?, HoSanctioned = 0 "
                    "WHERE ArticleID = ? AND SrNo = ?",
                    today,
                    body.article_id,
                    decision.sr_no,
                )
                rejected_descriptions.append(desc_by_sr_no.get(decision.sr_no, ""))

        if approved_descriptions or rejected_descriptions:
            cursor.execute("SELECT Email FROM UserMaster WHERE UserID = ?", applicant_user_id)
            row = cursor.fetchone()
            applicant_email = row[0] if row and row[0] else None

            if applicant_email:
                if approved_descriptions and rejected_descriptions:
                    subject = "Leave Application - Status Update"
                elif approved_descriptions:
                    subject = "Leave Application - Approved"
                else:
                    subject = "Leave Application - Rejected"

                sections = []
                if approved_descriptions:
                    sections.append("Approved:\n" + "\n".join(f"- {d}" for d in approved_descriptions))
                if rejected_descriptions:
                    sections.append("Rejected:\n" + "\n".join(f"- {d}" for d in rejected_descriptions))
                if body.remark.strip():
                    sections.append(f"Remark: {body.remark.strip()}")
                message = "\n\n".join(sections)

                cursor.execute(
                    "EXEC PROC_SENDEMAIL @EMAILID=?, @SUBJECT=?, @MESSAGE=?",
                    applicant_email,
                    subject,
                    message,
                )

    return {"success": True}


@router.get("/ceo-sanction-article/{article_id}", response_model=LeaveSanctionArticle)
def get_ceo_sanction_article(article_id: int) -> LeaveSanctionArticle:
    return _get_sanction_article(article_id)


@router.post("/ceo-sanction")
def submit_ceo_sanction(body: SubmitSanctionRequest) -> dict[str, bool]:
    today = datetime.now()
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID FROM Web_Article WHERE ArticleID = ?", body.article_id)
        row = cursor.fetchone()
        applicant_user_id = row[0] if row else 0

        cursor.execute(
            "SELECT ForDays, FromDate, ToDate FROM web_leaveapplication WHERE ArticleID = ? AND UserID = ?",
            body.article_id,
            applicant_user_id,
        )
        leave_row = cursor.fetchone()

        if body.selected_sr_nos:
            cursor.execute(
                "UPDATE web_leaveapplication SET CEOSanctioned = 1, CEOSanctionDate = ? WHERE ArticleID = ?",
                today,
                body.article_id,
            )
            cursor.execute("UPDATE web_article SET Enabled = 0 WHERE ArticleID = ?", body.article_id)

            if leave_row:
                cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ?", applicant_user_id)
                name_row = cursor.fetchone()
                applicant_name = name_row[0] if name_row else ""
                description = (
                    f"{applicant_name} on leave for {leave_row[0]} days "
                    f"from {leave_row[1].strftime('%d-%b-%Y')} to {leave_row[2].strftime('%d-%b-%Y')}"
                )
                cursor.execute(
                    "SET QUOTED_IDENTIFIER OFF; EXEC webproc_WebArticleAdd ?, ?, ?, ?, ?, ?, ?, ?, ?",
                    0,
                    3,
                    today,
                    "Leave",
                    "HR/Accounts",
                    description,
                    1,
                    applicant_user_id,
                    1,
                )
        # NOTE: source emails HO/applicant/akshayaj@retailware.info here — intentionally stubbed.

    return {"success": True}


# --- Mirrors LeaveSanctionCancellation.aspx.vb ---


@router.get("/cancel-sanction-request", response_model=LeaveCancelSanctionRequest)
def get_leave_cancel_sanction_request(leave_id: int, user_id: int, sr_no: int) -> LeaveCancelSanctionRequest:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT U.Name, L.HO, L.CancelFromDate, L.CancelToDate, L.CancelReason, L.CancelledbyHO
            FROM web_leaveapplication L INNER JOIN UserMaster U ON L.UserId = U.UserID
            WHERE L.UserID = ? AND L.LeaveID = ? AND L.SrNo = ?
            """,
            user_id,
            leave_id,
            sr_no,
        )
        row = cursor.fetchone()

    if row is None:
        return LeaveCancelSanctionRequest(
            applicant_name="", ho_name="", cancel_from_date=None, cancel_to_date=None,
            cancel_reason="", already_cancelled_by_ho=False,
        )

    return LeaveCancelSanctionRequest(
        applicant_name=row[0] or "",
        ho_name=(row[1] or "").strip(),
        cancel_from_date=row[2].isoformat() if row[2] else None,
        cancel_to_date=row[3].isoformat() if row[3] else None,
        cancel_reason=row[4] or "",
        already_cancelled_by_ho=bool(row[5]),
    )


@router.post("/ho-cancel-sanction")
def submit_ho_cancel_sanction(body: SubmitHoCancelSanctionRequest) -> dict[str, bool]:
    today = datetime.now()
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE web_leaveapplication SET CancelledByHO = 1, HOSanctionedDate = ? WHERE LeaveID = ?",
            today,
            body.leave_id,
        )
        # NOTE: source emails the applicant + akshayaj@retailware.info + amc2@retailware.info here — intentionally stubbed.

    return {"success": True}
