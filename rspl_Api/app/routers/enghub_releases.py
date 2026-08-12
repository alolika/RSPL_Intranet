"""Engineering Hub - Releases and Release Mapping.

Release notes are generated on read (concatenated titles/descriptions of
everything mapped), not stored separately — nothing to keep in sync, per the
approved design. Mapping a Feature or Development Item to a Release also
writes a 'Release Mapping' Activity (the PRD lists this as one of the
Activity types) so it shows up in that entity's Timeline automatically,
using the same EngHub_ActivityType row seeded in Phase 0 that no endpoint
had used until now.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-releases"])


class ReleaseRow(BaseModel):
    release_id: int
    name: str
    release_date: str | None = None
    description: str | None = None
    product_id: int | None = None
    product_name: str | None = None
    status_id: int
    status_name: str
    created_by_user_id: int
    created_by_name: str
    created_at: str


_RELEASE_SELECT = """
    SELECT r.ReleaseId, r.Name, r.ReleaseDate, r.Description, r.ProductId, p.Name AS ProductName,
           r.StatusId, s.Name AS StatusName, r.CreatedByUserId, cu.Name AS CreatedByName, r.CreatedAt
    FROM EngHub_Release r
    JOIN EngHub_Status s ON s.StatusId = r.StatusId
    JOIN UserMaster cu ON cu.UserID = r.CreatedByUserId
    LEFT JOIN EngHub_Product p ON p.ProductId = r.ProductId
"""


def _row_to_release(r: dict) -> ReleaseRow:
    return ReleaseRow(
        release_id=r["ReleaseId"], name=r["Name"] or "", release_date=str(r["ReleaseDate"]) if r["ReleaseDate"] else None,
        description=r["Description"], product_id=r["ProductId"], product_name=r["ProductName"],
        status_id=r["StatusId"], status_name=r["StatusName"] or "",
        created_by_user_id=r["CreatedByUserId"], created_by_name=r["CreatedByName"] or "",
        created_at=r["CreatedAt"].isoformat() if r["CreatedAt"] else "",
    )


@router.get("/releases", response_model=list[ReleaseRow])
def get_releases(product_id: int | None = None, status_id: int | None = None) -> list[ReleaseRow]:
    where: list[str] = []
    params: list = []
    if product_id is not None:
        where.append("r.ProductId = ?")
        params.append(product_id)
    if status_id is not None:
        where.append("r.StatusId = ?")
        params.append(status_id)
    sql = _RELEASE_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.ReleaseDate DESC, r.CreatedAt DESC"
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor, limit=500)
    return [_row_to_release(r) for r in rows]


@router.get("/releases/for-entity", response_model=list[ReleaseRow])
def get_releases_for_entity(feature_id: int | None = None, dev_item_id: int | None = None) -> list[ReleaseRow]:
    """Reverse lookup for the Feature/DevItem detail pages' "Mapped Releases"
    badge — which releases has this entity been mapped into. Registered
    before /releases/{release_id} so FastAPI's path matching doesn't try to
    parse "for-entity" as a release_id int first."""
    if (feature_id is None) == (dev_item_id is None):
        raise HTTPException(status_code=400, detail="Provide exactly one of feature_id, dev_item_id")
    with get_cursor() as cursor:
        cursor.execute(
            f"{_RELEASE_SELECT} WHERE r.ReleaseId IN "
            "(SELECT ReleaseId FROM EngHub_ReleaseMapping WHERE FeatureId = ? OR DevItemId = ?) "
            "ORDER BY r.ReleaseDate DESC, r.CreatedAt DESC",
            feature_id, dev_item_id,
        )
        rows = rows_to_dicts(cursor)
    return [_row_to_release(r) for r in rows]


@router.get("/releases/{release_id}", response_model=ReleaseRow)
def get_release(release_id: int) -> ReleaseRow:
    with get_cursor() as cursor:
        cursor.execute(f"{_RELEASE_SELECT} WHERE r.ReleaseId = ?", release_id)
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return _row_to_release(row)


class ReleaseForm(BaseModel):
    release_id: int
    name: str
    release_date: str | None = None
    description: str | None = None
    product_id: int | None = None
    status_id: int


@router.post("/releases")
def save_release(form: ReleaseForm, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        if form.release_id == 0:
            cursor.execute(
                "INSERT INTO EngHub_Release (Name, ReleaseDate, Description, ProductId, StatusId, CreatedByUserId) "
                "VALUES (?, ?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
                form.name, form.release_date, form.description, form.product_id, form.status_id, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
        else:
            cursor.execute(
                "UPDATE EngHub_Release SET Name=?, ReleaseDate=?, Description=?, ProductId=?, StatusId=?, "
                "LastEditedByUserId=?, LastEditedAt=SYSUTCDATETIME() WHERE ReleaseId=?",
                form.name, form.release_date, form.description, form.product_id, form.status_id,
                user.user_id, form.release_id,
            )
            new_id = form.release_id
    return {"success": True, "release_id": new_id}


class ReleaseMappingRow(BaseModel):
    release_mapping_id: int
    release_id: int
    feature_id: int | None = None
    feature_name: str | None = None
    dev_item_id: int | None = None
    dev_item_title: str | None = None
    mapped_by_user_id: int
    mapped_by_name: str
    mapped_at: str


@router.get("/releases/{release_id}/mappings", response_model=list[ReleaseMappingRow])
def get_release_mappings(release_id: int) -> list[ReleaseMappingRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT m.ReleaseMappingId, m.ReleaseId, m.FeatureId, f.Name AS FeatureName, "
            "m.DevItemId, di.Title AS DevItemTitle, m.MappedByUserId, u.Name AS MappedByName, m.MappedAt "
            "FROM EngHub_ReleaseMapping m "
            "LEFT JOIN EngHub_Feature f ON f.FeatureId = m.FeatureId "
            "LEFT JOIN EngHub_DevelopmentItem di ON di.DevItemId = m.DevItemId "
            "JOIN UserMaster u ON u.UserID = m.MappedByUserId "
            "WHERE m.ReleaseId = ? ORDER BY m.MappedAt DESC",
            release_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        ReleaseMappingRow(
            release_mapping_id=r["ReleaseMappingId"], release_id=r["ReleaseId"],
            feature_id=r["FeatureId"], feature_name=r["FeatureName"],
            dev_item_id=r["DevItemId"], dev_item_title=r["DevItemTitle"],
            mapped_by_user_id=r["MappedByUserId"], mapped_by_name=r["MappedByName"] or "",
            mapped_at=r["MappedAt"].isoformat() if r["MappedAt"] else "",
        )
        for r in rows
    ]


class MapRequest(BaseModel):
    feature_id: int | None = None
    dev_item_id: int | None = None


@router.post("/releases/{release_id}/map")
def map_to_release(release_id: int, body: MapRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    if (body.feature_id is None) == (body.dev_item_id is None):
        raise HTTPException(status_code=400, detail="Provide exactly one of feature_id, dev_item_id")

    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM EngHub_Release WHERE ReleaseId = ?", release_id)
        release = first_row_or_none(cursor)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found")

        cursor.execute(
            "INSERT INTO EngHub_ReleaseMapping (ReleaseId, FeatureId, DevItemId, MappedByUserId) "
            "VALUES (?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
            release_id, body.feature_id, body.dev_item_id, user.user_id,
        )
        new_id = int(first_row_or_none(cursor)["Id"])

        cursor.execute("SELECT ActivityTypeId FROM EngHub_ActivityType WHERE Name = 'Release Mapping'")
        activity_type_id = first_row_or_none(cursor)["ActivityTypeId"]
        cursor.execute(
            "INSERT INTO EngHub_Activity (ActivityTypeId, FeatureId, DevItemId, Description, LoggedByUserId) "
            "VALUES (?, ?, ?, ?, ?)",
            activity_type_id, body.feature_id, body.dev_item_id, f"Mapped to release {release['Name']}", user.user_id,
        )
    return {"success": True, "release_mapping_id": new_id}


@router.delete("/releases/{release_id}/mappings/{mapping_id}")
def unmap_from_release(release_id: int, mapping_id: int, user: CurrentUser = Depends(get_current_user)) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM EngHub_ReleaseMapping WHERE ReleaseMappingId = ? AND ReleaseId = ?", mapping_id, release_id
        )
    return {"success": True}


class ReleaseNoteItem(BaseModel):
    kind: str  # 'Feature' | 'DevelopmentItem'
    title: str
    description: str


@router.get("/releases/{release_id}/notes", response_model=list[ReleaseNoteItem])
def get_release_notes(release_id: int) -> list[ReleaseNoteItem]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT f.Name AS Title, f.Description FROM EngHub_ReleaseMapping m "
            "JOIN EngHub_Feature f ON f.FeatureId = m.FeatureId WHERE m.ReleaseId = ? AND m.FeatureId IS NOT NULL",
            release_id,
        )
        feature_rows = rows_to_dicts(cursor)
        cursor.execute(
            "SELECT di.Title, di.Description FROM EngHub_ReleaseMapping m "
            "JOIN EngHub_DevelopmentItem di ON di.DevItemId = m.DevItemId WHERE m.ReleaseId = ? AND m.DevItemId IS NOT NULL",
            release_id,
        )
        dev_item_rows = rows_to_dicts(cursor)
    return [
        ReleaseNoteItem(kind="Feature", title=r["Title"] or "", description=r["Description"] or "") for r in feature_rows
    ] + [
        ReleaseNoteItem(kind="DevelopmentItem", title=r["Title"] or "", description=r["Description"] or "") for r in dev_item_rows
    ]
