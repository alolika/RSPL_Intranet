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
from app.rights import FORM_CALL_HISTORY, require_access

router = APIRouter(prefix="/support", tags=["support-callsip"])

# A registered customer's own EntityID, or ("phone", their number) for an
# unregistered caller with no EntityID at all — see
# get_call_history_summary's grouping logic below.
_GroupKey = int | tuple[str, str]


class CallHistorySummaryRow(BaseModel):
    entity_id: int
    entity_name: str
    # The caller/callee's raw phone number — always populated (see
    # get_call_history_summary's 2026-08-04 update note), needed for
    # unregistered rows (entity_id=0) since they have no EntityID for the
    # detail drill-down to key off of; falls back to matching by this number
    # instead (get_call_history_detail already supports that).
    entity_phone: str
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
    recording_path: str


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


def _display_entity_name(name: str) -> str:
    """Replicates Proc_CallHistory's own display-name truncation exactly:
    `Substring(EntityName,0,charindex('-',EntityName))`, falling back to the
    full name whenever that substring comes back empty (no hyphen at all, or
    a hyphen as the very first character). T-SQL's SUBSTRING(x, 0, p) with a
    1-based hyphen position p returns exactly the first (p-1) characters —
    i.e. Python's `name[:p-1]`, which is just `name.partition('-')[0]`.

    A blank name means the call's other party didn't match any known
    CustomerMaster/CustDependents row at all (an "unregistered" phone number
    — see get_call_history_summary's 2026-08-04 update note) — shown as
    "Unregistered" per explicit request, rather than a blank cell.
    """
    if not name:
        return "Unregistered"
    before = name.partition("-")[0]
    return before if before else name


def _get_sip_extension_lookup() -> dict[int, str]:
    """Maps UserMaster.UserID -> SIPExtentionNo.

    Proc_CallHistory's own `Extention` column is the internal party's
    UserID (SourceEntityID/DestinationEntityID), NOT the real SIP extension
    number staff actually dial/see on their phone — confirmed live these
    are genuinely different values for the same person (e.g. Avinash Kore
    is UserID 153 but his real SIPExtentionNo is '75'; UserID 75 itself
    belongs to a completely unrelated person, Ganesh Taware). An earlier
    revision of the Extension search compared the search term directly
    against Extention/UserID, which is exactly why it matched the wrong
    person's calls — this lookup is what makes the search compare against
    the real extension number instead.
    """
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, SIPExtentionNo FROM UserMaster WHERE SIPExtentionNo IS NOT NULL AND SIPExtentionNo <> ''")
        rows = rows_to_dicts(cursor)
    return {r["UserID"]: r["SIPExtentionNo"] for r in rows}


@router.get("/call-history/summary", response_model=list[CallHistorySummaryRow])
def get_call_history_summary(
    from_date: date, to_date: date, customer_id: int = 0, mobile_no: str = "",
    extension_search: str = "", executive_search: str = "",
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CallHistorySummaryRow]:
    require_access(current_user.user_id, FORM_CALL_HISTORY, "view")
    # Rewritten (2026-07-30) to bypass the legacy Proc_CallHistory entirely
    # for this mode, rather than adding yet another read on top of it. The
    # proc's summary branch built its own filtered rowset into a table
    # variable (no statistics -> poor cardinality estimates), copying every
    # wide column including nvarchar(max) RecordingPath/ProcessDescription,
    # then ran 5 CORRELATED scalar subqueries per distinct EntityID over
    # that table variable to get NoOfIncomingCall/NoOfOutGoingCall/First-
    # /LastIncomingCallTime/TotalCallDuration — effectively O(rows x distinct
    # entities). On top of that, this endpoint used to run a SECOND full
    # rescan of the same 300K+-row table (_get_answered_unanswered_counts,
    # now removed) just to get the answered/missed split. Confirmed live
    # this was the actual bottleneck: a same-day query took ~4.7s and
    # anything wider than a couple of days (a normal 7-30 day report range)
    # timed out past the pooled connection's 60s cap and 500'd outright.
    #
    # This single query does one pass over SIPEventRegisterCDR (`base`,
    # narrow projection — no RecordingPath/ProcessDescription/etc.), then a
    # single set-based GROUP BY (`agg`) for every count/min/max/sum instead
    # of correlated subqueries, then joins the two. Every column name and
    # EntityID/Extention/executive computation matches Proc_CallHistory's
    # own summary branch exactly (see the proc's stored definition — same
    # CASE expressions, same CallType buckets).
    #
    # Update (2026-08-04): deliberately DROPPED the proc's own
    # `EntityName IS NOT NULL AND EntityName <> ''` guard — per explicit
    # request/investigation, that guard was silently excluding real calls
    # from/to phone numbers that don't match any CustomerMaster/
    # CustDependents row ("unregistered" numbers). Confirmed live: a small
    # but real slice of SIPEventRegisterCDR (~75 of 46K rows over a month,
    # 49 distinct phone numbers) has a blank EntityName for exactly this
    # reason, every one of them a normal-looking external call with a real
    # phone number, call time, duration, and call type — not junk/malformed
    # data. These now show with entity_id=0 and entity_name="Unregistered"
    # (see _display_entity_name) instead of being silently dropped.
    #
    # GroupPhone below is what keeps this safe for EXISTING (registered)
    # rows: it's NULL for every EntityID != 0 row (so GROUP BY/JOIN still
    # merges all of a real customer's calls purely by EntityID, unchanged —
    # SQL treats NULL = NULL as equal for grouping purposes), and only
    # becomes the real EntityPhone when EntityID = 0. Without this, the
    # `agg` CTE's GROUP BY EntityID alone would have collapsed EVERY
    # unregistered number in the date range into one combined row (they all
    # share EntityID=0), hiding the fact that they're 49 unrelated callers.
    extra_where = ""
    extra_params: list = []
    with get_cursor() as cursor:
        resolved_entity_id = _resolve_entity_id(cursor, customer_id, mobile_no)
        if resolved_entity_id > 0:
            extra_where = " AND EntityID = ?"
            extra_params = [resolved_entity_id]
        elif mobile_no:
            # RIGHT(?, 10) on the parameter too (matches get_call_attempts's
            # own established convention below) — EntityPhone can carry a
            # country-code prefix (e.g. "+919823497562", the raw CDR value
            # for an unregistered caller passed straight through as
            # entityPhone — see the 2026-08-04 update note above), which a
            # bare `RIGHT(EntityPhone, 10) = ?` never matched unless the
            # caller happened to pass an already-10-digit mobile_no.
            extra_where = " AND RIGHT(EntityPhone, 10) = RIGHT(?, 10)"
            extra_params = [mobile_no]

        cursor.execute(
            f"""
            WITH base AS (
                SELECT
                    CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceEntityID ELSE s.DestinationEntityID END AS EntityID,
                    CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceNo ELSE s.DestinationNo END AS EntityPhone,
                    CASE WHEN LEN(s.SourceNo) <= 3 THEN s.SourceEntityID ELSE s.DestinationEntityID END AS Extention,
                    CASE WHEN LEN(s.SourceNo) <= 3 THEN s.SourceName ELSE s.DestinationName END AS executive,
                    CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceName ELSE s.DestinationName END AS EntityName,
                    s.CallTime, s.CallType, s.Duration
                FROM synapsecdr.dbo.SIPEventRegisterCDR s
                WHERE (s.SourceEntityTypeID = 3 OR s.DestinationEntityTypeID = 3)
                  AND s.CallDate >= ? AND s.CallDate <= ?
            ),
            filtered AS (
                SELECT *, CASE WHEN EntityID = 0 THEN EntityPhone ELSE NULL END AS GroupPhone
                FROM base
                WHERE 1 = 1 {extra_where}
            ),
            agg AS (
                SELECT
                    EntityID, GroupPhone,
                    SUM(CASE WHEN CallType = 'External-incoming' THEN 1 ELSE 0 END) AS AnsweredIncoming,
                    SUM(CASE WHEN CallType IN ('External-incoming_busy', 'External-incoming_missed') THEN 1 ELSE 0 END) AS MissedIncoming,
                    SUM(CASE WHEN CallType = 'External-outgoing' THEN 1 ELSE 0 END) AS AnsweredOutgoing,
                    SUM(CASE WHEN CallType = 'External-outgoing_missed' THEN 1 ELSE 0 END) AS MissedOutgoing,
                    SUM(CASE WHEN CallType IN ('External-incoming', 'External-incoming_busy', 'External-incoming_missed') THEN 1 ELSE 0 END) AS NoOfIncomingCall,
                    SUM(CASE WHEN CallType IN ('External-outgoing', 'External-outgoing_missed') THEN 1 ELSE 0 END) AS NoOfOutGoingCall,
                    MIN(CASE WHEN CallType IN ('External-incoming', 'External-incoming_busy', 'External-incoming_missed') THEN CallTime END) AS FirstIncomingCallTime,
                    MAX(CASE WHEN CallType IN ('External-incoming', 'External-incoming_busy', 'External-incoming_missed') THEN CallTime END) AS LastIncomingCallTime,
                    SUM(Duration) AS TotalCallDuration
                FROM filtered
                GROUP BY EntityID, GroupPhone
            )
            SELECT DISTINCT f.EntityID, f.EntityPhone, f.GroupPhone, f.EntityName, f.Extention, f.executive,
                a.NoOfIncomingCall, a.NoOfOutGoingCall, a.FirstIncomingCallTime, a.LastIncomingCallTime, a.TotalCallDuration,
                a.AnsweredIncoming, a.MissedIncoming, a.AnsweredOutgoing, a.MissedOutgoing
            FROM filtered f
            JOIN agg a ON a.EntityID = f.EntityID AND ISNULL(a.GroupPhone, '') = ISNULL(f.GroupPhone, '')
            """,
            from_date, to_date, *extra_params,
        )
        rows = rows_to_dicts(cursor)

    # Same grouping the old code did for registered customers (one row per
    # (EntityID, Extention, executive) combination survives from the query
    # above, matching Proc_CallHistory's own output shape) — every row for a
    # given EntityID+GroupPhone carrying identical agg columns, grouped here
    # so the Extension/Executive searches below can scan every tagged
    # combination, and so a customer handled by multiple executives
    # collapses to one grid row. Unregistered rows (EntityID=0) key by phone
    # number instead (mirrors _merge_missed_calls_by_customer's own
    # established entity-or-phone fallback pattern) so 49 unrelated
    # unregistered callers stay 49 separate rows instead of one combined blob.
    by_entity: dict[_GroupKey, list[dict]] = {}
    for r in rows:
        key: _GroupKey = r["EntityID"] if r["EntityID"] else ("phone", r["EntityPhone"])
        by_entity.setdefault(key, []).append(r)

    # Two independent search fields, per explicit request (replacing the
    # earlier single auto-detecting box, which searched Extention/UserID
    # directly instead of the real SIP extension number — see
    # _get_sip_extension_lookup's own comment). Each narrows the result set
    # further on top of whatever customer_id/mobile_no already selected;
    # when both are filled, a customer must match both (AND), not either.
    matching_keys: set[_GroupKey] | None = None

    extension_search = extension_search.strip()
    if extension_search:
        extension_lookup = _get_sip_extension_lookup()
        matching_keys = {
            key
            for key, group in by_entity.items()
            if any(extension_lookup.get(g.get("Extention") or 0, "") == extension_search for g in group)
        }

    executive_search = executive_search.strip()
    if executive_search:
        needle = executive_search.lower()
        exec_matches = {
            key
            for key, group in by_entity.items()
            if any(needle in (g.get("executive") or "").lower() for g in group)
        }
        matching_keys = exec_matches if matching_keys is None else (matching_keys & exec_matches)

    result: list[CallHistorySummaryRow] = []
    for key, group in by_entity.items():
        if matching_keys is not None and key not in matching_keys:
            continue
        # Prefer the row with no specific Extention/executive tag (a stable,
        # predictable representative when one exists) — every row in the
        # group carries identical aggregate values regardless, so falling
        # back to the first row when no such "untagged" row exists is
        # equally correct, just an arbitrary pick among equals.
        r = next((g for g in group if not (g.get("Extention") or 0) and not (g.get("executive") or "").strip()), group[0])
        result.append(
            CallHistorySummaryRow(
                entity_id=r["EntityID"] or 0, entity_phone=r["EntityPhone"] or "",
                entity_name=_display_entity_name(r["EntityName"] or ""),
                no_of_incoming_call=r["NoOfIncomingCall"] or 0, no_of_outgoing_call=r["NoOfOutGoingCall"] or 0,
                first_incoming_call_time=r["FirstIncomingCallTime"].isoformat() if r["FirstIncomingCallTime"] else None,
                last_incoming_call_time=r["LastIncomingCallTime"].isoformat() if r["LastIncomingCallTime"] else None,
                total_call_duration=str(r["TotalCallDuration"] or ""),
                answered_incoming_calls=r["AnsweredIncoming"] or 0,
                missed_incoming_calls=r["MissedIncoming"] or 0,
                answered_outgoing_calls=r["AnsweredOutgoing"] or 0,
                missed_outgoing_calls=r["MissedOutgoing"] or 0,
            )
        )
    return result


@router.get("/call-history/detail", response_model=list[CallHistoryDetailRow])
def get_call_history_detail(
    from_date: date, to_date: date, entity_id: int = 0, mobile_no: str = "",
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CallHistoryDetailRow]:
    require_access(current_user.user_id, FORM_CALL_HISTORY, "view")
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
    # Rewritten (2026-07-30) to bypass Proc_CallHistory's detail branch (IsSummary=0)
    # entirely — that SELECT never projects RecordingPath at all (confirmed against
    # the proc's own stored definition), so there's no way to add the recording
    # "listen" feature on top of it without a schema/proc change. This query
    # reproduces that branch's own logic exactly: it built two candidate rows per
    # underlying call (once treating SourceNo as "the phone field", once
    # DestinationNo) via a UNION, then kept only whichever one had the real
    # (length>5) external number — equivalent to picking that value directly with
    # a CASE, one pass instead of a union of doubled rows.
    extra_where = ""
    extra_params: list = []
    with get_cursor() as cursor:
        resolved_entity_id = _resolve_entity_id(cursor, entity_id, mobile_no)
        if resolved_entity_id > 0:
            extra_where = " AND (CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceEntityID ELSE s.DestinationEntityID END) = ?"
            extra_params = [resolved_entity_id]
        elif mobile_no:
            # RIGHT(?, 10) on the parameter too — see get_call_history_summary's
            # matching fix/comment above (same latent bug, same fix).
            extra_where = " AND RIGHT((CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceNo ELSE s.DestinationNo END), 10) = RIGHT(?, 10)"
            extra_params = [mobile_no]

        cursor.execute(
            f"""
            SELECT * FROM (
                SELECT
                    s.CallType, s.CallTime,
                    CASE WHEN LEN(s.SourceNo) > 5 THEN s.SourceNo ELSE s.DestinationNo END AS SourceNo,
                    s.Duration, s.BillSeconds, s.SourceName, s.SourceEntityID,
                    s.DestinationName AS ReceivedBy, s.RecordingPath
                FROM synapsecdr.dbo.SIPEventRegisterCDR s
                WHERE (s.SourceEntityTypeID = 3 OR s.DestinationEntityTypeID = 3)
                  AND s.CallDate >= ? AND s.CallDate <= ? {extra_where}
            ) a
            WHERE LEN(a.SourceNo) > 5
            ORDER BY CallTime
            """,
            from_date, to_date, *extra_params,
        )
        rows = rows_to_dicts(cursor)

    return [
        CallHistoryDetailRow(
            source_name=r["SourceName"] or "", call_time=r["CallTime"].isoformat() if r["CallTime"] else "",
            source_no=r["SourceNo"] or "", duration=str(r["Duration"] or ""), bill_seconds=r["BillSeconds"] or 0,
            call_type=r["CallType"] or "", received_by=r["ReceivedBy"] or "",
            recording_path=r["RecordingPath"] or "",
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
    # WITH RECOMPILE (2026-07-30): confirmed live this is a textbook
    # parameter-sniffing cliff, not a query-design problem — the query
    # itself is fast. `SELECT * FROM dbo.webfun_SIPMissedCalls(@from,@to)`
    # alone runs in ~1s even for a full 1-year range, and the exact same
    # logic run as one ad-hoc batch (function + the two CustomerAttributes
    # joins this proc also does) also runs in ~1s. But calling the actual
    # WebProc_SIPMissedCalls stored proc with a 1-year range took 26.7s on
    # its first execution (using whatever plan was cached from a prior,
    # much-narrower typical date range — this report is usually run for a
    # single day or week), then dropped straight back to ~1s on every
    # subsequent call with that same wide range once SQL Server had a plan
    # shaped for it. That cliff would come back at random (a narrow-range
    # call recompiling it back to a narrow-shaped plan) for whichever user
    # next tries a wide "look back over months" search, which is a normal
    # thing to do on a *pending* missed-calls report. WITH RECOMPILE forces
    # a fresh plan sized to the actual parameters every call — measured at
    # ~0.9s even freshly compiled for the 1-year case, i.e. the compile
    # overhead this trades in is negligible next to the cliff it avoids.
    # This only changes how our endpoint invokes the shared proc (an
    # EXECUTE-statement option, not a proc/schema change) — its logic,
    # output, and every other caller are untouched.
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC WebProc_SIPMissedCalls ?, ?, ? WITH RECOMPILE",
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
