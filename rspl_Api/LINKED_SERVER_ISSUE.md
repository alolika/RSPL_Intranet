# Linked Server Issue — `License`

**Date found:** 2026-07-22
**Status: RESOLVED (2026-07-22)** — all 3 affected features confirmed working live.
**Server affected:** `retailware.no-ip.org,1954` / database `Worldnettech`
**Linked server target:** `myretailware.database.windows.net,1433` (Azure SQL),
catalog `License_Retailware`, login `myretailware`

## Resolution (2026-07-22)

Root cause confirmed: the `License` linked server was already correctly attached
(server, catalog, and login all verified matching, and the login's credentials
tested successfully directly against Azure SQL). The remaining failure was purely
the `remote proc transaction promotion` option requiring MSDTC for `EXEC`-style
remote procedure calls. Fixed by running (outside a transaction, via an autocommit
connection):

```sql
EXEC sp_serveroption 'License', 'remote proc transaction promotion', 'false';
```

Re-verified live immediately after, through the real API, across multiple
customers:

| Feature | Query type | Status |
|---|---|---|
| Show Pirated from Online | `SELECT` against a table-valued function (`fun_Web_CustomerLicensePiracy`) | ✅ `200 OK` |
| EInvoice Status | `SELECT * FROM vw_web_Einvoice` (view) | ✅ `200 OK` |
| Show Exe Version | `EXEC license_WebProc_CheckSoftwareVersions` (remote stored procedure) | ✅ `200 OK` — confirmed across 5+ different CustIDs, no errors |

All 3 features are now fully functional. Everything below is kept as history of
how the issue was diagnosed.

## Original issue (2026-07-22, before the fix)

Linked server `License` failed on distributed-transaction begin for **all**
access (reads and remote procedure calls alike).

## Error (reproduced live)

```
[42000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]The OLE DB provider
"MSOLEDBSQL" for linked server "License" reported an error. One or more arguments
were reported invalid by the provider. (7399)
[SQL Server]The operation could not be performed because OLE DB provider "MSOLEDBSQL"
for linked server "License" was unable to begin a distributed transaction. (7391)
[SQL Server]OLE DB provider "MSOLEDBSQL" for linked server "License" returned message
"The parameter is incorrect.". (7412)
```

## Reproduction

```sql
EXEC license_WebProc_CheckSoftwareVersions <CustID>, 1, ''
```

Fails immediately (~0.4s), confirming it's a connection/configuration error, not a
timeout or data issue.

## What's confirmed working

The linked server *is* registered — `SELECT name FROM sys.servers` lists `License`
— so this isn't a missing-linked-server problem like on the previous database. It's
a transaction-promotion/MSDTC configuration issue on that specific linked server.

## Likely fix area (for the DBA to confirm)

Either MSDTC isn't running/configured on this SQL Server instance, or the linked
server needs `remote proc transaction promotion` disabled, e.g.:

```sql
EXEC sp_serveroption 'License', 'remote proc transaction promotion', 'false';
```

This is a suggestion based on the error signature, not something that has been
applied — a DBA should verify before changing linked-server options on production.

## Impact

**All 3 originally blocked features are now fixed and confirmed live** (see
Resolution above):
- ✅ Trouble Ticket's "Show Exe Version"
- ✅ Modules' "Show Pirated from Online"
- ✅ EInvoice Status
