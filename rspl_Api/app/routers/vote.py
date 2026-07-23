from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.routers.developer import LookupOption

router = APIRouter(prefix="/developer/vote", tags=["vote"])

_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_share_date(d: date) -> str:
    return f"{d.day:02d}-{_MONTH_ABBR[d.month]}-{d.year}"


def _parse_share_date(text: str) -> date:
    day_str, mon_str, year_str = text.split("-")
    return date(int(year_str), _MONTH_ABBR.index(mon_str), int(day_str))


class ShareStatus(BaseModel):
    share_id: int
    coordinator_id: int
    is_closed: bool
    share_date: str | None
    has_any_share: bool
    is_current_user_coordinator: bool


class OpenShareRequest(BaseModel):
    branch_id: int
    date: date


class CloseShareRequest(BaseModel):
    branch_id: int


class SubmitShareRequest(BaseModel):
    branch_id: int
    share_id: int
    share_text: str


class VoteRequest(BaseModel):
    share_id: int
    vote_to_user_id: int


class VoteResultRow(BaseModel):
    user_name: str
    my_correct_share: str
    vote_count: int


_COORDINATOR_OPTION_NAME = {1: "NagarShareCoordinatorUserID", 2: "PuneShareCoordinatorUserID"}


def _latest_share(cursor, branch_id: int) -> dict | None:
    cursor.execute(
        "SELECT TOP 1 ShareID, Date, CoordinatorID, IsClosed FROM Voting_ShareMaster WHERE BranchID = ? ORDER BY ShareID DESC",
        branch_id,
    )
    rows = rows_to_dicts(cursor)
    return rows[0] if rows else None


def _branch_coordinator_id(cursor, branch_id: int) -> int:
    option_name = _COORDINATOR_OPTION_NAME.get(branch_id)
    if option_name is None:
        return 0
    cursor.execute("SELECT Value FROM Options2 WHERE Name = ?", option_name)
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] else 0


# --- Mirrors Section_Developer/VoteForBestShare.aspx.vb ---


@router.get("/status", response_model=ShareStatus)
def get_share_status(branch_id: int, current_user: CurrentUser = Depends(get_current_user)) -> ShareStatus:
    with get_cursor() as cursor:
        coordinator_id = _branch_coordinator_id(cursor, branch_id)
        latest = _latest_share(cursor, branch_id)

    return ShareStatus(
        share_id=latest["ShareID"] if latest else 0,
        coordinator_id=coordinator_id,
        is_closed=bool(latest["IsClosed"]) if latest else True,
        share_date=_parse_share_date(latest["Date"]).isoformat() if latest else None,
        has_any_share=latest is not None,
        is_current_user_coordinator=current_user.user_id == coordinator_id,
    )


@router.post("/open-share")
def open_share(
    body: OpenShareRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_InsertShareMaster @ShareID=?, @Date=?, @CoordinatorID=?, @BranchID=?, @IsClosed=?, @Transtype=?",
            0,
            _format_share_date(body.date),
            current_user.user_id,
            body.branch_id,
            0,
            "Insert",
        )
        row = cursor.fetchone()

    already_open = row is not None and row[0] == "101"
    return {"success": not already_open, "alreadyOpen": already_open}


@router.post("/close-share")
def close_share(
    body: CloseShareRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        latest = _latest_share(cursor, body.branch_id)
        if latest is None:
            return {"success": False}

        cursor.execute(
            "EXEC Proc_InsertShareMaster @ShareID=?, @Date=?, @CoordinatorID=?, @BranchID=?, @IsClosed=?, @Transtype=?",
            latest["ShareID"],
            latest["Date"],
            current_user.user_id,
            body.branch_id,
            1,
            "Update",
        )
    return {"success": True}


@router.get("/has-submitted")
def has_submitted_share(share_id: int, current_user: CurrentUser = Depends(get_current_user)) -> bool:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM Voting_ShareDetails WHERE ShareID = ? AND UserID = ?",
            share_id,
            current_user.user_id,
        )
        return cursor.fetchone() is not None


@router.post("/submit-share")
def submit_share(
    body: SubmitShareRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, str]:
    with get_cursor() as cursor:
        cursor.execute("SELECT Date FROM Voting_ShareMaster WHERE ShareID = ?", body.share_id)
        row = cursor.fetchone()
        shared_on = _parse_share_date(row[0]) if row else date.today()

        cursor.execute(
            "EXEC Proc_InsertShareDetail @BranchID=?, @UserID=?, @MyShare=?, @MyVoteToUserID=?, "
            "@Transtype=?, @SharedOn=?, @VotedOn=?",
            body.branch_id,
            current_user.user_id,
            body.share_text,
            0,
            "Insert",
            shared_on,
            date.today(),
        )
        result = rows_to_dicts(cursor)

    result_msg = result[0]["ResultMsg"] if result else "Share submitted successfully!"
    return {"resultMsg": result_msg}


@router.post("/vote")
def vote_for_employee(
    body: VoteRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, str]:
    with get_cursor() as cursor:
        cursor.execute("SELECT BranchID FROM Voting_ShareMaster WHERE ShareID = ?", body.share_id)
        row = cursor.fetchone()
        branch_id = row[0] if row else 0

        cursor.execute(
            "EXEC Proc_InsertShareDetail @BranchID=?, @UserID=?, @MyShare=?, @MyVoteToUserID=?, @Transtype=?",
            branch_id,
            current_user.user_id,
            "",
            body.vote_to_user_id,
            "Update",
        )
        result = rows_to_dicts(cursor)

    result_msg = result[0]["ResultMsg"] if result else "Vote recorded successfully!"
    return {"resultMsg": result_msg}


@router.get("/voting-employees", response_model=list[LookupOption])
def get_voting_employees(
    share_id: int, current_user: CurrentUser = Depends(get_current_user)
) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT Name, UserID FROM usermaster
            WHERE enabled = 1
              AND userid IN (SELECT UserID FROM voting_sharedetails WHERE ShareID = ?)
              AND userid <> ?
            ORDER BY Name
            """,
            share_id,
            current_user.user_id,
        )
        return [LookupOption(label=r["Name"], value=str(r["UserID"])) for r in rows_to_dicts(cursor)]


@router.get("/employee-share-text")
def get_employee_share_text(share_id: int, user_id: int) -> dict[str, str]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT MyShare FROM voting_sharedetails WHERE ShareID = ? AND UserID = ?", share_id, user_id
        )
        row = cursor.fetchone()
    return {"text": row[0] if row else ""}


@router.get("/closed-share-dates", response_model=list[LookupOption])
def get_closed_share_dates(branch_id: int) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Date, ShareID FROM voting_sharemaster WHERE branchid = ? AND isclosed = 1 ORDER BY ShareID",
            branch_id,
        )
        return [LookupOption(label=r["Date"], value=str(r["ShareID"])) for r in rows_to_dicts(cursor)]


@router.get("/results", response_model=list[VoteResultRow])
def get_vote_results(share_id: int) -> list[VoteResultRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT Name FROM usermaster WHERE enabled = 1 AND userid = vsd.MyVoteToUserID) AS UserName,
                (SELECT MyShare FROM voting_sharedetails WHERE userid = vsd.MyVoteToUserID AND ShareID = vsd.ShareID) AS MyCorrectShare,
                COUNT(vsd.MyVoteToUserID) AS VoteCount
            FROM voting_sharedetails vsd
            WHERE vsd.ShareID = ?
            GROUP BY vsd.MyVoteToUserID, vsd.ShareID
            HAVING vsd.MyVoteToUserID > 0
            ORDER BY VoteCount DESC
            """,
            share_id,
        )
        rows = rows_to_dicts(cursor)

    return [
        VoteResultRow(
            user_name=r["UserName"] or "",
            my_correct_share=r["MyCorrectShare"] or "",
            vote_count=r["VoteCount"],
        )
        for r in rows
    ]
