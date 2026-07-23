"""Mirrors Section_Support/CallHistory.aspx.vb, CallHistoryDetails.aspx.vb,
MissedCall.aspx.vb, ClosePopup.aspx.vb.

UPDATE: the note that used to be here ("SynapseCDR does not exist on this
SQL instance, every endpoint 500s, not live-tested") is stale — confirmed
`SynapseCDR` exists and `synapsecdr.dbo.SIPEventRegisterCDR` is reachable
using the same DB connection/credentials as the main app database (no
separate linked server or login needed, just a same-instance
cross-database three-part name). `/call-history/summary` and
`/call-history/detail` (Proc_CallHistory) have both been live-tested
against real data and are working — see get_call_history_detail's own note
for a real date-handling bug found and fixed there (CallDate is always
stored at exact midnight; a non-midnight/UTC-shifted datetime parameter
silently matched zero rows instead of erroring). The other endpoints in
this router (missed-calls, close-event — proc_SIPMissCallAttemptList,
WebProc_UpdateSIPEventRegisterCDR, webfun_SIPMissedCalls) are written
against the confirmed proc signatures but have NOT been individually
live-tested yet.

Separately (not a correctness bug, a real perf one): Proc_CallHistory's
summary mode times out past the pooled connection's 60s cap on broad,
unfiltered date ranges — SIPEventRegisterCDR has ~297K rows with no index
on the entity/date columns it filters by. A single day or a
customer/mobile-filtered range runs in ~2s; an open-ended multi-week "all
customers" search currently 500s. Not fixed here — flagged for whoever
picks up indexing this table.

The real-time click-to-call features (CallNow/lnkbtnphoneno_Click, which hit
an internal-only Asterisk PBX at 192.168.200.188) are NOT wired here either —
already stubbed with an alert at the Angular layer per the migration
checklist, and there's no way to reach that internal IP from this backend
regardless.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/support", tags=["support-callsip"])


class CallHistorySummaryRow(BaseModel):
    entity_id: int
    entity_name: str
    no_of_incoming_call: int
    no_of_outgoing_call: int
    first_incoming_call_time: str | None
    last_incoming_call_time: str | None
    total_call_duration: str


class CallHistoryDetailRow(BaseModel):
    source_name: str
    call_time: str
    source_no: str
    duration: str
    bill_seconds: int
    call_type: str
    received_by: str


class MissedCallRow(BaseModel):
    record_no: int
    entity_id: int
    entity_name: str
    entity_phone: str
    call_date: str
    min_call_time: str
    call_time: str
    attempt_count: int
    executive_name: str
    amc_amount: float
    amc_expiry_date: str
    ledger_balance: float
    action_state_id: int
    processed: int
    software_name: str
    installed_within_one_month: bool
    advance_amc: bool


class CloseEventRequest(BaseModel):
    record_no: int
    cust_id: int
    action_state_id: int
    narration: str
    add_dependent: bool
    mobile_no: str
    name: str


@router.get("/call-history/summary", response_model=list[CallHistorySummaryRow])
def get_call_history_summary(
    from_date: date, to_date: date, customer_id: int = 0, mobile_no: str = ""
) -> list[CallHistorySummaryRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_CallHistory ?, ?, ?, ?, 1",
            from_date.strftime("%d-%b-%Y"), to_date.strftime("%d-%b-%Y"), customer_id, mobile_no,
        )
        rows = rows_to_dicts(cursor)

    return [
        CallHistorySummaryRow(
            entity_id=r["EntityID"], entity_name=r["EntityName"] or "",
            no_of_incoming_call=r["NoOfIncomingCall"] or 0, no_of_outgoing_call=r["NoOfOutGoingCall"] or 0,
            first_incoming_call_time=r["FirstIncomingCallTime"].isoformat() if r["FirstIncomingCallTime"] else None,
            last_incoming_call_time=r["LastIncomingCallTime"].isoformat() if r["LastIncomingCallTime"] else None,
            total_call_duration=str(r["TotalCallDuration"] or ""),
        )
        for r in rows
    ]


@router.get("/call-history/detail", response_model=list[CallHistoryDetailRow])
def get_call_history_detail(
    from_date: date, to_date: date, entity_id: int = 0, mobile_no: str = ""
) -> list[CallHistoryDetailRow]:
    # Previously took from_date/to_date as raw strings, passed straight
    # through to the proc on the assumption the linking page's query string
    # already carried dd-MMM-yyyy text. It actually sent
    # fromDate.toISOString() — a full UTC datetime still carrying whatever
    # time-of-day the Date object held (not midnight) — which SQL Server
    # does parse into a DATETIME, but SIPEventRegisterCDR.CallDate is always
    # stored at exact midnight (confirmed live: CallTime carries the real
    # time, CallDate never does), so `CallDate >= '...T08:30:00Z'` silently
    # excluded the entire day instead of erroring. Typing these as `date`
    # (same as get_call_history_summary, which never had this bug) forces a
    # clean calendar date in, then reformats it the exact same safe way
    # before it ever reaches the proc.
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Proc_CallHistory ?, ?, ?, ?, 0",
            from_date.strftime("%d-%b-%Y"), to_date.strftime("%d-%b-%Y"), entity_id, mobile_no,
        )
        rows = rows_to_dicts(cursor)

    return [
        CallHistoryDetailRow(
            source_name=r["SourceName"] or "", call_time=r["CallTime"].isoformat() if r["CallTime"] else "",
            source_no=r["SourceNo"] or "", duration=str(r["Duration"] or ""), bill_seconds=r["BillSeconds"] or 0,
            call_type=r["CallType"] or "", received_by=r["ReceivedBy"] or "",
        )
        for r in rows
    ]


@router.get("/missed-calls", response_model=list[MissedCallRow])
def get_missed_calls(product: str = "", from_date: date | None = None, to_date: date | None = None) -> list[MissedCallRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_SIPMissedCalls ?, ?, ?",
            from_date.isoformat() if from_date else None,
            to_date.isoformat() if to_date else None,
            product,
        )
        rows = rows_to_dicts(cursor)

    return [
        MissedCallRow(
            record_no=r["RecordNo"], entity_id=r["EntityID"] or 0, entity_name=r["EntityName"] or "",
            entity_phone=r["EntityPhone"] or "", call_date=r["CallDate"].isoformat() if r["CallDate"] else "",
            min_call_time=r["MinCallTime"].isoformat() if r["MinCallTime"] else "",
            call_time=r["CallTime"].isoformat() if r["CallTime"] else "", attempt_count=r["Cnt"] or 0,
            executive_name=r["ExecutiveName"] or "", amc_amount=float(r["AMCAmount"] or 0),
            amc_expiry_date=r["AMCExpiryDate"].isoformat() if r["AMCExpiryDate"] else "",
            ledger_balance=float(r["Ledgerbalance"] or 0), action_state_id=r["ActionStateID"] or 0,
            processed=r["Processed"] or 0, software_name=r["Software Name"] or r.get("Software") or "",
            installed_within_one_month=bool(r["SIDateDifflessThanOneMonth"]),
            advance_amc=(r.get("AdvanceAMC") or "") == "ADVANCE AMC",
        )
        for r in rows
    ]


@router.get("/close-popup/narration")
def get_event_narration(record_no: int) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ProcessDescription FROM SynapseCDR.dbo.SIPEventRegisterCDR WHERE RecordNo = ?", record_no
        )
        row = cursor.fetchone()
    return (row[0] or "") if row else ""


@router.get("/close-popup/call-attempts")
def get_call_attempts(record_no: int, from_date: str, to_date: str) -> list[dict]:
    """from_date/to_date arrive as dd-MMM-yyyy text straight from the query string, same as get_call_history_detail."""
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC proc_SIPMissCallAttemptList ?, ?, ?",
            record_no, from_date, to_date,
        )
        rows = rows_to_dicts(cursor)

    return [
        {
            "attemptTime": r["CallTime"].isoformat() if r.get("CallTime") else "",
            "duration": str(r.get("Message") or ""),
            "status": r.get("PhoneNo") or "",
        }
        for r in rows
    ]


@router.post("/close-popup/close")
def close_event(
    body: CloseEventRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, object]:
    narration = f"{body.narration}-By {current_user.username} at {datetime.now().strftime('%d-%b-%Y %I:%M %p')}"
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_UpdateSIPEventRegisterCDR ?, ?, ?, ?, ?, ?, ?, ?",
            body.record_no, body.cust_id, body.action_state_id, narration,
            body.add_dependent, body.mobile_no, body.name, current_user.user_id,
        )
        rows = rows_to_dicts(cursor)

    if not rows:
        return {"success": False, "message": "Failed to save record !"}
    result_code = rows[0].get("ResultCode")
    result_msg = rows[0].get("ResultMsg", "")
    return {"success": str(result_code) == "100", "message": result_msg}
