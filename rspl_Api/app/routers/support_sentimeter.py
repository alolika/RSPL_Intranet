"""Customer Sentimeter — keyword frequency and customer-mood analytics (PRD v1.2).

Reads dbo.call_summaries / dbo.call_summary_details (Worldnettech DB) — same
connection as the rest of the app. The keyword chart, drill-down list, call
content and keyword-exclusion admin go through the dbo.usp_Sentimeter_*
stored procedures from D:\\sentimeter\\Sentimeter_Stored_Procedures.sql
(copied to database/sentimeter_stored_procedures.sql); every call binds its
parameters, multi-select filters are passed as JSON arrays.

Endpoints (prefix /support/sentimeter)
--------------------------------------
GET   /options                         usp_Sentimeter_GetFilterOptions
POST  /analytics/keywords              usp_Sentimeter_GetKeywordAnalytics (call count, distinct customers, avg duration)
POST  /analytics/keyword-calls         usp_Sentimeter_GetKeywordCalls (compact/expanded, + v1.4 columnFilters)
POST  /analytics/keyword-calls/filter-options  usp_Sentimeter_GetKeywordCallFilterOptions (v1.4)
POST  /calls/content                   usp_Sentimeter_GetCallContent (one field, on demand)
GET   /admin/keyword-exclusions        usp_Sentimeter_GetKeywordExclusions
POST  /admin/keyword-exclusions        usp_Sentimeter_AddKeywordExclusion
PUT   /admin/keyword-exclusions/{id}   usp_Sentimeter_UpdateKeywordExclusion
PATCH /admin/keyword-exclusions/{id}/status  usp_Sentimeter_SetKeywordExclusionStatus
GET   /data                            filtered rows (capped) for the summary panels
                                       (time per executive, mood per customer/executive,
                                       all-calls table + Excel export)

Summaries/transcripts are never part of a list response, and their text is
never logged.
"""

import json
import math
import re
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user
from app.rights import FORM_SENTIMETER, RIGHTS_ADMIN_USER_IDS, require_access

router = APIRouter(prefix="/support/sentimeter", tags=["support-sentimeter"])

TABLE = "dbo.call_summaries"
DATA_ROW_CAP = 10000
MAX_PAGE_SIZE = 200
CONTENT_TYPES = ("shortSummary", "longSummary", "transcript")

# THROW numbers used by the stored procedures -> HTTP status.
_PROC_ERRORS = {"50001": 400, "50002": 400, "50003": 400, "50010": 400, "50011": 409, "50012": 404}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SentimeterFilters(BaseModel):
    date_from: date | None = Field(None, alias="dateFrom")
    date_to: date | None = Field(None, alias="dateTo")
    customer_name: str = Field("", alias="customerName")
    executive_name: str = Field("", alias="executiveName")
    software_names: list[str] = Field(default_factory=list, alias="softwareNames")
    moods: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def proc_params(self) -> list:
        """@DateFrom, @DateTo, @CustomerName, @ExecutiveName, @SoftwareNamesJson, @MoodsJson, @KeywordsJson"""
        return [
            self.date_from, self.date_to,
            self.customer_name.strip() or None, self.executive_name.strip() or None,
            _json_list(self.software_names), _json_list(self.moods), _json_list(self.keywords),
        ]


def _json_list(values: list[str]) -> str:
    return json.dumps([v.strip() for v in values if v and v.strip()], ensure_ascii=False)


def _all_result_sets(cursor) -> list[list[dict]]:
    sets: list[list[dict]] = []
    while True:
        if cursor.description is not None:
            cols = [c[0] for c in cursor.description]
            sets.append([dict(zip(cols, r)) for r in cursor.fetchall()])
        if not cursor.nextset():
            return sets


def _run_proc(sql: str, params: list, multi: bool = False):
    try:
        with get_cursor() as cursor:
            cursor.execute(sql, *params)
            return _all_result_sets(cursor) if multi else rows_to_dicts(cursor)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "Could not find stored procedure" in msg:
            raise HTTPException(
                status_code=503,
                detail="Sentimeter database procedures are not installed. Run database/sentimeter_stored_procedures.sql.",
            )
        m = re.search(r"\b(500\d\d)\b", msg)
        if m and m.group(1) in _PROC_ERRORS:
            # The THROW text is a fixed, safe message from the procedure itself.
            text = re.search(r"\](?:\[[^\]]*\])*\s*([^\[\]]+?)\s*\(\d+\)", msg)
            raise HTTPException(status_code=_PROC_ERRORS[m.group(1)], detail=(text.group(1) if text else "Request rejected."))
        raise HTTPException(status_code=500, detail="Sentimeter query failed.")


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _require_view(user: CurrentUser) -> None:
    require_access(user.user_id, FORM_SENTIMETER, "view")


def _require_exclusion_admin(user: CurrentUser) -> None:
    if user.user_id not in RIGHTS_ADMIN_USER_IDS:
        raise HTTPException(status_code=403, detail="Only administrators can change keyword exclusions.")


# ---------------------------------------------------------------------------
# Filter options / keyword analytics / drill-down / content
# ---------------------------------------------------------------------------

@router.get("/options")
def get_sentimeter_options(user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_view(user)
    sets = _run_proc("EXEC dbo.usp_Sentimeter_GetFilterOptions", [], multi=True)
    software = [str(r["option_value"]) for r in (sets[0] if sets else [])]
    moods = [str(r["option_value"]) for r in (sets[1] if len(sets) > 1 else [])]
    # "software" is the pre-v1.2 key; kept so an older frontend build still
    # renders (it calls .map() on it) while frontend/backend deploys are out of step.
    return {"softwareNames": software, "software": software, "moods": moods}


@router.post("/analytics/keywords")
def get_keyword_analytics(filters: SentimeterFilters, user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_view(user)
    rows = _run_proc("EXEC dbo.usp_Sentimeter_GetKeywordAnalytics ?, ?, ?, ?, ?, ?, ?", filters.proc_params())
    items: dict[str, dict] = {}
    for r in rows:
        item = items.get(r["keywordKey"])
        if item is None:
            avg = r["averageDurationSeconds"]
            avg = float(avg) if avg is not None else None
            item = items[r["keywordKey"]] = {
                "keyword": r["keyword"],
                "callCount": int(r["keywordTotal"]),           # bubble size
                "distinctCustomers": int(r["distinctCustomers"]),  # X axis
                "averageDurationSeconds": avg,                 # Y axis (unrounded)
                "averageDurationDisplay": _mmss(avg),
                "moodCounts": {},
            }
        item["moodCounts"][r["mood"]] = int(r["callCount"])
    return {"items": list(items.values()), "truncated": False}


def _mmss(seconds: float | None) -> str | None:
    """MM:SS rounded to the nearest whole second; minutes not truncated past 99."""
    if seconds is None or seconds < 0:
        return None
    total = int(seconds + 0.5)
    return f"{total // 60:02d}:{total % 60:02d}"


# ---- v1.4 Excel-style column filters (allow-listed fields/operators only) ----

MAX_FILTER_VALUES = 500


class TextColumnFilter(BaseModel):
    values: list[str] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    contains: str | None = Field(None, max_length=200)

    model_config = {"extra": "forbid"}


class MoodColumnFilter(BaseModel):
    values: list[str] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)

    model_config = {"extra": "forbid"}


class DateColumnFilter(BaseModel):
    selected_dates: list[date] = Field(default_factory=list, alias="selectedDates", max_length=MAX_FILTER_VALUES)
    date_from: date | None = Field(None, alias="from")
    date_to: date | None = Field(None, alias="to")

    model_config = {"extra": "forbid", "populate_by_name": True}


class DurationColumnFilter(BaseModel):
    operator: Literal["eq", "gt", "gte", "lt", "lte", "between"]
    value: int = Field(..., ge=0)
    value_to: int | None = Field(None, alias="valueTo", ge=0)

    model_config = {"extra": "forbid", "populate_by_name": True}


class ColumnFilters(BaseModel):
    ticket_no: list[int] = Field(default_factory=list, alias="ticketNo", max_length=MAX_FILTER_VALUES)
    call_date: DateColumnFilter | None = Field(None, alias="callDate")
    customer_name: TextColumnFilter | None = Field(None, alias="customerName")
    executive_name: TextColumnFilter | None = Field(None, alias="executiveName")
    customer_mood: MoodColumnFilter | None = Field(None, alias="customerMood")
    software_name: TextColumnFilter | None = Field(None, alias="softwareName")
    call_duration_seconds: DurationColumnFilter | None = Field(None, alias="callDurationSeconds")

    # Unknown field names are rejected (422), never turned into SQL.
    model_config = {"extra": "forbid", "populate_by_name": True}

    def to_json(self) -> str:
        d = self.model_dump(by_alias=True, exclude_none=True, mode="json")
        dur = self.call_duration_seconds
        if dur and dur.operator == "between":
            if dur.value_to is None:
                raise HTTPException(status_code=400, detail="Duration 'between' needs valueTo.")
            if dur.value_to < dur.value:
                d["callDurationSeconds"]["value"], d["callDurationSeconds"]["valueTo"] = dur.value_to, dur.value
        return json.dumps(d, ensure_ascii=False)


class KeywordCallsRequest(BaseModel):
    keyword: str
    filters: SentimeterFilters = Field(default_factory=SentimeterFilters)
    local_mood: str | None = Field(None, alias="localMood")
    page: int = 1
    page_size: int = Field(50, alias="pageSize")
    view_mode: str = Field("compact", alias="viewMode")
    column_filters: ColumnFilters = Field(default_factory=ColumnFilters, alias="columnFilters")

    model_config = {"populate_by_name": True}


@router.post("/analytics/keyword-calls")
def get_keyword_calls(body: KeywordCallsRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_view(user)
    if not body.keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required.")
    page = max(1, body.page)
    page_size = min(max(1, body.page_size), MAX_PAGE_SIZE)
    view_mode = "expanded" if body.view_mode == "expanded" else "compact"
    sets = _run_proc(
        "EXEC dbo.usp_Sentimeter_GetKeywordCalls ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
        [body.keyword.strip(), *body.filters.proc_params(),
         (body.local_mood or "").strip() or None, page, page_size, view_mode,
         body.column_filters.to_json()],
        multi=True,
    )
    mood_rows = sets[0] if sets else []
    call_rows = sets[1] if len(sets) > 1 else []
    # The windowed COUNT rides on the page's rows; past the last page there is
    # none, so the client just sees an empty page with totalItems 0.
    total = int(call_rows[0]["totalItems"]) if call_rows else 0
    return {
        "items": [
            {
                "ticketNo": r["ticketNo"],
                "callDate": _iso(r["callDate"]),
                "customerName": r["customerName"],
                "executiveName": r["executiveName"],
                "customerMood": r["customerMood"],
                "softwareName": r["softwareName"],
                "callDurationSeconds": r["callDurationSeconds"],
                "callDurationDisplay": r["callDurationDisplay"],
            }
            for r in call_rows
        ],
        "moodCounts": {r["mood"]: int(r["callCount"]) for r in mood_rows},
        "page": page,
        "pageSize": page_size,
        "totalItems": total,
        "totalPages": math.ceil(total / page_size) if total else 0,
        "viewMode": view_mode,
    }


class FilterOptionsRequest(BaseModel):
    keyword: str
    filters: SentimeterFilters = Field(default_factory=SentimeterFilters)
    field: Literal["ticketNo", "callDate", "customerName", "executiveName", "customerMood", "softwareName"]
    search: str | None = Field(None, max_length=200)
    max_values: int = Field(200, alias="maxValues", ge=1, le=1000)

    model_config = {"populate_by_name": True}


@router.post("/analytics/keyword-calls/filter-options")
def get_keyword_call_filter_options(body: FilterOptionsRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Distinct values of one expanded-list column for the selected keyword +
    global filters — bounded and searchable (Excel-style header menus)."""
    _require_view(user)
    if not body.keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required.")
    rows = _run_proc(
        "EXEC dbo.usp_Sentimeter_GetKeywordCallFilterOptions ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
        [body.keyword.strip(), *body.filters.proc_params(), body.field, (body.search or "").strip() or None, body.max_values],
    )
    return {
        "field": body.field,
        "values": [{"value": r["optionValue"], "callCount": int(r["callCount"])} for r in rows],
        "totalValues": int(rows[0]["totalValues"]) if rows else 0,
    }


class ContentRequest(BaseModel):
    ticket_no: int = Field(..., alias="ticketNo")
    content_type: str = Field(..., alias="contentType")

    model_config = {"populate_by_name": True}


@router.post("/calls/content")
def get_call_content(body: ContentRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_view(user)
    if body.content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="contentType must be shortSummary, longSummary or transcript.")
    if body.ticket_no <= 0:
        raise HTTPException(status_code=400, detail="Invalid ticketNo.")
    rows = _run_proc("EXEC dbo.usp_Sentimeter_GetCallContent ?, ?", [body.ticket_no, body.content_type])
    if not rows:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return {"ticketNo": rows[0]["ticketNo"], "contentType": body.content_type, "content": rows[0]["content"]}


# ---------------------------------------------------------------------------
# Keyword exclusions (admin)
# ---------------------------------------------------------------------------

class ExclusionRequest(BaseModel):
    keyword: str
    reason: str | None = None


class ExclusionStatusRequest(BaseModel):
    is_active: bool = Field(..., alias="isActive")

    model_config = {"populate_by_name": True}


def _exclusion(r: dict) -> dict:
    return {
        "exclusionId": r["exclusionId"], "keyword": r["keyword"], "reason": r["reason"],
        "isActive": bool(r["isActive"]), "createdAt": _iso(r["createdAt"]), "updatedAt": _iso(r["updatedAt"]),
    }


def _clean_exclusion(body: ExclusionRequest) -> tuple[str, str | None]:
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword is required.")
    if len(keyword) > 200 or len(body.reason or "") > 500:
        raise HTTPException(status_code=400, detail="Keyword max 200 and reason max 500 characters.")
    return keyword, (body.reason or "").strip() or None


@router.get("/admin/keyword-exclusions")
def list_keyword_exclusions(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    _require_view(user)
    rows = _run_proc("EXEC dbo.usp_Sentimeter_GetKeywordExclusions ?", [1])
    return [_exclusion(r) for r in rows]


@router.post("/admin/keyword-exclusions", status_code=201)
def add_keyword_exclusion(body: ExclusionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_exclusion_admin(user)
    keyword, reason = _clean_exclusion(body)
    rows = _run_proc("EXEC dbo.usp_Sentimeter_AddKeywordExclusion ?, ?", [keyword, reason])
    return _exclusion(rows[0])


@router.put("/admin/keyword-exclusions/{exclusion_id}")
def update_keyword_exclusion(exclusion_id: int, body: ExclusionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_exclusion_admin(user)
    keyword, reason = _clean_exclusion(body)
    rows = _run_proc("EXEC dbo.usp_Sentimeter_UpdateKeywordExclusion ?, ?, ?", [exclusion_id, keyword, reason])
    return _exclusion(rows[0])


@router.patch("/admin/keyword-exclusions/{exclusion_id}/status")
def set_keyword_exclusion_status(
    exclusion_id: int, body: ExclusionStatusRequest, user: CurrentUser = Depends(get_current_user)
) -> dict:
    _require_exclusion_admin(user)
    rows = _run_proc("EXEC dbo.usp_Sentimeter_SetKeywordExclusionStatus ?, ?", [exclusion_id, body.is_active])
    return _exclusion(rows[0])


@router.get("/admin/me")
def get_exclusion_admin_flag(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"canManageExclusions": user.user_id in RIGHTS_ADMIN_USER_IDS}


# ---------------------------------------------------------------------------
# Summary panels (time per executive, mood per customer/executive, all calls)
# ---------------------------------------------------------------------------

@router.get("/data")
def get_sentimeter_data(
    date_from: date | None = None,
    date_to: date | None = None,
    customer: str | None = None,
    executive: str | None = None,
    software: list[str] = Query([]),
    mood: list[str] = Query([]),
    keyword: list[str] = Query([]),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Same filter semantics as the stored procedures: inclusive end date via
    a next-day boundary, OR within software/mood/keyword, exact keyword tokens,
    blank mood = 'Unknown'."""
    _require_view(user)
    mood_expr = "COALESCE(NULLIF(LTRIM(RTRIM(customer_mood)), N''), N'Unknown')"
    clauses: list[str] = []
    params: list = []

    if date_from:
        clauses.append("call_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("call_date < DATEADD(day, 1, CAST(? AS date))")
        params.append(date_to)
    if customer and customer.strip():
        clauses.append("customer_name LIKE ?")
        params.append(f"%{customer.strip()}%")
    if executive and executive.strip():
        clauses.append("executive_name LIKE ?")
        params.append(f"%{executive.strip()}%")
    software = [s.strip() for s in software if s.strip()]
    if software:
        clauses.append(f"LTRIM(RTRIM(software_name)) IN ({', '.join('?' * len(software))})")
        params.extend(software)
    mood = [m.strip() for m in mood if m.strip()]
    if mood:
        clauses.append(f"{mood_expr} IN ({', '.join('?' * len(mood))})")
        params.extend(mood)
    keyword = [k.strip().lower() for k in keyword if k.strip()]
    if keyword:
        clauses.append(
            "EXISTS (SELECT 1 FROM STRING_SPLIT(ISNULL(keywords, N''), N',') s "
            f"WHERE LOWER(LTRIM(RTRIM(s.value))) IN ({', '.join('?' * len(keyword))}))"
        )
        params.extend(keyword)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT TOP ({DATA_ROW_CAP + 1}) id, call_date, executive_name, customer_name, "
        f"software_name, {mood_expr} AS customer_mood, keywords, call_duration, mood_reason "
        f"FROM {TABLE}{where} ORDER BY call_date DESC, id DESC"
    )

    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        raw = rows_to_dicts(cursor)

    truncated = len(raw) > DATA_ROW_CAP
    rows = [
        {
            "id": r["id"],
            "call_date": _iso(r["call_date"]),
            "executive_name": r["executive_name"],
            "customer_name": r["customer_name"],
            "software_name": r["software_name"],
            "customer_mood": r["customer_mood"],
            "keywords": r["keywords"],
            "call_duration": r["call_duration"],
            "mood_reason": r["mood_reason"],
        }
        for r in raw[:DATA_ROW_CAP]
    ]
    return {"rows": rows, "truncated": truncated}
