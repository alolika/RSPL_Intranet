"""Engineering Hub - per-user menu rights (Menu Rights page).

Engineering Hub-only and separate from the portal-wide User Rights feature
(app/rights.py / user_rights.py) on purpose: that one is per-form View/Edit
with a "no row = full access" default, whereas here the requirement is the
opposite — a user sees ONLY the Engineering Hub menus an admin has granted.

Schema (EngHub_UserMenuRights, see database/enghub_menu_rights.sql):
PK (UserId, MenuCode). A row means "granted"; no row means hidden.

Menu Rights admins (MENU_RIGHTS_ADMIN_USER_IDS below — its own fixed list,
deliberately NOT the portal User Rights allow-list in app.rights) are the
only ones who see the Menu Rights page and can assign. For their
own menus they follow their granted rows exactly like everyone else; only the
Menu Rights page itself is always open to them, so they can't lock themselves
out.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub/menu-rights", tags=["engineering-hub-menu-rights"])

# 24 = Navnath Lanke, 142 = Maruti Sangle — the only users who get the Menu
# Rights page, per explicit request.
MENU_RIGHTS_ADMIN_USER_IDS = {24, 142}

# Must match MENU_* codes in the Angular engineering-hub-nav.ts exactly.
# Order here is the order shown on the Menu Rights page.
MENU_LABELS: dict[str, str] = {
    "DASHBOARD": "Dashboard",
    "FEATURES": "Features",
    "DEV_ITEMS": "Development Items",
    "TASKS": "Tasks",
    "ACTIVITIES": "Activities",
    "DECISIONS": "Decisions",
    "RELEASES": "Releases",
    "REPORTS": "Reports",
    "MASTERS": "Masters",
}


class MyMenusResponse(BaseModel):
    is_admin: bool
    menus: list[str]


class MenuOption(BaseModel):
    code: str
    label: str


class UserMenuRow(BaseModel):
    user_id: int
    name: str
    menus: list[str]


class SetMenusRequest(BaseModel):
    menus: list[str]


def _is_admin(user_id: int) -> bool:
    return user_id in MENU_RIGHTS_ADMIN_USER_IDS


def _require_admin(user_id: int) -> None:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="You do not have permission to manage Engineering Hub menu rights.")


def require_menu(user_id: int, menu_code: str) -> None:
    """403 unless the user has been granted `menu_code`.
    Used by the create/save endpoints behind the inline "+" buttons (Module,
    Feature, Dev Item), so hiding those buttons isn't only cosmetic. Applies
    to admins too — they follow their own granted menus like anyone else."""
    with get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM EngHub_UserMenuRights WHERE UserId = ? AND MenuCode = ?", user_id, menu_code)
        granted = cursor.fetchone() is not None
    if not granted:
        raise HTTPException(status_code=403, detail=f"You do not have rights to the {MENU_LABELS[menu_code]} menu.")


@router.get("/me", response_model=MyMenusResponse)
def get_my_menus(current_user: CurrentUser = Depends(get_current_user)) -> MyMenusResponse:
    with get_cursor() as cursor:
        cursor.execute("SELECT MenuCode FROM EngHub_UserMenuRights WHERE UserId = ?", current_user.user_id)
        granted = {r[0] for r in cursor.fetchall()}
    # Filter through MENU_LABELS so a stale/unknown code in the table is
    # ignored, and the result keeps the canonical menu order.
    return MyMenusResponse(is_admin=_is_admin(current_user.user_id), menus=[code for code in MENU_LABELS if code in granted])


@router.get("/admin/menus", response_model=list[MenuOption])
def list_menus(current_user: CurrentUser = Depends(get_current_user)) -> list[MenuOption]:
    _require_admin(current_user.user_id)
    return [MenuOption(code=code, label=label) for code, label in MENU_LABELS.items()]


# Every enabled user, each with their granted menus (empty list = sees
# nothing). One call feeds both the user picker and the "who has what" grid.
@router.get("/admin/users", response_model=list[UserMenuRow])
def list_users(current_user: CurrentUser = Depends(get_current_user)) -> list[UserMenuRow]:
    _require_admin(current_user.user_id)
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        users = rows_to_dicts(cursor)
        cursor.execute("SELECT UserId, MenuCode FROM EngHub_UserMenuRights")
        grants = rows_to_dicts(cursor)
    by_user: dict[int, set[str]] = {}
    for g in grants:
        by_user.setdefault(g["UserId"], set()).add(g["MenuCode"])
    return [
        UserMenuRow(
            user_id=u["UserID"],
            name=u["Name"] or "",
            menus=[code for code in MENU_LABELS if code in by_user.get(u["UserID"], set())],
        )
        for u in users
    ]


# Replaces the user's whole menu set in one transaction (get_cursor commits
# on success / rolls back on error), so a save can never leave a half-applied
# mix of old and new rights.
@router.put("/admin/{user_id}")
def set_user_menus(user_id: int, body: SetMenusRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_admin(current_user.user_id)
    unknown = [m for m in body.menus if m not in MENU_LABELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown menu code(s): {', '.join(unknown)}")
    with get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM UserMaster WHERE UserID = ?", user_id)
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found.")
        cursor.execute("DELETE FROM EngHub_UserMenuRights WHERE UserId = ?", user_id)
        for code in dict.fromkeys(body.menus):
            cursor.execute(
                "INSERT INTO EngHub_UserMenuRights (UserId, MenuCode, GrantedByUserId) VALUES (?, ?, ?)",
                user_id, code, current_user.user_id,
            )
    return {"success": True}
