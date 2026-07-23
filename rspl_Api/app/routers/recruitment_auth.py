"""Mirrors Section_Recruitment/Login.aspx.vb — a separate CandidateID/
AdminCode session model from the main portal (WebProc_ValidateUserDISC vs
WebProc_ValidateUserCRM). Password uses the same self-inverse Chr(255-Asc)
obfuscation as the main portal (clsDBLayer.PasswordEncrypt/PasswordDecrypt
are byte-for-byte identical implementations in the source, so login applies
the same transform to the submitted plaintext before comparing).

Real finding worth remembering: WebProc_ValidateUserDISC's SELECT hardcodes
`4 AdminCode` — the commented-out `isnull(UM.UserType,0) AdminCode` alternative
is dead code. Every successfully authenticated candidate/recruiter gets
AdminCode=4 (i.e. IsAdmin=true) in production today; there is no real
non-admin login despite the source's admin-gated pages (DISCCandidate,
DiscCandidateAdd) checking Session("AdminCode")=0. Not something to "fix"
here — replicated faithfully by just returning whatever the proc gives back.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor
from app.security import create_access_token, legacy_password_obfuscate

router = APIRouter(prefix="/recruitment/auth", tags=["recruitment-auth"])


class RecruitmentLoginRequest(BaseModel):
    login_name: str
    password: str


class RecruitmentUser(BaseModel):
    candidate_id: int
    candidate_name: str
    admin_code: int
    is_admin: bool
    email: str
    mobile_no: str
    qualification: str
    department: str
    show_answer: bool
    question_paper_id: int


class RecruitmentLoginResponse(BaseModel):
    access_token: str
    user: RecruitmentUser


@router.post("/login", response_model=RecruitmentLoginResponse)
def login(body: RecruitmentLoginRequest) -> RecruitmentLoginResponse:
    obfuscated_password = legacy_password_obfuscate(body.password)
    with get_cursor() as cursor:
        cursor.execute("EXEC WebProc_ValidateUserDISC ?, ?", body.login_name, obfuscated_password)
        row = first_row_or_none(cursor)

    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login Failed !")

    admin_code = row.get("ADminCode") or 0
    user = RecruitmentUser(
        candidate_id=row["CandidateID"], candidate_name=row.get("username") or "", admin_code=admin_code,
        is_admin=admin_code != 0, email=row.get("EmailID") or "", mobile_no=row.get("MobileNO") or "",
        qualification=row.get("Qualification") or "", department=row.get("Department") or "",
        show_answer=bool(row.get("ShowAnswer")), question_paper_id=row.get("QuestionPaperID") or 0,
    )
    token = create_access_token({"sub": str(user.candidate_id), "admin_code": user.admin_code, "typ": "candidate"})
    return RecruitmentLoginResponse(access_token=token, user=user)
