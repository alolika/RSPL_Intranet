"""Simplified rebuild of Section_General/schedule.aspx.vb backed by the real
Schedular_Proc_Insert_UpdateTaskData / Schedular_Proc_Insert_UpdateAppointment
procs and RSPL_EmployeeSchedule table. The source's dynamic GridView-column
generation/rowspan-merge mechanics are ASP.NET WebForms plumbing (not a
business rule) and were already dropped in favor of a plain employee x day
grid at the Angular layer — see schedule-data.service.ts's doc comment. This
router queries RSPL_EmployeeSchedule directly with a normal SELECT instead of
calling Schedular_Proc_GetTaskData, whose output is a dynamically pivoted
grid shaped for that old rendering, not the flat per-task row list the
Angular model now uses.
"""

from datetime import date, datetime, time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/general/schedule", tags=["general-schedule"])


class LookupOption(BaseModel):
    label: str
    value: int


class ScheduleTask(BaseModel):
    task_sr_no: int
    user_id: int
    user_name: str
    day: int
    is_appointment: bool
    title: str
    from_time: str | None
    to_time: str | None
    description: str
    color: str
    is_private: bool
    entity_type_id: int | None
    entity_name: str
    guest_emails: list[str]
    reminder_channels: list[int]


class SaveTaskRequest(BaseModel):
    task: ScheduleTask
    year: int
    month: int


class ScheduleComment(BaseModel):
    user_name: str
    comment_time: str
    comment_text: str


class AddCommentRequest(BaseModel):
    task_sr_no: int
    task_user_id: int
    task_date: date
    text: str


# NOTE: no confirmed lookup table found for these in the time available —
# kept as the same static list the frontend previously hardcoded. The values
# double as @ActionID for Schedular_Proc_EmployeeTaskRemainderRelation.
REMINDER_CHANNELS = [
    LookupOption(label="SMS", value=1),
    LookupOption(label="Email", value=2),
    LookupOption(label="Prompt", value=3),
    LookupOption(label="GTalk", value=4),
    LookupOption(label="Whats App", value=5),
]

_ENTITY_TABLES = {
    1: ("UserMaster", "UserID", "Name"),
    2: ("SupplierMaster", "SupplierId", "Name"),
    3: ("CustomerMaster", "CustId", "Displayname"),
    4: ("BankMaster", "BankId", "Name"),
    5: ("AccountMaster", "AccountID", "Name"),
    6: ("LedgerGroupMaster", "LedgerGroupId", "Name"),
    7: ("Salesman", "SalesmanID", "Name"),
}


@router.get("/employees", response_model=list[LookupOption])
def get_employees() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT UserID, Name FROM UserMaster WHERE Enabled = 1 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]


@router.get("/entity-types", response_model=list[LookupOption])
def get_entity_types() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT EntityTypeId, Name FROM EntityTypeMaster WHERE EntityTypeId > 0 ORDER BY Name")
        return [LookupOption(label=r["Name"], value=r["EntityTypeId"]) for r in rows_to_dicts(cursor)]


@router.get("/reminder-channels", response_model=list[LookupOption])
def get_reminder_channels() -> list[LookupOption]:
    return REMINDER_CHANNELS


def _entity_name(cursor, entity_type_id: int | None, entity_id: int | None) -> str:
    if not entity_type_id or not entity_id or entity_type_id not in _ENTITY_TABLES:
        return ""
    table, key_col, name_col = _ENTITY_TABLES[entity_type_id]
    cursor.execute(f"SELECT {name_col} FROM {table} WHERE {key_col} = ?", entity_id)
    row = cursor.fetchone()
    return (row[0] or "") if row else ""


# save_task() writes here via Schedular_Proc_EmployeeTaskRemainderRelation, but nothing
# ever read it back — get_month_tasks() hardcoded reminder_channels=[] on every row, so an
# edit dialog could never show which channels were already selected for a task. Action_id=0
# is the proc's own "no reminders selected" sentinel row (inserted whenever the channel list
# is empty, per its `IF @ActionID=0 BEGIN DELETE ... END` clear-then-mark logic) — excluded
# here since it isn't a real channel like the REMINDER_CHANNELS values (1-5).
def _reminder_channels(cursor, task_sr_no: int, user_id: int) -> list[int]:
    if not task_sr_no:
        return []
    cursor.execute(
        "SELECT DISTINCT Action_id FROM RSPL_Employee_TaskReminderRelation WHERE Task_id = ? AND Userid = ? AND Action_id > 0",
        task_sr_no,
        user_id,
    )
    return [r[0] for r in cursor.fetchall()]


@router.get("/tasks", response_model=list[ScheduleTask])
def get_month_tasks(year: int, month: int) -> list[ScheduleTask]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT ES.TaskSrNo, ES.UserID, U.Name AS UserName, D.DayNumberOfMonth,
                   ES.IsAppointment, ES.AppointmentTitle, ES.FromTime, ES.ToTime,
                   ES.TaskDescription, ES.TaskColor, ES.IsPrivate, ES.EntityTypeID,
                   ES.EntityID, ES.GuestEmailID
            FROM RSPL_EmployeeSchedule ES
            INNER JOIN UserMaster U ON ES.UserID = U.UserID
            INNER JOIN Dim_Time D ON ES.TimeID = D.TimeID
            WHERE D.MonthNumberOfYear = ? AND D.CalendarYear = ?
            ORDER BY D.DayNumberOfMonth, U.Name
            """,
            month,
            str(year),
        )
        rows = rows_to_dicts(cursor)

        tasks = []
        for r in rows:
            entity_name = _entity_name(cursor, r["EntityTypeID"], r["EntityID"])
            guest_emails = [e.strip() for e in (r["GuestEmailID"] or "").split(",") if e.strip()]
            tasks.append(
                ScheduleTask(
                    task_sr_no=r["TaskSrNo"],
                    user_id=r["UserID"],
                    user_name=r["UserName"] or "",
                    day=r["DayNumberOfMonth"],
                    is_appointment=bool(r["IsAppointment"]),
                    title=r["AppointmentTitle"] or "",
                    from_time=r["FromTime"].isoformat() if r["FromTime"] else None,
                    to_time=r["ToTime"].isoformat() if r["ToTime"] else None,
                    description=r["TaskDescription"] or "",
                    color=r["TaskColor"] or "",
                    is_private=bool(r["IsPrivate"]),
                    entity_type_id=r["EntityTypeID"],
                    entity_name=entity_name,
                    guest_emails=guest_emails,
                    reminder_channels=_reminder_channels(cursor, r["TaskSrNo"], r["UserID"]),
                )
            )
    return tasks


@router.post("/tasks")
def save_task(body: SaveTaskRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    task = body.task
    task_date = date(body.year, body.month, task.day)
    now = datetime.now()

    from_time = datetime.combine(task_date, _parse_time(task.from_time)) if task.from_time else None
    to_time = datetime.combine(task_date, _parse_time(task.to_time)) if task.to_time else None
    # Frontend already blocks submitting without both times, but never
    # checked their order — nothing stopped saving a task "From 5 PM To 9
    # AM". Re-validated here since the frontend can't be trusted to be the
    # only caller of this endpoint.
    if from_time and to_time and from_time >= to_time:
        return {"success": False}
    action = "UPDATE" if task.task_sr_no else "INSERT"

    with get_cursor() as cursor:
        # CRITICAL, confirmed live: Dim_Time (the date-dimension table both
        # save procs use to resolve a date into a TimeID) only has rows from
        # 01-Apr-2004 through 15-Apr-2015 — nothing for the last decade-plus,
        # including "today" under any real clock. Saving a task for any
        # current/recent date previously crashed with a raw 500
        # (IntegrityError: NULL into TimeID) instead of a usable message,
        # since the proc's own internal TimeID lookup just silently returns
        # NULL for a date outside that range and the INSERT then fails.
        # Checking up front so the caller gets a clear signal instead of an
        # opaque SQL error. Extending Dim_Time itself is a data/infra change
        # outside this API's scope — flagged separately, not done here.
        cursor.execute("SELECT 1 FROM Dim_Time WHERE TransactionDate = ?", task_date)
        if cursor.fetchone() is None:
            return {"success": False, "dateNotSupported": True}

        # RSPL_EmployeeSchedule.EntityTypeID is NOT NULL, but Entity Type is
        # an optional field on the form (no selection = task.entity_type_id
        # is None) — every save without one previously crashed with a raw
        # 500 (IntegrityError: NULL into EntityTypeID). EntityTypeId=0
        # ("Other") is the schema's own established sentinel for exactly
        # this case — confirmed live it's already what 805 of the 807
        # existing rows use, and get_entity_types() deliberately excludes it
        # from the picker (`WHERE EntityTypeId > 0`) since users aren't
        # meant to select it explicitly.
        entity_type_id = task.entity_type_id or 0

        if task.is_appointment:
            cursor.execute(
                """
                EXEC Schedular_Proc_Insert_UpdateAppointment
                    @TaskDate=?, @userId=?, @FromTime=?, @Totime=?, @TaskDescription=?,
                    @MuserId=?, @MDate=?, @IsPrivate=?, @TaskColor=?, @CreationUserID=?,
                    @GuestEmailId=?, @AppointmentTitel=?, @Action=?, @tasrno1=?,
                    @EntityType=?, @Entity=?
                """,
                task_date,
                task.user_id,
                from_time,
                to_time,
                task.description,
                current_user.user_id,
                task_date,
                task.is_private,
                task.color,
                current_user.user_id,
                ",".join(task.guest_emails),
                task.title,
                action,
                task.task_sr_no,
                entity_type_id,
                entity_type_id,
            )
        else:
            cursor.execute(
                """
                EXEC Schedular_Proc_Insert_UpdateTaskData
                    @TaskDate=?, @userId=?, @FromTime=?, @Totime=?, @TaskDescription=?,
                    @MuserId=?, @MDate=?, @IsPrivate=?, @TaskColor=?, @CreationUserID=?,
                    @Action=?, @tasrno1=?, @appointmenttitle=?, @EntityType=?, @Entity=?
                """,
                task_date,
                task.user_id,
                from_time,
                to_time,
                task.description,
                current_user.user_id,
                task_date,
                task.is_private,
                task.color,
                current_user.user_id,
                action,
                task.task_sr_no,
                task.title,
                entity_type_id,
                entity_type_id,
            )

        if task.task_sr_no:
            # Was previously append-only: looping the reminder-relation proc
            # once per selected channel only ever INSERTs rows — confirmed
            # via the proc's own body that it only deletes existing rows
            # when @ActionID=0, which this loop never passes for a non-empty
            # selection. That meant unchecking a reminder channel during an
            # edit never actually removed it — the saved set only ever grew,
            # never shrank, across repeated edits. Explicitly clear this
            # task's existing reminder rows first, so the saved set always
            # matches exactly what's checked.
            #
            # Task_ID is NOT globally unique — confirmed live it's a
            # per-user-per-day counter (this project's schedule-data.service.ts
            # doc comment already flagged this for the comments endpoints;
            # same table, same caveat applies here). Task_ID=1/UserId=25
            # alone already spans three unrelated real dates in this data.
            # Must scope by TaskDate too, matching the proc's own delete
            # logic exactly (`WHERE USERID=... AND TaskDate=... AND
            # Task_ID=...`) — omitting it would silently wipe reminder rows
            # belonging to a different day's task that happens to reuse the
            # same Task_ID/UserId pair.
            cursor.execute(
                "DELETE FROM RSPL_Employee_TaskReminderRelation WHERE Task_ID = ? AND UserId = ? AND TaskDate = ?",
                task.task_sr_no,
                task.user_id,
                task_date,
            )

        for channel_id in task.reminder_channels or [0]:
            cursor.execute(
                "EXEC Schedular_Proc_EmployeeTaskRemainderRelation ?, ?, ?, ?",
                task_date,
                task.user_id,
                channel_id,
                task.description,
            )

    return {"success": True}


@router.get("/comments", response_model=list[ScheduleComment])
def get_comments(task_sr_no: int, user_id: int, task_date: date) -> list[ScheduleComment]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT U.Name, C.CDate, C.CTime, C.CommentDesc
            FROM RSPL_Employee_TaskComments2 C
            INNER JOIN Dim_Time D ON C.TimeId = D.TimeID
            INNER JOIN UserMaster U ON C.CUserid = U.UserID
            WHERE C.TaskSrNo = ? AND C.UserId = ? AND D.TransactionDate = ?
            ORDER BY C.CommentNo
            """,
            task_sr_no,
            user_id,
            task_date,
        )
        rows = rows_to_dicts(cursor)

    return [
        ScheduleComment(
            user_name=r["Name"] or "",
            comment_time=r["CTime"].isoformat() if r["CTime"] else "",
            comment_text=r["CommentDesc"] or "",
        )
        for r in rows
    ]


@router.post("/comments")
def add_comment(body: AddCommentRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC Schedule_Rspl_InsertTaskComment ?, ?, ?, ?, ?",
            body.task_date,
            body.task_sr_no,
            body.task_user_id,
            body.text,
            current_user.user_id,
        )
    return {"success": True}


@router.get("/guest-emails")
def search_guest_emails(term: str) -> list[str]:
    with get_cursor() as cursor:
        cursor.execute("EXEC Proc_GetContactList1 ?, ?", "Name", term)
        return [r["EmailID"] for r in rows_to_dicts(cursor) if r.get("EmailID")]


def _parse_time(value: str) -> time:
    return datetime.fromisoformat(value).time()
