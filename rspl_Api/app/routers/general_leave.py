"""Mirrors the leave-workflow page family: Section_General/LeaveApplication.aspx.vb,
LeaveCancellation.aspx.vb, LeaveCancel.aspx.vb, LeaveSanction.aspx.vb,
LeaveSanctionCancellation.aspx.vb, CEO_LeaveSanction.aspx.vb.

NOTE: per user decision (2026-07-10), the source's PROC_SENDEMAIL /
PROC_SendWhatsApp calls (which target hardcoded real staff addresses like
ceo@retailware.info) are intentionally NOT called here. All DB writes are
real; the notification side effects are stubbed out until this is ready to
run against real inboxes.

LeaveSanction/LeaveSanctionCancellation/CEO_LeaveSanction have no
Page_PreInit session check in the source (reached via emailed links, not
requiring portal login) — their endpoints below likewise don't require auth.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

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


class LeaveSanctionArticle(BaseModel):
    applicant_name: str
    ho_name: str
    ho_enabled: bool
    items: list[PendingSanctionItem]


class SubmitSanctionRequest(BaseModel):
    article_id: int
    selected_sr_nos: list[int]
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

        if body.inform_to_user_ids:
            placeholders = ", ".join("?" for _ in body.inform_to_user_ids)
            cursor.execute(
                f"SELECT Email FROM UserMaster WHERE UserID IN ({placeholders})", *body.inform_to_user_ids
            )
            emails = [r["Email"] for r in rows_to_dicts(cursor) if r["Email"]]
            if emails:
                cursor.execute(
                    "UPDATE web_leaveapplication SET InformTo = ? WHERE UserId = ? AND ArticleID = ?",
                    ",".join(emails),
                    current_user.user_id,
                    article_id,
                )

        # NOTE: source sends HO/CEO email + WhatsApp notifications here — intentionally stubbed, see module docstring.

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
            "SELECT SrNo, HO, HOSanctioned FROM web_leaveapplication WHERE UserID = ? AND ArticleID = ?",
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
        )
        for r in article_rows
    ]

    return LeaveSanctionArticle(applicant_name=applicant_name, ho_name=ho_name, ho_enabled=ho_enabled, items=items)


@router.get("/sanction-article/{article_id}", response_model=LeaveSanctionArticle)
def get_leave_sanction_article(article_id: int) -> LeaveSanctionArticle:
    return _get_sanction_article(article_id)


@router.post("/ho-sanction")
def submit_ho_sanction(body: SubmitSanctionRequest) -> dict[str, bool]:
    today = datetime.now()
    with get_cursor() as cursor:
        for sr_no in body.selected_sr_nos:
            cursor.execute(
                "UPDATE web_leaveapplication SET HoSanctioned = 1, HOSanctionedDate = ? WHERE ArticleID = ? AND SrNo = ?",
                today,
                body.article_id,
                sr_no,
            )
        # NOTE: source emails/WhatsApps the applicant + informTo list here — intentionally stubbed.

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
