"""User Rights Management — see app/rights.py for the schema/design notes
and the default-to-'edit' rollout model.

Two audiences share this router:
- Any logged-in user can read their OWN effective rights (GET /me) — needed
  by the Angular app to decide what to show/hide/disable for itself.
- Only the fixed rights-admin allow-list (app.rights.RIGHTS_ADMIN_USER_IDS)
  can list/assign/reset OTHER users' rights (the /admin/* routes below).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.rights import FORM_LABELS, get_effective_access, require_rights_admin

router = APIRouter(prefix="/user-rights", tags=["user-rights"])


class MyRightsResponse(BaseModel):
    forms: dict[str, str]


@router.get("/me", response_model=MyRightsResponse)
def get_my_rights(current_user: CurrentUser = Depends(get_current_user)) -> MyRightsResponse:
    return MyRightsResponse(forms={code: get_effective_access(current_user.user_id, code) for code in FORM_LABELS})


class FormOption(BaseModel):
    code: str
    label: str


class UserOption(BaseModel):
    user_id: int
    name: str


class RightRow(BaseModel):
    user_id: int
    user_name: str
    form_code: str
    form_label: str
    access_level: str


class SetRightRequest(BaseModel):
    user_id: int
    form_code: str
    access_level: str


@router.get("/admin/forms", response_model=list[FormOption])
def list_forms(current_user: CurrentUser = Depends(get_current_user)) -> list[FormOption]:
    require_rights_admin(current_user.user_id)
    return [FormOption(code=code, label=label) for code, label in FORM_LABELS.items()]


@router.get("/admin/users", response_model=list[UserOption])
def list_users(current_user: CurrentUser = Depends(get_current_user)) -> list[UserOption]:
    require_rights_admin(current_user.user_id)
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        rows = rows_to_dicts(cursor)
    return [UserOption(user_id=r["UserID"], name=r["Name"] or "") for r in rows]


@router.get("/admin", response_model=list[RightRow])
def list_rights(current_user: CurrentUser = Depends(get_current_user)) -> list[RightRow]:
    require_rights_admin(current_user.user_id)
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT r.UserId, u.Name, r.FormCode, r.AccessLevel "
            "FROM RSPL_UserFormRights r JOIN UserMaster u ON u.UserID = r.UserId "
            "ORDER BY u.Name, r.FormCode"
        )
        rows = rows_to_dicts(cursor)
    return [
        RightRow(
            user_id=r["UserId"], user_name=r["Name"] or "", form_code=r["FormCode"],
            form_label=FORM_LABELS.get(r["FormCode"], r["FormCode"]), access_level=r["AccessLevel"],
        )
        for r in rows
    ]


@router.put("/admin")
def set_right(body: SetRightRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_rights_admin(current_user.user_id)
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE RSPL_UserFormRights SET AccessLevel = ?, LastEditedByUserId = ?, LastEditedAt = SYSDATETIME() "
            "WHERE UserId = ? AND FormCode = ?",
            body.access_level, current_user.user_id, body.user_id, body.form_code,
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "INSERT INTO RSPL_UserFormRights (UserId, FormCode, AccessLevel, LastEditedByUserId, LastEditedAt) "
                "VALUES (?, ?, ?, ?, SYSDATETIME())",
                body.user_id, body.form_code, body.access_level, current_user.user_id,
            )
    return {"success": True}


# Resets a user back to this form's default (no row = 'edit' for most forms,
# but 'view' for Executive Schedule — see app.rights._DEFAULT_ACCESS_OVERRIDES)
# rather than requiring the admin screen to explicitly set that level itself
# — the two end up looking identical from get_effective_access's point of
# view, but this makes "undo my override" an explicit, honest action instead
# of writing a redundant row.
@router.delete("/admin/{user_id}/{form_code}")
def reset_right(user_id: int, form_code: str, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    require_rights_admin(current_user.user_id)
    with get_cursor() as cursor:
        cursor.execute("DELETE FROM RSPL_UserFormRights WHERE UserId = ? AND FormCode = ?", user_id, form_code)
    return {"success": True}
