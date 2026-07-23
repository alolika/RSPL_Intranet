from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor
from app.security import create_access_token, legacy_password_obfuscate

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login_name: str
    password: str
    security_key: str = ""


class PortalUser(BaseModel):
    user_id: int
    user_name: str
    short_name: str
    home_branch_id: int | None
    bill_date: str | None
    mobile_no: str | None
    email: str | None
    sip_ext_no: str | None
    user_type: str | None


class LoginResponse(BaseModel):
    access_token: str
    user: PortalUser


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    # Queries UserMaster directly on LoginName/Password rather than going
    # through WebProc_ValidateUserCRM — same self-inverse Chr(255-Asc)
    # obfuscation the proc itself compares against (see
    # legacy_password_obfuscate's docstring).
    obfuscated_password = legacy_password_obfuscate(body.password)

    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name, Shortname, Branchid, Mobileno, Email, SIPExtentionNo, UserType "
            "FROM UserMaster WHERE LoginName = ? AND Password = ? AND Enabled = 1",
            body.login_name, obfuscated_password,
        )
        row = first_row_or_none(cursor)

    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login Failed !")

    with get_cursor() as cursor:
        cursor.execute("SELECT BillDate FROM Options")
        bill_date_row = cursor.fetchone()
    bill_date = bill_date_row[0].isoformat() if bill_date_row and bill_date_row[0] else None

    user = PortalUser(
        user_id=row["UserID"],
        user_name=row["Name"] or "",
        short_name=row["Shortname"] or "",
        home_branch_id=row.get("Branchid"),
        bill_date=bill_date,
        mobile_no=row.get("Mobileno"),
        email=row.get("Email"),
        sip_ext_no=row.get("SIPExtentionNo"),
        user_type=str(row.get("UserType")) if row.get("UserType") is not None else None,
    )

    token = create_access_token(
        {
            "sub": str(user.user_id),
            "username": user.user_name,
            "short_name": user.short_name,
            "home_branch_id": user.home_branch_id,
            "user_type": user.user_type,
        }
    )

    return LoginResponse(access_token=token, user=user)
