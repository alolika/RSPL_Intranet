import queue
import threading
from contextlib import contextmanager
from typing import Any, Generator

import pyodbc

from app.config import settings

# A handful of long-lived connections, opened once and reused across
# requests, instead of opening (and closing) a brand-new one per request.
# Confirmed live that the *first* connection from a fresh process to this
# remote SQL Server (rspldemosql.retailware.in) can take 100+ seconds — raw
# network latency to that host is only ~37ms, so the cost is in the ODBC
# driver/TLS handshake, not the network itself — while warm reconnects from
# the same process cost 0.2-0.7s. Opening a fresh connection per request (the
# previous behavior) meant every single API call paid that 0.2-0.7s handshake
# on top of its query time, and since most pages fire 3-4 sequential calls on
# load, that overhead alone added multiple seconds to every page — this was
# the dominant cause of the app feeling slow everywhere, not any one page's
# queries.
_POOL_SIZE = 5
_pool: "queue.Queue[pyodbc.Connection]" = queue.Queue()
_pool_lock = threading.Lock()
_pool_ready = False


def _connection_string() -> str:
    return (
        f"DRIVER={settings.db_driver};"
        f"SERVER={settings.db_server},{settings.db_port};"
        f"DATABASE={settings.db_name};"
        f"UID={settings.db_user};"
        f"PWD={settings.db_password};"
        "TrustServerCertificate=yes;"
    )


def _new_connection() -> pyodbc.Connection:
    # `timeout=` here bounds the connect/login handshake itself (separate
    # from conn.timeout below, which bounds query execution). Without it,
    # pyodbc.connect() has no cap at all — confirmed live that when the
    # network path to this remote SQL Server is degraded, a fresh connect
    # attempt can hang far past the ~100s "cold" cost documented below,
    # which is exactly what turned a single dead pooled connection into a
    # 10+ minute request when the replacement was opened synchronously
    # (see get_cursor()'s finally block).
    conn = pyodbc.connect(_connection_string(), timeout=10)
    # Safety net, not a performance tuning knob: without this, a single
    # runaway/hung query (e.g. webproc_LeadChartSummaryV2, confirmed live to
    # sometimes exceed 60s re-evaluating a heavy view ~10 times per call)
    # ties up one of only 5 pooled connections indefinitely — get_cursor()
    # never gets an exception to react to, so that slot is gone for the rest
    # of the process's life. 60s is generous enough not to trip on any
    # already-slow-but-working query seen elsewhere in this app; a query
    # past that is failing the user regardless, so surfacing a clean error
    # is strictly better than an indefinite hang either way.
    conn.timeout = 60
    return conn


def _replenish_pool_async() -> None:
    """Opens a replacement pooled connection in the background instead of
    blocking whichever request's cursor just died — a synchronous reconnect
    here was the actual cause of logins taking 10-15 minutes: the failing
    query would error out reasonably quickly, but the *cleanup* then paid
    the full uncapped reconnect cost before the client ever saw a response.
    If the DB is still unreachable, retries on a short delay instead of
    permanently shrinking the pool."""

    def _worker() -> None:
        try:
            _pool.put(_new_connection())
        except pyodbc.Error:
            threading.Timer(5.0, _replenish_pool_async).start()

    threading.Thread(target=_worker, daemon=True).start()


def warm_pool() -> None:
    """Opens the pool's connections up front (called from main.py's startup
    hook) so the expensive first-connection cost is paid once while the
    server is starting, not on whichever user's request happens to arrive
    first. Safe to call more than once — only actually connects the first
    time."""
    global _pool_ready
    if _pool_ready:
        return
    with _pool_lock:
        if _pool_ready:
            return
        for _ in range(_POOL_SIZE):
            _pool.put(_new_connection())
        _pool_ready = True


@contextmanager
def get_cursor() -> Generator[pyodbc.Cursor, None, None]:
    """Yields a cursor from the connection pool; commits on success, rolls
    back on error. A connection that errors is discarded and replaced with a
    fresh one rather than returned to the pool, so a dropped/stale connection
    (e.g. after a long idle period) self-heals instead of poisoning every
    future request that happens to draw it.

    Every caller uses parameterized queries (`?` placeholders) — unlike the
    source app's string-concatenated SQL, which is deliberately not carried
    forward.
    """
    warm_pool()
    conn = _pool.get()
    healthy = True
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        healthy = False
        try:
            conn.rollback()
        except pyodbc.Error:
            pass
        raise
    finally:
        if healthy:
            _pool.put(conn)
        else:
            try:
                conn.close()
            except pyodbc.Error:
                pass
            _replenish_pool_async()


def rows_to_dicts(cursor: pyodbc.Cursor, limit: int | None = None) -> list[dict[str, Any]]:
    """Converts the current result set to dicts, skipping ahead past any
    leading non-SELECT statements (INSERT/UPDATE/dynamic-SQL EXEC) a stored
    proc may run before its actual SELECT — pyodbc doesn't do this itself and
    raises "No results" if left on one of those statements.

    `limit`, when given, uses `fetchmany()` instead of `fetchall()` — for a
    proc with no server-side TOP/ORDER BY (so there's no way to cap the
    result set in SQL), this stops pulling rows over the wire once enough
    have arrived instead of transferring the whole result set just to
    discard most of it in Python afterward. Confirmed live on
    RSPL_SalesDashborad_OpenLeads (60k+ unordered rows): fetchall() took
    ~7s, fetchmany(1000) ~0.2s, for the exact same rows the caller keeps.
    """
    while cursor.description is None:
        if not cursor.nextset():
            return []
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchmany(limit) if limit is not None else cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


def first_row_or_none(cursor: pyodbc.Cursor) -> dict[str, Any] | None:
    rows = rows_to_dicts(cursor)
    return rows[0] if rows else None
