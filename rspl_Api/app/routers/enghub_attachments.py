"""Engineering Hub - Attachments (Feature / Development Item / Task /
Activity / Decision).

Same filesystem-per-entity storage convention proven in support.py
(C:\\Retailware\\<AppName>Attachments\\<scope>\\<file>, sanitized filenames,
collision-safe naming) — but unlike support.py's pure folder-listing, every
upload also gets a EngHub_Attachment DB row, since Engineering Hub explicitly
needs uploader attribution for effort/history tracking that support.py never
needed. Extensions and size cap are wider than support.py's image-only 5MB
convention (per the approved design's §7 Q3): these are general engineering
documents (design notes, logs, screenshots), not just ticket photos.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/engineering-hub", tags=["engineering-hub-attachments"])

ATTACHMENTS_ROOT = Path(r"C:\Retailware\EngHubAttachments")
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".zip",
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB, per the approved design's Q3
_ENTITY_TYPES = {"Feature", "DevelopmentItem", "Task", "Activity", "Decision"}


def _safe_entity_dir(entity_type: str, entity_id: int) -> Path:
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid entity_type: {entity_type}")
    entity_dir = ATTACHMENTS_ROOT / entity_type / str(entity_id)
    entity_dir.mkdir(parents=True, exist_ok=True)
    return entity_dir


def _safe_filename(original_name: str) -> str:
    # Path(...).name strips any directory components a malicious/unexpected
    # filename could carry (e.g. "../../evil.exe") — combined with the
    # extension allow-list, the saved name can never escape the per-entity
    # folder or write a disallowed file type.
    name = Path(original_name).name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or '(none)'}")
    stem = re.sub(r"[^\w\-. ]", "_", Path(name).stem) or "attachment"
    return f"{stem}{ext}"


def _unique_path(entity_dir: Path, filename: str) -> Path:
    candidate = entity_dir / filename
    if not candidate.exists():
        return candidate
    stem, ext = Path(filename).stem, Path(filename).suffix
    n = 1
    while (entity_dir / f"{stem} ({n}){ext}").exists():
        n += 1
    return entity_dir / f"{stem} ({n}){ext}"


class AttachmentRow(BaseModel):
    attachment_id: int
    entity_type: str
    entity_id: int
    file_name: str
    file_size_bytes: int
    content_type: str | None = None
    uploaded_by_user_id: int
    uploaded_by_name: str
    uploaded_at: str


def _row_to_attachment(r: dict) -> AttachmentRow:
    return AttachmentRow(
        attachment_id=r["AttachmentId"], entity_type=r["EntityType"], entity_id=r["EntityId"],
        file_name=r["FileName"] or "", file_size_bytes=r["FileSizeBytes"], content_type=r["ContentType"],
        uploaded_by_user_id=r["UploadedByUserId"], uploaded_by_name=r["UploadedByName"] or "",
        uploaded_at=r["UploadedAt"].isoformat() if r["UploadedAt"] else "",
    )


@router.get("/attachments", response_model=list[AttachmentRow])
def get_attachments(entity_type: str, entity_id: int) -> list[AttachmentRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT a.AttachmentId, a.EntityType, a.EntityId, a.FileName, a.FileSizeBytes, a.ContentType, "
            "a.UploadedByUserId, u.Name AS UploadedByName, a.UploadedAt "
            "FROM EngHub_Attachment a JOIN UserMaster u ON u.UserID = a.UploadedByUserId "
            "WHERE a.EntityType = ? AND a.EntityId = ? AND a.Enabled = 1 ORDER BY a.UploadedAt DESC",
            entity_type, entity_id,
        )
        rows = rows_to_dicts(cursor)
    return [_row_to_attachment(r) for r in rows]


@router.post("/attachments", response_model=list[AttachmentRow])
def upload_attachments(
    entity_type: str, entity_id: int, files: list[UploadFile] = File(...), user: CurrentUser = Depends(get_current_user)
) -> list[AttachmentRow]:
    entity_dir = _safe_entity_dir(entity_type, entity_id)
    saved: list[AttachmentRow] = []
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ?", user.user_id)
        uploader_name = (first_row_or_none(cursor) or {}).get("Name") or user.username
        for upload in files:
            filename = _safe_filename(upload.filename or "attachment")
            content = upload.file.read()
            if len(content) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(status_code=400, detail=f"{filename} exceeds the 10MB limit")
            dest = _unique_path(entity_dir, filename)
            with dest.open("wb") as f:
                f.write(content)
            cursor.execute(
                "INSERT INTO EngHub_Attachment (EntityType, EntityId, FileName, StoredFileName, FileSizeBytes, "
                "ContentType, UploadedByUserId) VALUES (?, ?, ?, ?, ?, ?, ?); SELECT SCOPE_IDENTITY() AS Id",
                entity_type, entity_id, filename, dest.name, len(content), upload.content_type, user.user_id,
            )
            new_id = int(first_row_or_none(cursor)["Id"])
            saved.append(
                AttachmentRow(
                    attachment_id=new_id, entity_type=entity_type, entity_id=entity_id, file_name=filename,
                    file_size_bytes=len(content), content_type=upload.content_type,
                    uploaded_by_user_id=user.user_id, uploaded_by_name=uploader_name, uploaded_at="",
                )
            )
    return saved


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int) -> FileResponse:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT EntityType, EntityId, FileName, StoredFileName FROM EngHub_Attachment "
            "WHERE AttachmentId = ? AND Enabled = 1",
            attachment_id,
        )
        row = first_row_or_none(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    file_path = ATTACHMENTS_ROOT / row["EntityType"] / str(row["EntityId"]) / row["StoredFileName"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")
    return FileResponse(file_path, filename=row["FileName"])


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, user: CurrentUser = Depends(get_current_user)) -> dict:
    # Soft-delete only, matching the "never hard-delete" convention used
    # everywhere else in this schema — the file stays on disk (recoverable).
    with get_cursor() as cursor:
        cursor.execute("UPDATE EngHub_Attachment SET Enabled = 0 WHERE AttachmentId = ?", attachment_id)
    return {"success": True}
