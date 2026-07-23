"""Mirrors Agile_project_Manegment/Commendations.aspx.vb,
AppriciationRegister.aspx.vb, and TaskTracking.aspx.vb's appreciation feed
panel — all three read/write `Rspl_AppreciationMaster`.

Real gap (see agile_tasks.py's docstring for the module-wide context):
`Rspl_AppreciationMaster` is confirmed to not exist on this database at all
(verified via OBJECT_ID) — not a missing proc wrapping a real table, a
genuinely absent table. There is no reasonable direct-SQL substitute the
way there was for the missing "Get" procs elsewhere in this module, since
no equivalent data exists anywhere in the schema. These endpoints degrade
to empty results / a no-op success rather than raising "invalid object
name" on every call.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/agile", tags=["agile-appreciation"])


class LookupOption(BaseModel):
    label: str
    value: int


class AppreciationMessage(BaseModel):
    comment: str
    to_user: str
    from_user: str


class AppreciationRow(BaseModel):
    appreciation_id: int
    from_user: str
    to_user: str
    reason: str
    description: str


class AddAppreciationRequest(BaseModel):
    to_user_id: int
    reason: str
    description: str
    is_commendation: bool
    keep_confidential: bool


@router.get("/appreciation-feed", response_model=list[AppreciationMessage])
def get_appreciation_messages() -> list[AppreciationMessage]:
    return []


@router.get("/appreciation-users", response_model=list[LookupOption])
def get_commendation_users(current_user_id: int = 0) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("Select Name, UserID from userMaster Where userid not in (?) and Enabled = 1 order by Name", current_user_id)
        return [LookupOption(label=r["Name"] or "", value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.post("/appreciations")
def add_appreciation(body: AddAppreciationRequest) -> dict:
    return {"success": True}


@router.get("/appreciations", response_model=list[AppreciationRow])
def get_appreciation_register(user_id: int = 0, is_commendation: bool = True) -> list[AppreciationRow]:
    return []
