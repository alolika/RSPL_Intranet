"""User Rights Management — per-user, per-form View/Edit permissions.

New, greenfield concern: no existing role/permission system exists in this
app (confirmed by inspection — UserType/UserTypeID elsewhere is only used
to *scope which other users* appear in a dropdown/roster, e.g. Executive
Schedule's team rosters, never to gate a user's own access to a feature).

Schema (RSPL_UserFormRights, hand-created directly on the SQL Server
instance): PK (UserId, FormCode), AccessLevel in ('view', 'edit', 'none').

Default-access model (per explicit request): a user with NO row for a given
form defaults to 'edit' — i.e. full access, exactly matching every existing
user's behavior before this feature existed. Nobody loses access the moment
this ships; an admin has to explicitly add a row to dial someone back to
'view' or 'none'. This is why get_effective_access below returns 'edit'
rather than 'none' when no row is found.

Rights-admin allow-list (who can assign rights to OTHERS): a fixed set of
UserIDs (22 = Amol Jadhav, 25 = Alolika Lanke, 142 = Maruti Sangle) named
explicitly per request — there's no existing "IsAdmin" flag on UserMaster to
hook into (isPayrollAdmin is payroll-specific, unrelated). Every other user
can only ever read their OWN effective rights via GET /user-rights/me.
"""

from fastapi import HTTPException

from app.db import get_cursor

FORM_EXEC_SCHEDULE = "EXEC_SCHEDULE"
FORM_HOLIDAY_MASTER = "HOLIDAY_MASTER"
FORM_CALL_HISTORY = "CALL_HISTORY"

FORM_LABELS: dict[str, str] = {
    FORM_EXEC_SCHEDULE: "Executive Schedule",
    FORM_HOLIDAY_MASTER: "Holiday Master",
    FORM_CALL_HISTORY: "Call History Report",
}

RIGHTS_ADMIN_USER_IDS = {22, 25, 142}

_LEVEL_RANK = {"none": 0, "view": 1, "edit": 2}


def get_effective_access(user_id: int, form_code: str) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT AccessLevel FROM RSPL_UserFormRights WHERE UserId = ? AND FormCode = ?",
            user_id, form_code,
        )
        row = cursor.fetchone()
    return row[0] if row else "edit"


def require_access(user_id: int, form_code: str, min_level: str) -> None:
    level = get_effective_access(user_id, form_code)
    if _LEVEL_RANK[level] < _LEVEL_RANK[min_level]:
        raise HTTPException(status_code=403, detail=f"You do not have {min_level} access to this form.")


def require_rights_admin(user_id: int) -> None:
    if user_id not in RIGHTS_ADMIN_USER_IDS:
        raise HTTPException(status_code=403, detail="You do not have permission to manage user rights.")
