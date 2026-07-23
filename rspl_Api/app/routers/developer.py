from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/developer", tags=["developer"])


class LookupOption(BaseModel):
    label: str
    value: str


class DailyLogEntry(BaseModel):
    log_id: int
    date: str
    for_date: str
    location: str
    product: str
    work_description: str
    plan_for_next_day: str
    reason_for_late_entry: str
    remark: str
    user_name: str
    user_id: int


class SaveDailyLogRequest(BaseModel):
    for_date: str
    location: str
    product: str
    work_description: str
    plan_for_next_day: str = ""
    reason_for_late_entry: str = ""
    remark: str = ""


class TableListRow(BaseModel):
    table_id: int
    table_name: str
    sequence: int
    script: str
    description: str
    trans_name: str | None
    is_track_table: bool
    purpose_of_table: str
    comments: str
    primary_key_info: str
    modify_date: str | None
    questionable: bool


# --- Mirrors Section_Developer/DailyLog.aspx.vb ---


@router.get("/products", response_model=list[LookupOption])
def get_products() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT attrvalue FROM customerattributes WHERE Attributeid = 1 AND attrvalue <> ''"
        )
        return [LookupOption(label=r["attrvalue"], value=r["attrvalue"]) for r in rows_to_dicts(cursor)]


@router.post("/daily-log")
def save_daily_log(
    body: SaveDailyLogRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_DailyLogAdd ?, ?, ?, ?, ?, ?, ?, ?",
            body.for_date,
            current_user.user_id,
            body.location,
            body.product,
            body.work_description,
            body.plan_for_next_day,
            body.reason_for_late_entry,
            body.remark,
        )
    return {"success": True}


# --- Mirrors Section_Developer/DailyLogRegister.aspx.vb ---


@router.get("/users", response_model=list[LookupOption])
def get_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=str(r["UserID"])) for r in rows_to_dicts(cursor)]


@router.get("/daily-logs", response_model=list[DailyLogEntry])
def get_daily_logs(from_date: date, to_date: date, user_id: int | None = None) -> list[DailyLogEntry]:
    query = """
        SELECT dl.VoucherNo, dl.Date, dl.ForDate, dl.Location, dl.Product, dl.WorkDescription,
               dl.PlanForNextDay, dl.ReasonForLateEntry, dl.Remark, dl.UserID, um.Name
        FROM web_dailylog dl
        INNER JOIN UserMaster um ON dl.UserID = um.UserID
        WHERE dl.ForDate >= ? AND dl.ForDate <= ?
    """
    params: list = [from_date, to_date]
    if user_id is not None:
        query += " AND dl.UserID = ?"
        params.append(user_id)

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [
        DailyLogEntry(
            log_id=r["VoucherNo"],
            date=r["Date"].isoformat(),
            for_date=r["ForDate"].isoformat(),
            location=r["Location"] or "",
            product=r["Product"] or "",
            work_description=r["WorkDescription"] or "",
            plan_for_next_day=r["PlanForNextDay"] or "",
            reason_for_late_entry=r["ReasonForLateEntry"] or "",
            remark=r["Remark"] or "",
            user_name=r["Name"],
            user_id=r["UserID"],
        )
        for r in rows
    ]


# --- Mirrors Section_Developer/TableList.aspx.vb ---


def _sync_new_tables(cursor) -> None:
    """Mirrors TableList.aspx.vb's Page_Load insert-new-tables-on-load step (idempotent)."""
    cursor.execute(
        """
        INSERT INTO Retailware_TableList
            (BaseTable, TableName, Sequence, Script, Description, TransactionID,
             IsTrackTable, PurposeOfTable, Comments, PrimaryKeyInfo, ModifyBy, ModifyDate, Questionable)
        SELECT Name, Name, 1, '', '', 0, 0, '', '', '', 0, GETDATE(), 1
        FROM SysObjects
        WHERE Xtype = 'u' AND Name NOT IN (SELECT TableName FROM Retailware_TableList)
        """
    )


@router.get("/yt-descriptions", response_model=list[LookupOption])
def get_yt_descriptions() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT DISTINCT Description FROM Retailware_TableList WHERE Description <> ''")
        return [LookupOption(label=r["Description"], value=r["Description"]) for r in rows_to_dicts(cursor)]


@router.get("/transaction-types", response_model=list[LookupOption])
def get_transaction_types() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT TransName FROM TransTypemaster")
        return [LookupOption(label=r["TransName"], value=r["TransName"]) for r in rows_to_dicts(cursor)]


@router.get("/modified-by-users", response_model=list[LookupOption])
def get_modified_by_users() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Name, UserID FROM UserMaster WHERE UserType IN (4, 1) AND Enabled = 1"
        )
        return [LookupOption(label=r["Name"], value=str(r["UserID"])) for r in rows_to_dicts(cursor)]


@router.get("/table-list", response_model=list[TableListRow])
def get_table_list(
    yt_description: str | None = None, transaction: str | None = None, modified_by: str | None = None
) -> list[TableListRow]:
    query = """
        SELECT R.TableID, R.BaseTable, R.TableName, R.Sequence, R.Script, R.Description,
               T.TransName, R.IsTrackTable, R.PurposeOfTable, R.Comments, R.PrimaryKeyInfo,
               R.ModifyDate, R.Questionable
        FROM Retailware_TableList R
        LEFT OUTER JOIN TransTypeMaster T ON T.TransType = R.TransactionID
        LEFT OUTER JOIN UserMaster U ON U.UserID = R.ModifyBy
        WHERE 1 = 1
    """
    params: list = []
    if yt_description and yt_description != "ALL":
        query += " AND R.Description = ?"
        params.append(yt_description)
    if transaction and transaction != "ALL":
        query += " AND T.TransName = ?"
        params.append(transaction)
    if modified_by and modified_by != "ALL":
        query += " AND U.Name = ?"
        params.append(modified_by)

    with get_cursor() as cursor:
        _sync_new_tables(cursor)
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [
        TableListRow(
            table_id=r["TableID"],
            table_name=r["TableName"],
            sequence=r["Sequence"] or 0,
            script=r["Script"] or "",
            description=r["Description"] or "",
            trans_name=r["TransName"],
            is_track_table=bool(r["IsTrackTable"]),
            purpose_of_table=r["PurposeOfTable"] or "",
            comments=r["Comments"] or "",
            primary_key_info=r["PrimaryKeyInfo"] or "",
            modify_date=r["ModifyDate"].isoformat() if r["ModifyDate"] else None,
            questionable=bool(r["Questionable"]),
        )
        for r in rows
    ]


@router.put("/table-list/{table_id}")
def update_table_list_row(table_id: int, body: TableListRow) -> dict[str, bool]:
    with get_cursor() as cursor:
        trans_type = None
        if body.trans_name:
            cursor.execute("SELECT TransType FROM TransTypemaster WHERE TransName = ?", body.trans_name)
            row = cursor.fetchone()
            trans_type = row[0] if row else None

        cursor.execute(
            """
            UPDATE Retailware_TableList
            SET Sequence = ?, Script = ?, TransactionID = ?, IsTrackTable = ?,
                PurposeOfTable = ?, Comments = ?, PrimaryKeyInfo = ?, Questionable = ?
            WHERE TableID = ?
            """,
            body.sequence,
            body.script,
            trans_type,
            body.is_track_table,
            body.purpose_of_table,
            body.comments,
            body.primary_key_info,
            body.questionable,
            table_id,
        )
    return {"success": True}
