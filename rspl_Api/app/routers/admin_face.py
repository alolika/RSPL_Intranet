"""Face recognition — employee enrollment (admin) and face-based attendance (general).

Database prerequisites (see database/face_recognition_migration.sql):
  UserMaster: FaceEmbedding NVARCHAR(MAX), FacePhotoFile NVARCHAR(255), FaceRegisteredAt NVARCHAR(50)
  FaceAttendance: (UserID, AttendanceDate) PK, ArrivalTime, Confidence, Lat, Lng, AccuracyM, City, State, Country

Face photos are stored on disk in face_photos/ (sibling of app/) and served
as static files at /face-photos/<filename>.
"""

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.db import first_row_or_none, get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(tags=["face"])

FACE_DESCRIPTOR_LENGTH = 128
FACE_MATCH_THRESHOLD = 0.45

# Photos land next to the `app` package — i.e. rspl_Api/face_photos/
_PHOTOS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "face_photos")
os.makedirs(_PHOTOS_DIR, exist_ok=True)


# ── helpers ────────────────────────────────────────────────────────────────

def _encode_embeddings(vectors: list[list[float]]) -> str:
    for v in vectors:
        if len(v) != FACE_DESCRIPTOR_LENGTH:
            raise ValueError(f"Descriptor must be {FACE_DESCRIPTOR_LENGTH} floats, got {len(v)}")
    return json.dumps([[float(x) for x in v] for v in vectors])


def _decode_embeddings(value: str | None) -> list[list[float]]:
    text = (value or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list) or not data:
        return []
    # Legacy: a single flat 128-length vector stored before template-set support.
    if isinstance(data[0], (int, float)):
        return [[float(x) for x in data]]
    return [[float(x) for x in v] for v in data]


def _photo_url(filename: str | None) -> str:
    return f"/face-photos/{filename}" if filename else ""


# ── response models ────────────────────────────────────────────────────────

class EmployeeRow(BaseModel):
    userId: int
    userName: str
    faceRegisteredAt: str | None = None


class EnrolResponse(BaseModel):
    memberId: str
    faceRegisteredAt: str
    photoUrl: str


class EnrolledFace(BaseModel):
    memberId: str
    memberName: str
    firstName: str
    embeddings: list[list[float]]
    photoUrl: str
    faceRegisteredAt: str


class FaceEmbeddingsResponse(BaseModel):
    faces: list[EnrolledFace]
    etag: str


class FaceAttendanceRequest(BaseModel):
    employeeId: str
    confidence: float
    lat: float | None = None
    lng: float | None = None
    accuracyM: float | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


class FaceAttendanceResponse(BaseModel):
    employeeId: str
    memberName: str
    firstName: str
    alreadyMarked: bool
    arrivalTime: str
    confidence: float
    lat: float | None = None
    lng: float | None = None
    city: str | None = None


class FaceCheckoutRequest(BaseModel):
    employeeId: str
    confidence: float


class FaceCheckoutResponse(BaseModel):
    employeeId: str
    memberName: str
    firstName: str
    departureTime: str
    alreadyCheckedOut: bool


class FaceAttendanceReportRow(BaseModel):
    userId: int
    userName: str
    attendanceDate: str
    arrivalTime: str
    departureTime: str | None = None
    confidence: float | None = None
    lat: float | None = None
    lng: float | None = None
    accuracyM: float | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


# ── endpoints ──────────────────────────────────────────────────────────────

@router.get("/admin/face/employees", response_model=list[EmployeeRow])
def list_employees(_cu: CurrentUser = Depends(get_current_user)) -> list[EmployeeRow]:
    """All active employees with their face-enrollment status."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name, FaceRegisteredAt "
            "FROM UserMaster WHERE Enabled = 1 ORDER BY Name"
        )
        rows = rows_to_dicts(cursor)
    return [
        EmployeeRow(
            userId=r["UserID"],
            userName=r["Name"] or "",
            faceRegisteredAt=r["FaceRegisteredAt"],
        )
        for r in rows
    ]


@router.post("/admin/face/employees/{user_id}", response_model=EnrolResponse)
async def enrol_face(
    user_id: int,
    embedding: str = Form(...),
    photo: UploadFile = File(...),
    _cu: CurrentUser = Depends(get_current_user),
) -> EnrolResponse:
    """Enrol or re-enrol an employee's face. Admin only (caller must be authenticated)."""
    # Parse and validate embeddings
    try:
        raw = json.loads(embedding)
        if not isinstance(raw, list) or not raw:
            raise ValueError("embeddings must be a non-empty list")
        # Accept both a single flat vector and a template set.
        if isinstance(raw[0], (int, float)):
            vectors: list[list[float]] = [raw]
        else:
            vectors = raw
        embedding_json = _encode_embeddings(vectors)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Save reference photo
    photo_bytes = await photo.read()
    filename = f"{user_id}_{uuid.uuid4().hex[:8]}.jpg"
    with open(os.path.join(_PHOTOS_DIR, filename), "wb") as fh:
        fh.write(photo_bytes)

    registered_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_cursor() as cursor:
        # Remove old photo file on re-enrolment
        cursor.execute("SELECT FacePhotoFile FROM UserMaster WHERE UserID = ?", user_id)
        existing = first_row_or_none(cursor)
        if existing and existing.get("FacePhotoFile"):
            old = os.path.join(_PHOTOS_DIR, existing["FacePhotoFile"])
            try:
                if os.path.exists(old):
                    os.remove(old)
            except OSError:
                pass

        cursor.execute(
            "UPDATE UserMaster "
            "SET FaceEmbedding = ?, FacePhotoFile = ?, FaceRegisteredAt = ? "
            "WHERE UserID = ?",
            embedding_json, filename, registered_at, user_id,
        )

    return EnrolResponse(
        memberId=str(user_id),
        faceRegisteredAt=registered_at,
        photoUrl=_photo_url(filename),
    )


@router.delete("/admin/face/employees/{user_id}", status_code=204)
def clear_face(
    user_id: int,
    _cu: CurrentUser = Depends(get_current_user),
) -> None:
    """Remove an employee's face enrolment. Admin only."""
    with get_cursor() as cursor:
        cursor.execute("SELECT FacePhotoFile FROM UserMaster WHERE UserID = ?", user_id)
        existing = first_row_or_none(cursor)
        if existing and existing.get("FacePhotoFile"):
            old = os.path.join(_PHOTOS_DIR, existing["FacePhotoFile"])
            try:
                if os.path.exists(old):
                    os.remove(old)
            except OSError:
                pass
        cursor.execute(
            "UPDATE UserMaster "
            "SET FaceEmbedding = NULL, FacePhotoFile = NULL, FaceRegisteredAt = NULL "
            "WHERE UserID = ?",
            user_id,
        )


@router.get("/admin/face/embeddings", response_model=FaceEmbeddingsResponse)
def get_embeddings(
    response: Response,
    if_none_match: str | None = None,
    _cu: CurrentUser = Depends(get_current_user),
) -> FaceEmbeddingsResponse | Response:
    """Return all enrolled face descriptors. ETag-cached: pass If-None-Match for 304."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Name, FaceEmbedding, FacePhotoFile, FaceRegisteredAt "
            "FROM UserMaster "
            "WHERE Enabled = 1 AND FaceEmbedding IS NOT NULL "
            "ORDER BY Name"
        )
        rows = rows_to_dicts(cursor)

    etag_input = ";".join(
        f"{r['UserID']}|{r['FaceRegisteredAt'] or ''}" for r in rows
    )
    etag = 'W/"faces-' + hashlib.md5(etag_input.encode()).hexdigest()[:16] + '"'
    response.headers["ETag"] = etag

    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    faces: list[EnrolledFace] = []
    for r in rows:
        embeddings = _decode_embeddings(r["FaceEmbedding"])
        if not embeddings:
            continue
        name: str = r["Name"] or ""
        first = name.split()[0] if name else ""
        faces.append(
            EnrolledFace(
                memberId=str(r["UserID"]),
                memberName=name,
                firstName=first,
                embeddings=embeddings,
                photoUrl=_photo_url(r["FacePhotoFile"]),
                faceRegisteredAt=r["FaceRegisteredAt"] or "",
            )
        )

    return FaceEmbeddingsResponse(faces=faces, etag=etag)


@router.post("/general/face/attendance", response_model=FaceAttendanceResponse)
def mark_face_attendance(
    body: FaceAttendanceRequest,
    _cu: CurrentUser = Depends(get_current_user),
) -> FaceAttendanceResponse:
    """Mark today's attendance for the matched employee.

    Confidence must meet FACE_MATCH_THRESHOLD (server-side re-check).
    Timestamp is server clock — client clock is never trusted.
    Deduplicates per (UserID, AttendanceDate); returns alreadyMarked=True for duplicates.
    """
    if body.confidence < FACE_MATCH_THRESHOLD:
        raise HTTPException(
            status_code=422,
            detail=f"Confidence {body.confidence:.3f} is below the required threshold {FACE_MATCH_THRESHOLD}.",
        )

    try:
        user_id = int(body.employeeId)
    except ValueError:
        raise HTTPException(status_code=422, detail="employeeId must be a numeric string")

    now_utc = datetime.now(timezone.utc)
    # Use server's local date for the attendance record (IST ~ UTC+5:30)
    today: date = now_utc.date()
    arrival_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_cursor() as cursor:
        # Lookup employee name
        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ? AND Enabled = 1", user_id)
        emp = first_row_or_none(cursor)
        if emp is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        name: str = emp["Name"] or ""
        first = name.split()[0] if name else ""

        # Check for duplicate
        cursor.execute(
            "SELECT ArrivalTime FROM FaceAttendance WHERE UserID = ? AND AttendanceDate = ?",
            user_id, today,
        )
        dup = first_row_or_none(cursor)
        if dup:
            return FaceAttendanceResponse(
                employeeId=body.employeeId,
                memberName=name,
                firstName=first,
                alreadyMarked=True,
                arrivalTime=dup["ArrivalTime"],
                confidence=body.confidence,
                lat=body.lat,
                lng=body.lng,
                city=body.city,
            )

        cursor.execute(
            "INSERT INTO FaceAttendance "
            "(UserID, AttendanceDate, ArrivalTime, Confidence, Lat, Lng, AccuracyM, City, [State], Country) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            user_id, today, arrival_time,
            body.confidence, body.lat, body.lng, body.accuracyM,
            body.city, body.state, body.country,
        )

    return FaceAttendanceResponse(
        employeeId=body.employeeId,
        memberName=name,
        firstName=first,
        alreadyMarked=False,
        arrivalTime=arrival_time,
        confidence=body.confidence,
        lat=body.lat,
        lng=body.lng,
        city=body.city,
    )


@router.get("/admin/face/attendance-report", response_model=list[FaceAttendanceReportRow])
def get_face_attendance_report(
    name: str = "",
    date_from: str = "",
    date_to: str = "",
    _cu: CurrentUser = Depends(get_current_user),
) -> list[FaceAttendanceReportRow]:
    """Face-recognition attendance records joined with employee names.

    Filters: name (partial, case-insensitive), date_from/date_to (yyyy-mm-dd).
    Results are capped at 2000 rows ordered by AttendanceDate DESC, ArrivalTime DESC.
    """
    conditions = ["1=1"]
    params: list = []

    if name.strip():
        conditions.append("um.Name LIKE ?")
        params.append(f"%{name.strip()}%")
    if date_from.strip():
        conditions.append("fa.AttendanceDate >= ?")
        params.append(date_from.strip())
    if date_to.strip():
        conditions.append("fa.AttendanceDate <= ?")
        params.append(date_to.strip())

    where = " AND ".join(conditions)
    sql = (
        "SELECT TOP 2000 fa.UserID, um.Name, "
        "CONVERT(NVARCHAR(10), fa.AttendanceDate, 23) AS AttendanceDate, "
        "fa.ArrivalTime, fa.DepartureTime, fa.Confidence, fa.Lat, fa.Lng, fa.AccuracyM, "
        "fa.City, fa.[State], fa.Country "
        "FROM FaceAttendance fa "
        "JOIN UserMaster um ON fa.UserID = um.UserID "
        f"WHERE {where} "
        "ORDER BY fa.AttendanceDate DESC, fa.ArrivalTime DESC"
    )

    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)

    return [
        FaceAttendanceReportRow(
            userId=r["UserID"],
            userName=r["Name"] or "",
            attendanceDate=r["AttendanceDate"] or "",
            arrivalTime=r["ArrivalTime"] or "",
            departureTime=r["DepartureTime"],
            confidence=r["Confidence"],
            lat=r["Lat"],
            lng=r["Lng"],
            accuracyM=r["AccuracyM"],
            city=r["City"],
            state=r["State"],
            country=r["Country"],
        )
        for r in rows
    ]


@router.post("/general/face/checkout", response_model=FaceCheckoutResponse)
def mark_face_checkout(
    body: FaceCheckoutRequest,
    _cu: CurrentUser = Depends(get_current_user),
) -> FaceCheckoutResponse:
    """Record today's departure time for the matched employee.

    Requires a prior check-in (ArrivalTime) for today — returns 404 if none.
    Confidence must meet FACE_MATCH_THRESHOLD. Departure time is server clock.
    Re-scanning after already checked out updates the departure time (last
    scan wins, so a brief re-scan at gate-close doesn't break anything).
    """
    if body.confidence < FACE_MATCH_THRESHOLD:
        raise HTTPException(
            status_code=422,
            detail=f"Confidence {body.confidence:.3f} is below threshold {FACE_MATCH_THRESHOLD}.",
        )

    try:
        user_id = int(body.employeeId)
    except ValueError:
        raise HTTPException(status_code=422, detail="employeeId must be a numeric string")

    now_utc = datetime.now(timezone.utc)
    today: date = now_utc.date()
    departure_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM UserMaster WHERE UserID = ? AND Enabled = 1", user_id)
        emp = first_row_or_none(cursor)
        if emp is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        name: str = emp["Name"] or ""
        first = name.split()[0] if name else ""

        cursor.execute(
            "SELECT ArrivalTime, DepartureTime FROM FaceAttendance WHERE UserID = ? AND AttendanceDate = ?",
            user_id, today,
        )
        rec = first_row_or_none(cursor)
        if rec is None:
            raise HTTPException(status_code=404, detail="No check-in found for today. Please check in first.")

        already = bool(rec.get("DepartureTime"))
        cursor.execute(
            "UPDATE FaceAttendance SET DepartureTime = ? WHERE UserID = ? AND AttendanceDate = ?",
            departure_time, user_id, today,
        )

    return FaceCheckoutResponse(
        employeeId=body.employeeId,
        memberName=name,
        firstName=first,
        departureTime=departure_time,
        alreadyCheckedOut=already,
    )
