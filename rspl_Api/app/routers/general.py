"""Mirrors Section_General/*.aspx.vb (the non-leave, non-report-engine pages)."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/general", tags=["general"])


class LookupOption(BaseModel):
    label: str
    value: int


class AttendanceRow(BaseModel):
    user_id: int
    user_name: str
    days: dict[str, str]


class AssetTrackRow(BaseModel):
    date: str
    from_user: str
    to_user: str
    asset_type: str
    remark: str


class FeedbackRequest(BaseModel):
    name: str
    email: str
    city: str
    subject: str
    message: str


class AssetTrackRequest(BaseModel):
    asset_type: str
    from_user_id: int
    remark: str


# --- Shared lookups ---


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/other-users", response_model=list[LookupOption])
def get_other_users(exclude_user_id: int) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 AND UserID <> ? ORDER BY Name",
            exclude_user_id,
        )
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/months", response_model=list[LookupOption])
def get_months() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT MonthID, MonthName FROM MonthMaster ORDER BY MonthID")
        return [LookupOption(label=r["MonthName"], value=r["MonthID"]) for r in rows_to_dicts(cursor)]


@router.get("/years", response_model=list[LookupOption])
def get_years() -> list[LookupOption]:
    current = datetime.now().year
    return [LookupOption(label=str(y), value=y) for y in (current - 1, current, current + 1)]


# --- Mirrors Section_General/Feedback.aspx.vb ---


@router.post("/feedback")
def submit_feedback(body: FeedbackRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "SET QUOTED_IDENTIFIER OFF; EXEC webproc_FeedbackAdd ?, ?, ?, ?, ?, ?",
            body.name,
            body.email,
            body.city,
            body.subject,
            body.message,
            current_user.user_id,
        )
    return {"success": True}


# --- Mirrors Section_General/AssetTrack.aspx.vb ---


@router.get("/asset-types", response_model=list[LookupOption])
def get_asset_types() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ID, MasterValue FROM Web_LittleMaster WHERE MasterName = 'AssetType'")
        return [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.get("/asset-track", response_model=list[AssetTrackRow])
def get_asset_track_rows() -> list[AssetTrackRow]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT w.Date, u1.Name AS FromUser, u2.Name AS ToUser, w.AssetType, w.Remark
            FROM Web_Assettrack w
            INNER JOIN UserMaster u1 ON w.FromUserID = u1.UserID
            INNER JOIN UserMaster u2 ON w.ToUserID = u2.UserID
            ORDER BY w.VoucherNo DESC
            """
        )
        rows = rows_to_dicts(cursor)

    return [
        AssetTrackRow(
            date=r["Date"].isoformat(),
            from_user=r["FromUser"] or "",
            to_user=r["ToUser"] or "",
            asset_type=r["AssetType"] or "",
            remark=r["Remark"] or "",
        )
        for r in rows
    ]


@router.post("/asset-track")
def add_asset_track(
    body: AssetTrackRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "SET QUOTED_IDENTIFIER OFF; EXEC webproc_AssettrackAdd ?, ?, ?, ?",
            body.asset_type,
            body.from_user_id,
            current_user.user_id,
            body.remark,
        )
    return {"success": True}


# --- Mirrors Section_General/EmpAttendance.aspx.vb ---


@router.get("/attendance", response_model=list[AttendanceRow])
def get_attendance(user_id: int, year: int, month: int) -> list[AttendanceRow]:
    day_columns = [str(d) for d in range(1, 32)]
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_getuserschedule ?, ?, ?",
            user_id,
            year,
            month,
        )
        rows = rows_to_dicts(cursor)

    return [
        AttendanceRow(
            user_id=r["UserID"],
            user_name=r["UserName"] or "",
            days={d: r[d] for d in day_columns if r.get(d) is not None},
        )
        for r in rows
    ]
