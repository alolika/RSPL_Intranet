"""Mirrors Section_Admin/Article.aspx.vb, ArticleRegister.aspx.vb,
WebMasters.aspx.vb (class name "Master" in source), and AddVideos.aspx.vb
(class name "Section_Admin_Videos", dual-mode via ?Add=). ViewVideo.aspx.vb
needs no backend — it only plays a URL passed in via query param.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/admin/content", tags=["admin-content"])


class LookupOption(BaseModel):
    label: str
    value: int


class ArticleRow(BaseModel):
    article_id: int
    publish_date: str
    category_id: int
    category: str
    title: str
    author: str
    description: str
    enabled: bool


class SaveArticleRequest(BaseModel):
    article_id: int | None = None
    category_id: int
    publish_date: date
    title: str
    author: str
    description: str
    enabled: bool


class VideoModuleOption(BaseModel):
    module_id: int
    name: str


class VideoRow(BaseModel):
    id: int
    name: str
    module_id: int
    module: str
    details: str
    video_url: str
    disabled: bool


class SaveVideoRequest(BaseModel):
    name: str
    details: str
    video_url: str
    disabled: bool
    module_name: str


class SaveWebMasterValueRequest(BaseModel):
    master_name: str
    id: int | None = None
    value: str


@router.get("/article-categories", response_model=list[LookupOption])
def get_article_categories() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Id, MasterValue FROM web_littlemaster WHERE MasterName = 'ArticleCategory' ORDER BY Id"
        )
        return [LookupOption(label=r["MasterValue"], value=r["Id"]) for r in rows_to_dicts(cursor)]


@router.get("/article-ids", response_model=list[LookupOption])
def get_article_ids() -> list[LookupOption]:
    # web_article is flooded with auto-generated leave-notification rows (thousands),
    # same unfiltered source query as the legacy dropdown — capped to the most recent
    # 500 so the picklist stays usable, per the established TOP-N pattern for
    # unbounded historical queries (VisitLogReport/TTRegister).
    with get_cursor() as cursor:
        cursor.execute("SELECT TOP 500 ArticleId FROM web_article ORDER BY ArticleId DESC")
        return [LookupOption(label=str(r["ArticleId"]), value=r["ArticleId"]) for r in rows_to_dicts(cursor)]


@router.get("/article/{article_id}", response_model=ArticleRow | None)
def get_article(article_id: int) -> ArticleRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT a.ArticleId, a.PublishDate, a.CategoryId, lm.MasterValue AS Category, a.Title, a.Auther, "
            "a.Description, a.Enabled FROM web_article a "
            "LEFT JOIN web_littlemaster lm ON a.CategoryId = lm.Id AND lm.MasterName = 'ArticleCategory' "
            "WHERE a.ArticleId = ?",
            article_id,
        )
        r = cursor.fetchone()
        if not r:
            return None
        row = dict(zip([c[0] for c in cursor.description], r))
    return ArticleRow(
        article_id=row["ArticleId"], publish_date=row["PublishDate"].isoformat() if row["PublishDate"] else "",
        category_id=row["CategoryId"], category=row["Category"] or "", title=row["Title"] or "",
        author=row["Auther"] or "", description=row["Description"] or "", enabled=bool(row["Enabled"]),
    )


@router.get("/article-register", response_model=list[ArticleRow])
def get_article_register(category_id: int = 0) -> list[ArticleRow]:
    # Same unbounded-history situation as get_article_ids() above — capped to the
    # most recent 500 rows (matching source query otherwise).
    where = " AND a.CategoryId = ?" if category_id else ""
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT TOP 500 a.ArticleId, a.PublishDate, a.CategoryId, lm.MasterValue AS Category, a.Title, a.Auther, "
            f"a.Description, 1 AS Enabled FROM web_article a "
            f"INNER JOIN web_littlemaster lm ON a.CategoryId = lm.Id WHERE lm.MasterName = 'ArticleCategory'{where} "
            f"ORDER BY a.ArticleId DESC",
            *([category_id] if category_id else []),
        )
        rows = rows_to_dicts(cursor)
    return [
        ArticleRow(
            article_id=r["ArticleId"], publish_date=r["PublishDate"].isoformat() if r["PublishDate"] else "",
            category_id=r["CategoryId"], category=r["Category"] or "", title=r["Title"] or "",
            author=r["Auther"] or "", description=r["Description"] or "", enabled=True,
        )
        for r in rows
    ]


@router.post("/article")
def save_article(body: SaveArticleRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        if body.article_id is None:
            cursor.execute(
                "EXEC webproc_webarticleadd 0, ?, ?, ?, ?, ?, ?, ?, 0",
                body.category_id, body.publish_date, body.title, body.author, body.description,
                body.enabled, current_user.user_id,
            )
        else:
            cursor.execute(
                "EXEC webproc_webarticleedit ?, ?, ?, ?, ?, ?, ?, ?",
                body.article_id, body.category_id, body.publish_date, body.title, body.author,
                body.description, body.enabled, current_user.user_id,
            )
    return {"success": True}


@router.get("/web-master-names", response_model=list[str])
def get_web_master_names() -> list[str]:
    with get_cursor() as cursor:
        cursor.execute("SELECT DISTINCT MasterName FROM web_littlemaster ORDER BY MasterName")
        return [r["MasterName"] for r in rows_to_dicts(cursor)]


@router.get("/web-master-values")
def get_web_master_values(master_name: str) -> list[dict]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Id, MasterValue FROM web_littlemaster WHERE MasterName = ? ORDER BY Id", master_name
        )
        return [{"id": r["Id"], "value": r["MasterValue"]} for r in rows_to_dicts(cursor)]


@router.post("/web-master-value")
def save_web_master_value(body: SaveWebMasterValueRequest) -> dict:
    with get_cursor() as cursor:
        if body.id is None:
            cursor.execute(
                "SELECT 1 FROM web_littlemaster WHERE MasterName = ? AND MasterValue = ?",
                body.master_name, body.value,
            )
            if cursor.fetchone():
                return {"success": False, "message": "This Value already exist! Please enter another value.!"}
            cursor.execute("EXEC webproc_webmasteradd ?, ?", body.master_name, body.value)
            return {"success": True, "message": "Record insert successfully"}
        cursor.execute("EXEC Webproc_Webmasteredit ?, ?, ?", body.id, body.master_name, body.value)
    return {"success": True, "message": "Web_Mastervalue edited successfully!"}


@router.get("/video-modules", response_model=list[VideoModuleOption])
def get_video_modules() -> list[VideoModuleOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ModuleId, Name FROM VideoModuleMaster ORDER BY Name")
        return [VideoModuleOption(module_id=r["ModuleId"], name=r["Name"]) for r in rows_to_dicts(cursor)]


@router.get("/videos", response_model=list[VideoRow])
def get_videos(search: str = "") -> list[VideoRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_GetVideoDetails ?", search)
        rows = rows_to_dicts(cursor)
    return [
        VideoRow(
            id=r["ID"], name=r["Name"] or "", module_id=r["ModuleID"] or 0, module=r["Module"] or "",
            details=r["Details"] or "", video_url=r["VideoURL"] or "", disabled=bool(r["Disabled"]),
        )
        for r in rows
    ]


@router.post("/video")
def save_video(video_id: int, body: SaveVideoRequest) -> dict:
    # Note: Webproc_AddVideoDetails' UPDATE branch has a latent bug in the deployed
    # proc (`Set Name=@Name,@Description=@Description,...` reassigns the @Description
    # parameter to itself instead of the Description column) — editing a video's
    # description silently no-ops on the DB side. Not fixable from this API layer.
    with get_cursor() as cursor:
        module_id = 0
        if body.module_name:
            cursor.execute("SELECT ModuleId FROM VideoModuleMaster WHERE Name = ?", body.module_name)
            mod_row = cursor.fetchone()
            module_id = mod_row[0] if mod_row else 0
        cursor.execute(
            "EXEC Webproc_AddVideoDetails ?, ?, ?, ?, ?, ?",
            video_id, body.name, body.details, body.video_url, body.disabled, module_id,
        )
        rows = rows_to_dicts(cursor)
        message = rows[0]["ResultMsg"] if rows else "Video saved."
    return {"success": True, "message": message}


@router.post("/video-module")
def add_video_module(name: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_AddVideoModuleMaster 0, ?, 0", name)
        rows = rows_to_dicts(cursor)
        message = rows[0]["ResultMsg"] if rows else "Module added successfully!"
    return {"success": True, "message": message}
