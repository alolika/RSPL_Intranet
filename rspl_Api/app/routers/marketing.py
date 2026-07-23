"""Shared lookups used across Section_Marketing pages (UserMaster/SalesMan
pickers appear in nearly every enquiry/action/report page in this section).
Page-specific routers live in separate marketing_*.py files.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing", tags=["marketing"])


class LookupOption(BaseModel):
    label: str
    value: int


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/salesmen", response_model=list[LookupOption])
def get_salesmen() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT SalesManId, Name FROM SalesMan WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["SalesManId"]) for r in rows_to_dicts(cursor)]
