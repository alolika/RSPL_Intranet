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

Update (2026-07-28): attempt_missed_call() added — MissedCall.aspx's "Click
to Attempt" action (Angular: missed-call.ts's attempt()) was a pure
client-side stub in SupportDataService.attemptCall() (no HTTP call at all)
from back when SynapseCDR didn't exist, so there was nothing to write to.
Confirmed via sys.objects there's no dedicated "record an attempt" proc —
ExecutiveName is a real nvarchar(500) column on SIPEventRegisterCDR that
webfun_SIPMissedCalls reads directly, so a plain UPDATE by RecordNo is the
correct write, not a missing/undiscovered proc. Worth knowing:
webfun_SIPMissedCalls's grouping includes ExecutiveName as a GROUP BY key
when aggregating multiple raw CDR rows per entity/day into one grid row (via
MAX(RecordNo)) — if a given entity has more than one underlying CDR row for
the same day and only one gets its ExecutiveName updated here, the next
fetch could in theory split back into two grid rows instead of one updating
cleanly. Not fixed here (would need the legacy source's own Attempt
click-handler to confirm whether it updates by RecordNo alone or by the
whole entity/day group, and that source isn't on this machine) — flagged for
whoever revisits this if a duplicate-row report ever comes in.

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

from fastapi import APIRouter, Depends, HTTPException
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
    # Proc_CallHistory's own summary mode (@IsSummary=1) has no
    # answered/missed breakdown at all — NoOfIncomingCall/NoOfOutGoingCall
    # above already blend answered and missed together by design (see the
    # proc's own ABC CTE: CallType IN ('External-incoming',
    # 'External-incoming_busy', 'External-incoming_missed') for incoming,
    # ('External-outgoing', 'External-outgoing_missed') for outgoing).
    # These four counts are computed separately (see
    # _get_answered_unanswered_counts below) by re-querying
    # SIPEventRegisterCDR directly with the identical EntityID/date/mobile
    # resolution the proc itself uses, splitting each existing direction
    # bucket into its answered vs missed halves — deliberately NOT a single
    # flat "answered"/"unanswered" pair, so the frontend can combine this
    # with the existing Call Type (Incoming/Outgoing) filter correctly
    # (e.g. "Incoming + Unanswered" must only count missed INCOMING calls,
    # not any missed call regardless of direction).
    answered_incoming_calls: int
    missed_incoming_calls: int
    answered_outgoing_calls: int
    missed_outgoing_calls: int


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


class AttemptCallRequest(BaseModel):
    record_no: int
    executive_name: str


def _resolve_entity_id(cursor, entity_id: int, mobile_no: str) -> int:
    # Mirrors Proc_CallHistory's own resolution step exactly (see "If
    # @EntityID=0 and @Mobileno<>''" in the proc source): a mobile number
    # search with no explicit customer picked resolves to whichever
    # CustomerMaster or CustDependents row owns that number, the same way
    # the proc does it before building its own WHERE clause.
    if entity_id == 0 and mobile_no:
        cursor.execute(
            "SELECT TOP 1 CustID FROM ("
            "  SELECT CustID FROM CustomerMaster WHERE Mobileno = ?"
            "  UNION"
            "  SELECT CustID FROM CustDependents WHERE DepMobileNo = ?"
            ") a",
            mobile_no, mobile_no,
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    return entity_id


def _get_answered_unanswered_counts(
    from_date: date, to_date: date, customer_id: int, mobile_no: str
) -> dict[int, tuple[int, int, int, int]]:
    """Returns {EntityID: (answered_incoming, missed_incoming, answered_outgoing, missed_outgoing)}.

    Queries synapsecdr.dbo.SIPEventRegisterCDR directly rather than adding a
    mode to the shared legacy Proc_CallHistory — replicates that proc's own
    EntityID computation ("case when len(SourceNo)>5 then SourceEntityID
    else DestinationEntityID end") and date/entity/mobile filtering exactly,
    so results stay consistent with the NoOfIncomingCall/NoOfOutGoingCall
    counts the proc already returns, just split by answered vs missed.
    """
    with get_cursor() as cursor:
        resolved_entity_id = _resolve_entity_id(cursor, customer_id, mobile_no)

        where_clauses = ["(s.SourceEntityTypeID = 3 OR s.DestinationEntityTypeID = 3)", "s.CallDate >= ? AND s.CallDate <= ?"]
        params: list = [from_date, to_date]
        if resolved_entity_id > 0:
            where_clauses.append("(CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceEntityID ELSE s.DestinationEntityID END) = ?")
            params.append(resolved_entity_id)
        elif mobile_no:
            where_clauses.append(
                "RIGHT((CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceNo ELSE s.DestinationNo END), 10) = ?"
            )
            params.append(mobile_no)

        cursor.execute(
            "SELECT "
            "  (CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceEntityID ELSE s.DestinationEntityID END) AS EntityID, "
            "  SUM(CASE WHEN s.CallType = 'External-incoming' THEN 1 ELSE 0 END) AS AnsweredIncoming, "
            "  SUM(CASE WHEN s.CallType IN ('External-incoming_busy', 'External-incoming_missed') THEN 1 ELSE 0 END) AS MissedIncoming, "
            "  SUM(CASE WHEN s.CallType = 'External-outgoing' THEN 1 ELSE 0 END) AS AnsweredOutgoing, "
            "  SUM(CASE WHEN s.CallType = 'External-outgoing_missed' THEN 1 ELSE 0 END) AS MissedOutgoing "
            f"FROM synapsecdr.dbo.SIPEventRegisterCDR s WHERE {' AND '.join(where_clauses)} "
            "GROUP BY (CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceEntityID ELSE s.DestinationEntityID END)",
            *params,
        )
        rows = rows_to_dicts(cursor)

    return {
        r["EntityID"]: (r["AnsweredIncoming"] or 0, r["MissedIncoming"] or 0, r["AnsweredOutgoing"] or 0, r["MissedOutgoing"] or 0)
        for r in rows
    }


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

    status_counts = _get_answered_unanswered_counts(from_date, to_date, customer_id, mobile_no)

    return [
        CallHistorySummaryRow(
            entity_id=r["EntityID"], entity_name=r["EntityName"] or "",
            no_of_incoming_call=r["NoOfIncomingCall"] or 0, no_of_outgoing_call=r["NoOfOutGoingCall"] or 0,
            first_incoming_call_time=r["FirstIncomingCallTime"].isoformat() if r["FirstIncomingCallTime"] else None,
            last_incoming_call_time=r["LastIncomingCallTime"].isoformat() if r["LastIncomingCallTime"] else None,
            total_call_duration=str(r["TotalCallDuration"] or ""),
            answered_incoming_calls=status_counts.get(r["EntityID"], (0, 0, 0, 0))[0],
            missed_incoming_calls=status_counts.get(r["EntityID"], (0, 0, 0, 0))[1],
            answered_outgoing_calls=status_counts.get(r["EntityID"], (0, 0, 0, 0))[2],
            missed_outgoing_calls=status_counts.get(r["EntityID"], (0, 0, 0, 0))[3],
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


def _merge_missed_calls_by_customer(rows: list[dict]) -> list[dict]:
    """webfun_SIPMissedCalls (the table function WebProc_SIPMissedCalls wraps)
    groups by EntityID plus several other per-call columns — CallType,
    ExecutiveName among them — not by EntityID alone. ExecutiveName in
    particular is set per-call once someone clicks Attempt (see
    attempt_missed_call above), so a customer with one already-attempted call
    and one not-yet-attempted call splits into two separate rows from the
    proc, each only spanning its own single call — confirmed live, e.g. one
    real EntityID showed up as two rows, First/Last Call Time identical
    within each because neither row saw the other's call at all.

    Rewriting that ~100-line legacy function's GROUP BY risks side effects on
    the other columns it also computes (EntityName suffixing, Software
    categorization, the outgoing-call ExecutiveName derivation) — safer to
    merge same-customer rows here instead, after the query, touching nothing
    but this endpoint. Registered entities (EntityID != 0) are merged by
    EntityID; unregistered ones (EntityID == 0, no matched customer) are
    merged by phone number instead, mirroring the same two-tier split
    webfun_SIPMissedCalls itself already uses internally.
    """
    groups: dict[tuple[str, object], list[dict]] = {}
    for r in rows:
        key = ("entity", r["EntityID"]) if r["EntityID"] else ("phone", r["EntityPhone"])
        groups.setdefault(key, []).append(r)

    merged: list[dict] = []
    for group_rows in groups.values():
        if len(group_rows) == 1:
            merged.append(group_rows[0])
            continue

        # The most recent call's row is the representative for every
        # entity-level display field (name, software, AMC figures, ledger
        # balance) — those should already be consistent per customer, so
        # picking from the latest call is the most current/accurate choice.
        representative = dict(max(group_rows, key=lambda r: r["CallTime"] or r["MinCallTime"]))
        all_times = [t for r in group_rows for t in (r["MinCallTime"], r["CallTime"]) if t is not None]
        if all_times:
            representative["MinCallTime"] = min(all_times)
            representative["CallTime"] = max(all_times)
        representative["Cnt"] = sum(r["Cnt"] or 0 for r in group_rows)
        # Prefer whichever call in the group already has a recorded
        # Executive Name — merging must never un-attempt a call that was
        # already marked attempted just because a newer, not-yet-attempted
        # call from the same customer came in later.
        representative["ExecutiveName"] = next((r["ExecutiveName"] for r in group_rows if r.get("ExecutiveName")), "")
        merged.append(representative)

    merged.sort(key=lambda r: r["RecordNo"])
    return merged


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

    rows = _merge_missed_calls_by_customer(rows)

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


@router.post("/missed-calls/attempt")
def attempt_missed_call(body: AttemptCallRequest) -> dict[str, bool]:
    # webfun_SIPMissedCalls (the table-valued function get_missed_calls's proc
    # wraps) reads ExecutiveName straight from this real column on
    # SynapseCDR.dbo.SIPEventRegisterCDR — there's no dedicated "record an
    # attempt" proc (confirmed via sys.objects; WebProc_UpdateSIPEventRegisterCDR
    # only handles PreClose/Close, ActionStateID 1/2, and never touches
    # ExecutiveName at all), so a direct parameterized UPDATE is the correct
    # write here, not a missing proc call. Previously this whole action was a
    # client-side stub (`of({success:true}).pipe(delay(150))`, no HTTP call
    # at all) dating back to when SynapseCDR didn't exist on this instance —
    # it exists now, confirmed live via sys.databases.
    #
    # The grid's "Click to Attempt" state is computed client-side from
    # whatever snapshot was last fetched (see missed-call.ts's
    # displayRows()) — with the grid open in more than one browser/tab, two
    # people can both see the same call as not-yet-attempted and both click
    # it. The WHERE clause below makes the UPDATE itself the single
    # authoritative check (no separate SELECT-then-UPDATE, which would leave
    # a race window between the two statements): it only succeeds if
    # ExecutiveName is still blank at the moment this runs. rowcount==0
    # means someone else's attempt already landed first (or the record
    # doesn't exist), so this call is rejected as a conflict rather than
    # silently overwriting whoever got there first — the plain reassignment
    # this endpoint used to always do unconditionally.
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE SynapseCDR.dbo.SIPEventRegisterCDR SET ExecutiveName = ? "
            "WHERE RecordNo = ? AND (ExecutiveName IS NULL OR ExecutiveName = '')",
            body.executive_name,
            body.record_no,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="This call has already been attempted.")
    return {"success": True}


@router.get("/close-popup/narration")
def get_event_narration(record_no: int) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ProcessDescription FROM SynapseCDR.dbo.SIPEventRegisterCDR WHERE RecordNo = ?", record_no
        )
        row = cursor.fetchone()
    return (row[0] or "") if row else ""


@router.get("/close-popup/call-attempts")
def get_call_attempts(entity_id: int, mobile_no: str, from_date: date, to_date: date) -> list[dict]:
    """Replaces the old EXEC proc_SIPMissCallAttemptList ?, ?, ? call (2026-07-28).
    That proc looked up direction (incoming vs outgoing) from a single RecordNo,
    then required a LEFT JOIN to SMSSent to also have an ActionID=6 row whose
    SentDate matched *today's* Options.Billdate — not the day of the call — so
    it only ever showed a call if an unrelated SMS batch happened to fire today,
    which is unrelated to whether the customer actually has call history.
    Confirmed live: a customer with 2 plain incoming-missed calls, no SMS
    involved at all, returned 0 rows from the old proc for exactly this reason.
    It also missed half a customer's history whenever their calls spanned both
    incoming and outgoing-missed direction, since the proc only ever looked at
    one direction (whichever the single RecordNo happened to represent).

    This queries SIPEventRegisterCDR directly for the given EntityID, covering
    both call directions via UNION ALL, with the SMS Message (if any exists)
    left-joined in as enrichment only, never required for a row to show. Same
    fallback pattern as _merge_missed_calls_by_customer: entity_id=0 rows (no
    matched customer) match by mobile_no's last 10 digits instead, mirroring
    webfun_SIPMissedCalls's own right(EntityPhone,10) convention — SourceNo
    carries a country-code prefix, DestinationNo doesn't, so a plain equality
    match would miss half of these.
    """
    with get_cursor() as cursor:
        if entity_id:
            cursor.execute(
                """
                SELECT c.CallTime, c.SourceNo AS PhoneNo, sm.Message
                FROM SynapseCDR.dbo.SIPEventRegisterCDR c
                LEFT OUTER JOIN SMSSent sm ON sm.Voucherno = c.RecordNo
                WHERE c.Discard = 0 AND c.SourceEntityID = ?
                  AND c.CallType IN ('External-incoming_missed', 'External-incoming_busy')
                  AND c.CallDate >= ? AND c.CallDate <= ?
                UNION ALL
                SELECT c.CallTime, c.DestinationNo AS PhoneNo, sm.Message
                FROM SynapseCDR.dbo.SIPEventRegisterCDR c
                LEFT OUTER JOIN SMSSent sm ON sm.Voucherno = c.RecordNo
                WHERE c.Discard = 0 AND c.DestinationEntityID = ?
                  AND c.CallType = 'External-outgoing_missed'
                  AND c.CallDate >= ? AND c.CallDate <= ?
                ORDER BY CallTime
                """,
                entity_id, from_date, to_date, entity_id, from_date, to_date,
            )
        else:
            cursor.execute(
                """
                SELECT c.CallTime, c.SourceNo AS PhoneNo, sm.Message
                FROM SynapseCDR.dbo.SIPEventRegisterCDR c
                LEFT OUTER JOIN SMSSent sm ON sm.Voucherno = c.RecordNo
                WHERE c.Discard = 0 AND RIGHT(c.SourceNo, 10) = RIGHT(?, 10)
                  AND c.CallType IN ('External-incoming_missed', 'External-incoming_busy')
                  AND c.CallDate >= ? AND c.CallDate <= ?
                UNION ALL
                SELECT c.CallTime, c.DestinationNo AS PhoneNo, sm.Message
                FROM SynapseCDR.dbo.SIPEventRegisterCDR c
                LEFT OUTER JOIN SMSSent sm ON sm.Voucherno = c.RecordNo
                WHERE c.Discard = 0 AND RIGHT(c.DestinationNo, 10) = RIGHT(?, 10)
                  AND c.CallType = 'External-outgoing_missed'
                  AND c.CallDate >= ? AND c.CallDate <= ?
                ORDER BY CallTime
                """,
                mobile_no, from_date, to_date, mobile_no, from_date, to_date,
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
