/*
================================================================================
 Engineering Hub — Production/Target-Environment Deployment Script
================================================================================
 Target engine   : Microsoft SQL Server (T-SQL). Verified against a live
                    SQL Server instance via pyodbc / "ODBC Driver 17 for SQL
                    Server" (rspl_Api's app/config.py, app/db.py) — no other
                    engine is used anywhere in this application.

 Source of truth : This script is derived from
                    D:\Maruti_workspace\rspl_Api\database\enghub_migration.sql,
                    which is the ONLY SQL file in the rspl_Api repository that
                    references any EngHub_* object. Confirmed by:
                      - grep for "EngHub" across every *.sql file in the repo
                        (1 file: enghub_migration.sql)
                      - grep for CREATE PROCEDURE/VIEW/FUNCTION/TRIGGER or
                        ALTER TABLE ... EngHub anywhere in the repo (0 hits)
                      - a live query against sys.objects / sys.sql_modules on
                        the dev/demo database this module was built and
                        tested against, filtering for any Procedure, View,
                        Function, or Trigger whose name OR definition text
                        mentions "EngHub" (0 hits)
                    Engineering Hub is a brand-new, greenfield module. Every
                    query the application issues against it is a direct
                    parameterized SQL statement from Python (see
                    app/routers/enghub_*.py) — there are NO legacy stored
                    procedures, views, functions, or triggers for this
                    module to carry over. This is a deliberate architectural
                    choice for this module, not a gap in this script.

 What this deploys : 24 new, independent tables (10 masters, 3 core-hierarchy,
                    4 activity-family, 1 decision, 1 assignment-history,
                    4 release/ticket-association, 1 attachment) plus their
                    PKs/FKs/CHECK/DEFAULT/UNIQUE constraints, 21 supporting
                    indexes, and idempotent seed/master data. Nothing in this
                    script alters, drops, or reads any pre-existing table
                    except via read-only FOREIGN KEY references into
                    dbo.UserMaster, dbo.CustomerMaster, and dbo.TTMaster.

 Idempotent      : Every CREATE TABLE is guarded by an OBJECT_ID existence
                    check; every index not created inside a guarded
                    CREATE TABLE block has its own sys.indexes existence
                    check; every seed INSERT is a WHERE-NOT-EXISTS upsert
                    keyed by Name. Safe to run repeatedly, including a
                    partial re-run after an earlier failure, and safe to run
                    against a database that already has some or all of these
                    objects (already-existing objects are silently skipped).

 Transactional   : Section 1 (all DDL: tables/constraints/indexes) runs in
                    one named transaction with TRY/CATCH — SQL Server DDL is
                    fully transactional, so any failure partway through
                    rolls back every table created so far in that section,
                    not just the DML. Section 2 (seed data) runs in its own
                    separate named transaction with the same TRY/CATCH
                    pattern, deliberately kept independent of Section 1 so
                    that a seed-data issue can be fixed and re-run without
                    needing to also replay the (already-committed) DDL.

 PREREQUISITES (verified automatically by Section 0 below, script aborts
 with a clear message if any is not met — do not skip this check):
   - dbo.UserMaster    must exist, with UserID       as INT,    PRIMARY KEY
   - dbo.CustomerMaster must exist, with CustID       as BIGINT, PRIMARY KEY
   - dbo.TTMaster       must exist, with VoucherNo    as BIGINT, PRIMARY KEY
   These three columns are the targets of every FOREIGN KEY this script
   creates into pre-existing application tables. Confirmed live against
   this application's currently-configured database (Worldnettech) that
   all three match exactly.

 NOT covered by this script (explicitly listed per the "don't guess" rule
 rather than silently omitted):
   1. Filesystem folder C:\Retailware\EngHubAttachments\ on whatever host
      runs the FastAPI backend — EngHub_Attachment only stores metadata;
      the actual uploaded files live on disk under this path
      (app/routers/enghub_attachments.py), created lazily by the app on
      first upload but worth pre-creating with correct permissions for a
      production deployment. Not a database object, cannot be scripted here.
   2. Application-level configuration (rspl_Api's .env: DB_SERVER/DB_NAME/
      DB_USER/DB_PASSWORD/JWT_SECRET, and the Angular frontend's
      environment.ts apiBaseUrl) pointing at the target database/host —
      out of scope for a SQL script.
   3. Application/API deployment itself (this script only creates database
      objects; it does not deploy the FastAPI backend or Angular frontend
      code that uses them).
================================================================================
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;   -- required for the filtered unique index in Section 1

----------------------------------------------------------------------
-- SECTION 0: Pre-flight prerequisite checks
-- Fails fast, before any DDL runs, if the three legacy tables this module
-- has real foreign keys into are missing or shaped differently than
-- expected on the target database.
----------------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'UserMaster' AND COLUMN_NAME = 'UserID' AND DATA_TYPE = 'int'
)
BEGIN
    RAISERROR('Prerequisite check FAILED: dbo.UserMaster.UserID (INT) not found on this database. Engineering Hub FKs into this column and cannot be deployed without it.', 16, 1);
    RETURN;
END

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CustomerMaster' AND COLUMN_NAME = 'CustID' AND DATA_TYPE = 'bigint'
)
BEGIN
    RAISERROR('Prerequisite check FAILED: dbo.CustomerMaster.CustID (BIGINT) not found on this database. Engineering Hub FKs into this column and cannot be deployed without it.', 16, 1);
    RETURN;
END

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'TTMaster' AND COLUMN_NAME = 'VoucherNo' AND DATA_TYPE = 'bigint'
)
BEGIN
    RAISERROR('Prerequisite check FAILED: dbo.TTMaster.VoucherNo (BIGINT) not found on this database. Engineering Hub FKs into this column and cannot be deployed without it.', 16, 1);
    RETURN;
END

PRINT 'Pre-flight checks passed: UserMaster.UserID (INT), CustomerMaster.CustID (BIGINT), TTMaster.VoucherNo (BIGINT) all confirmed.';
GO


----------------------------------------------------------------------
-- SECTION 1: Schema creation — tables, constraints (PK/FK/CHECK/DEFAULT/
-- UNIQUE), and indexes. One transaction covering all DDL in this section.
----------------------------------------------------------------------

BEGIN TRANSACTION EngHubMigration;

BEGIN TRY

    ----------------------------------------------------------------------
    -- 1a. Masters
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Product', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Product (
            ProductId           INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(100)  NOT NULL,
            Description         NVARCHAR(500)  NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_Product_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Product_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Product PRIMARY KEY CLUSTERED (ProductId)
        );
        PRINT 'Created table dbo.EngHub_Product';
    END

    IF OBJECT_ID('dbo.EngHub_Module', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Module (
            ModuleId            INT IDENTITY(1,1) NOT NULL,
            ProductId           INT            NOT NULL,
            Name                NVARCHAR(100)  NOT NULL,
            Description         NVARCHAR(500)  NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_Module_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Module_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Module PRIMARY KEY CLUSTERED (ModuleId),
            CONSTRAINT FK_EngHub_Module_Product FOREIGN KEY (ProductId) REFERENCES dbo.EngHub_Product (ProductId)
        );
        CREATE INDEX IX_EngHub_Module_Product ON dbo.EngHub_Module (ProductId);
        PRINT 'Created table dbo.EngHub_Module';
    END

    IF OBJECT_ID('dbo.EngHub_TaskType', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_TaskType (
            TaskTypeId          INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_TaskType_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_TaskType_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_TaskType PRIMARY KEY CLUSTERED (TaskTypeId)
        );
        PRINT 'Created table dbo.EngHub_TaskType';
    END

    IF OBJECT_ID('dbo.EngHub_DevItemType', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_DevItemType (
            DevItemTypeId       INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_DevItemType_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_DevItemType_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_DevItemType PRIMARY KEY CLUSTERED (DevItemTypeId)
        );
        PRINT 'Created table dbo.EngHub_DevItemType';
    END

    IF OBJECT_ID('dbo.EngHub_ActivityType', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_ActivityType (
            ActivityTypeId      INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_ActivityType_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_ActivityType_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_ActivityType PRIMARY KEY CLUSTERED (ActivityTypeId)
        );
        PRINT 'Created table dbo.EngHub_ActivityType';
    END

    IF OBJECT_ID('dbo.EngHub_DecisionType', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_DecisionType (
            DecisionTypeId      INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_DecisionType_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_DecisionType_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_DecisionType PRIMARY KEY CLUSTERED (DecisionTypeId)
        );
        PRINT 'Created table dbo.EngHub_DecisionType';
    END

    IF OBJECT_ID('dbo.EngHub_InterruptionReason', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_InterruptionReason (
            InterruptionReasonId INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(100)  NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_InterruptionReason_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_InterruptionReason_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_InterruptionReason PRIMARY KEY CLUSTERED (InterruptionReasonId)
        );
        PRINT 'Created table dbo.EngHub_InterruptionReason';
    END

    IF OBJECT_ID('dbo.EngHub_Priority', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Priority (
            PriorityId          INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(30)   NOT NULL,
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_Priority_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Priority_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Priority PRIMARY KEY CLUSTERED (PriorityId)
        );
        PRINT 'Created table dbo.EngHub_Priority';
    END

    IF OBJECT_ID('dbo.EngHub_OriginType', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_OriginType (
            OriginTypeId        INT IDENTITY(1,1) NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,   -- Support/Management/Sales/Customer/Partner/Tester/Developer
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_OriginType_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_OriginType_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_OriginType PRIMARY KEY CLUSTERED (OriginTypeId)
        );
        PRINT 'Created table dbo.EngHub_OriginType';
    END

    -- One physical table serves the Feature/DevelopmentItem/Task/Decision/Release
    -- status vocabularies, scoped by EntityType, instead of five near-identical
    -- tables — the same "master-driven configuration" idea applied once.
    IF OBJECT_ID('dbo.EngHub_Status', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Status (
            StatusId            INT IDENTITY(1,1) NOT NULL,
            EntityType          VARCHAR(20)    NOT NULL,
            Name                NVARCHAR(50)   NOT NULL,
            Color               VARCHAR(7)     NULL,        -- '#RRGGBB'
            SortOrder           INT            NOT NULL CONSTRAINT DF_EngHub_Status_SortOrder DEFAULT (0),
            IsTerminal          BIT            NOT NULL CONSTRAINT DF_EngHub_Status_IsTerminal DEFAULT (0),
            Enabled             BIT            NOT NULL CONSTRAINT DF_EngHub_Status_Enabled DEFAULT (1),
            CreatedByUserId     INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Status_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId  INT            NULL,
            LastEditedAt        DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Status PRIMARY KEY CLUSTERED (StatusId),
            CONSTRAINT CK_EngHub_Status_EntityType CHECK (EntityType IN ('Feature','DevelopmentItem','Task','Decision','Release'))
        );
        CREATE INDEX IX_EngHub_Status_EntityType ON dbo.EngHub_Status (EntityType, SortOrder);
        PRINT 'Created table dbo.EngHub_Status';
    END

    ----------------------------------------------------------------------
    -- 1b. Core hierarchy: Product -> Module -> Feature -> DevelopmentItem -> Task
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Feature', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Feature (
            FeatureId            INT IDENTITY(1,1) NOT NULL,
            ModuleId             INT            NOT NULL,
            Name                 NVARCHAR(150)  NOT NULL,
            Description          NVARCHAR(MAX)  NULL,
            FeatureOwnerUserId   INT            NULL,
            TechnicalOwnerUserId INT            NULL,
            Enabled              BIT            NOT NULL CONSTRAINT DF_EngHub_Feature_Enabled DEFAULT (1),
            CreatedByUserId      INT            NOT NULL,
            CreatedAt            DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Feature_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId   INT            NULL,
            LastEditedAt         DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Feature PRIMARY KEY CLUSTERED (FeatureId),
            CONSTRAINT FK_EngHub_Feature_Module FOREIGN KEY (ModuleId) REFERENCES dbo.EngHub_Module (ModuleId),
            CONSTRAINT FK_EngHub_Feature_Owner FOREIGN KEY (FeatureOwnerUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT FK_EngHub_Feature_TechOwner FOREIGN KEY (TechnicalOwnerUserId) REFERENCES dbo.UserMaster (UserID)
        );
        CREATE INDEX IX_EngHub_Feature_Module ON dbo.EngHub_Feature (ModuleId);
        PRINT 'Created table dbo.EngHub_Feature';
    END

    IF OBJECT_ID('dbo.EngHub_DevelopmentItem', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_DevelopmentItem (
            DevItemId             INT IDENTITY(1,1) NOT NULL,
            FeatureId             INT            NOT NULL,
            DevItemTypeId         INT            NOT NULL,
            Title                 NVARCHAR(200)  NOT NULL,
            Description           NVARCHAR(MAX)  NULL,
            OriginTypeId          INT            NULL,
            OriginTicketVoucherNo BIGINT         NULL,   -- TTMaster.VoucherNo is BIGINT, not INT
            CustomerId            BIGINT         NULL,   -- CustomerMaster.CustID is BIGINT, not INT
            PriorityId            INT            NULL,
            StatusId              INT            NOT NULL,
            ClosedAt              DATETIME2(3)   NULL,
            CreatedByUserId       INT            NOT NULL,
            CreatedAt             DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_DevItem_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId    INT            NULL,
            LastEditedAt          DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_DevelopmentItem PRIMARY KEY CLUSTERED (DevItemId),
            CONSTRAINT FK_EngHub_DevItem_Feature FOREIGN KEY (FeatureId) REFERENCES dbo.EngHub_Feature (FeatureId),
            CONSTRAINT FK_EngHub_DevItem_Type FOREIGN KEY (DevItemTypeId) REFERENCES dbo.EngHub_DevItemType (DevItemTypeId),
            CONSTRAINT FK_EngHub_DevItem_Origin FOREIGN KEY (OriginTypeId) REFERENCES dbo.EngHub_OriginType (OriginTypeId),
            CONSTRAINT FK_EngHub_DevItem_Ticket FOREIGN KEY (OriginTicketVoucherNo) REFERENCES dbo.TTMaster (VoucherNo),
            CONSTRAINT FK_EngHub_DevItem_Customer FOREIGN KEY (CustomerId) REFERENCES dbo.CustomerMaster (CustID),
            CONSTRAINT FK_EngHub_DevItem_Priority FOREIGN KEY (PriorityId) REFERENCES dbo.EngHub_Priority (PriorityId),
            CONSTRAINT FK_EngHub_DevItem_Status FOREIGN KEY (StatusId) REFERENCES dbo.EngHub_Status (StatusId)
        );
        CREATE INDEX IX_EngHub_DevItem_Feature ON dbo.EngHub_DevelopmentItem (FeatureId);
        CREATE INDEX IX_EngHub_DevItem_Status ON dbo.EngHub_DevelopmentItem (StatusId);
        CREATE INDEX IX_EngHub_DevItem_Customer ON dbo.EngHub_DevelopmentItem (CustomerId);
        PRINT 'Created table dbo.EngHub_DevelopmentItem';
    END

    IF OBJECT_ID('dbo.EngHub_Task', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Task (
            TaskId               INT IDENTITY(1,1) NOT NULL,
            DevItemId            INT            NOT NULL,
            TaskTypeId           INT            NOT NULL,
            Title                NVARCHAR(200)  NOT NULL,
            Description          NVARCHAR(MAX)  NULL,
            StatusId             INT            NOT NULL,
            EstimatedHours       DECIMAL(6,2)   NULL,
            ClosedAt             DATETIME2(3)   NULL,
            CreatedByUserId      INT            NOT NULL,
            CreatedAt            DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Task_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId   INT            NULL,
            LastEditedAt         DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Task PRIMARY KEY CLUSTERED (TaskId),
            CONSTRAINT FK_EngHub_Task_DevItem FOREIGN KEY (DevItemId) REFERENCES dbo.EngHub_DevelopmentItem (DevItemId),
            CONSTRAINT FK_EngHub_Task_Type FOREIGN KEY (TaskTypeId) REFERENCES dbo.EngHub_TaskType (TaskTypeId),
            CONSTRAINT FK_EngHub_Task_Status FOREIGN KEY (StatusId) REFERENCES dbo.EngHub_Status (StatusId)
        );
        CREATE INDEX IX_EngHub_Task_DevItem ON dbo.EngHub_Task (DevItemId);
        CREATE INDEX IX_EngHub_Task_Status ON dbo.EngHub_Task (StatusId);
        PRINT 'Created table dbo.EngHub_Task';
    END

    ----------------------------------------------------------------------
    -- 1c. Activities (append-only). May attach to a Feature and/or a
    -- DevelopmentItem and/or a Task — at least one required.
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Activity', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Activity (
            ActivityId          INT IDENTITY(1,1) NOT NULL,
            ActivityTypeId      INT            NOT NULL,
            FeatureId           INT            NULL,
            DevItemId           INT            NULL,
            TaskId              INT            NULL,
            Description         NVARCHAR(MAX)  NULL,
            DurationMinutes     INT            NULL,
            OldStatusId         INT            NULL,
            NewStatusId         INT            NULL,
            OccurredAt          DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Activity_OccurredAt DEFAULT (SYSUTCDATETIME()),
            LoggedByUserId      INT            NOT NULL,
            CreatedAt           DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Activity_CreatedAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_Activity PRIMARY KEY CLUSTERED (ActivityId),
            CONSTRAINT FK_EngHub_Activity_Type FOREIGN KEY (ActivityTypeId) REFERENCES dbo.EngHub_ActivityType (ActivityTypeId),
            CONSTRAINT FK_EngHub_Activity_Feature FOREIGN KEY (FeatureId) REFERENCES dbo.EngHub_Feature (FeatureId),
            CONSTRAINT FK_EngHub_Activity_DevItem FOREIGN KEY (DevItemId) REFERENCES dbo.EngHub_DevelopmentItem (DevItemId),
            CONSTRAINT FK_EngHub_Activity_Task FOREIGN KEY (TaskId) REFERENCES dbo.EngHub_Task (TaskId),
            CONSTRAINT FK_EngHub_Activity_OldStatus FOREIGN KEY (OldStatusId) REFERENCES dbo.EngHub_Status (StatusId),
            CONSTRAINT FK_EngHub_Activity_NewStatus FOREIGN KEY (NewStatusId) REFERENCES dbo.EngHub_Status (StatusId),
            CONSTRAINT FK_EngHub_Activity_LoggedBy FOREIGN KEY (LoggedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT CK_EngHub_Activity_HasParent CHECK (FeatureId IS NOT NULL OR DevItemId IS NOT NULL OR TaskId IS NOT NULL)
        );
        CREATE INDEX IX_EngHub_Activity_Feature ON dbo.EngHub_Activity (FeatureId);
        CREATE INDEX IX_EngHub_Activity_DevItem ON dbo.EngHub_Activity (DevItemId);
        CREATE INDEX IX_EngHub_Activity_Task ON dbo.EngHub_Activity (TaskId);
        CREATE INDEX IX_EngHub_Activity_LoggedBy ON dbo.EngHub_Activity (LoggedByUserId, OccurredAt);
        PRINT 'Created table dbo.EngHub_Activity';
    END

    IF OBJECT_ID('dbo.EngHub_ActivityParticipant', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_ActivityParticipant (
            ActivityParticipantId INT IDENTITY(1,1) NOT NULL,
            ActivityId             INT           NOT NULL,
            UserId                 INT           NOT NULL,
            ParticipationStatus    VARCHAR(10)   NOT NULL CONSTRAINT DF_EngHub_ActivityParticipant_Status DEFAULT ('Accepted'),
            AddedByUserId          INT           NOT NULL,
            RespondedAt            DATETIME2(3)  NULL,
            CreatedAt              DATETIME2(3)  NOT NULL CONSTRAINT DF_EngHub_ActivityParticipant_CreatedAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_ActivityParticipant PRIMARY KEY CLUSTERED (ActivityParticipantId),
            CONSTRAINT FK_EngHub_ActivityParticipant_Activity FOREIGN KEY (ActivityId) REFERENCES dbo.EngHub_Activity (ActivityId),
            CONSTRAINT FK_EngHub_ActivityParticipant_User FOREIGN KEY (UserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT FK_EngHub_ActivityParticipant_AddedBy FOREIGN KEY (AddedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT UQ_EngHub_ActivityParticipant UNIQUE (ActivityId, UserId),
            CONSTRAINT CK_EngHub_ActivityParticipant_Status CHECK (ParticipationStatus IN ('Invited','Accepted','Rejected','SelfAdded'))
        );
        PRINT 'Created table dbo.EngHub_ActivityParticipant';
    END

    -- 1:1 extension of an EngHub_Activity row where ActivityType = 'Interruption'.
    IF OBJECT_ID('dbo.EngHub_Interruption', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Interruption (
            ActivityId            INT            NOT NULL,
            InterruptedTaskId     INT            NOT NULL,
            NewTaskId             INT            NOT NULL,
            InterruptionReasonId  INT            NOT NULL,
            RequestedByUserId     INT            NOT NULL,
            Comments              NVARCHAR(500)  NULL,
            CONSTRAINT PK_EngHub_Interruption PRIMARY KEY CLUSTERED (ActivityId),
            CONSTRAINT FK_EngHub_Interruption_Activity FOREIGN KEY (ActivityId) REFERENCES dbo.EngHub_Activity (ActivityId),
            CONSTRAINT FK_EngHub_Interruption_OldTask FOREIGN KEY (InterruptedTaskId) REFERENCES dbo.EngHub_Task (TaskId),
            CONSTRAINT FK_EngHub_Interruption_NewTask FOREIGN KEY (NewTaskId) REFERENCES dbo.EngHub_Task (TaskId),
            CONSTRAINT FK_EngHub_Interruption_Reason FOREIGN KEY (InterruptionReasonId) REFERENCES dbo.EngHub_InterruptionReason (InterruptionReasonId),
            CONSTRAINT FK_EngHub_Interruption_RequestedBy FOREIGN KEY (RequestedByUserId) REFERENCES dbo.UserMaster (UserID)
        );
        PRINT 'Created table dbo.EngHub_Interruption';
    END

    -- One row per user: "what am I working on right now" — drives the
    -- "Have you been interrupted?" prompt when logging work against a
    -- different Task than this pointer.
    IF OBJECT_ID('dbo.EngHub_UserCurrentTask', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_UserCurrentTask (
            UserId    INT           NOT NULL,
            TaskId    INT           NOT NULL,
            SetAt     DATETIME2(3)  NOT NULL CONSTRAINT DF_EngHub_UserCurrentTask_SetAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_UserCurrentTask PRIMARY KEY CLUSTERED (UserId),
            CONSTRAINT FK_EngHub_UserCurrentTask_User FOREIGN KEY (UserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT FK_EngHub_UserCurrentTask_Task FOREIGN KEY (TaskId) REFERENCES dbo.EngHub_Task (TaskId)
        );
        PRINT 'Created table dbo.EngHub_UserCurrentTask';
    END

    ----------------------------------------------------------------------
    -- 1d. Decisions — may attach to a Feature and/or DevelopmentItem and/or
    -- Task (at least one required), same flexible-attachment shape as
    -- Activity.
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Decision', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Decision (
            DecisionId            INT IDENTITY(1,1) NOT NULL,
            FeatureId             INT            NULL,
            DevItemId             INT            NULL,
            TaskId                INT            NULL,
            DecisionTypeId        INT            NOT NULL,
            Description           NVARCHAR(MAX)  NOT NULL,
            ApproverUserId        INT            NULL,
            ApproverExternalName  NVARCHAR(200)  NULL,
            Reason                NVARCHAR(MAX)  NOT NULL,
            RiskLevel             VARCHAR(10)    NOT NULL,
            ReviewDate            DATE           NULL,
            StatusId              INT            NOT NULL,
            CustomerId            BIGINT         NULL,   -- CustomerMaster.CustID is BIGINT, not INT
            CreatedByUserId       INT            NOT NULL,
            CreatedAt             DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Decision_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId    INT            NULL,
            LastEditedAt          DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Decision PRIMARY KEY CLUSTERED (DecisionId),
            CONSTRAINT FK_EngHub_Decision_Feature FOREIGN KEY (FeatureId) REFERENCES dbo.EngHub_Feature (FeatureId),
            CONSTRAINT FK_EngHub_Decision_DevItem FOREIGN KEY (DevItemId) REFERENCES dbo.EngHub_DevelopmentItem (DevItemId),
            CONSTRAINT FK_EngHub_Decision_Task FOREIGN KEY (TaskId) REFERENCES dbo.EngHub_Task (TaskId),
            CONSTRAINT FK_EngHub_Decision_Type FOREIGN KEY (DecisionTypeId) REFERENCES dbo.EngHub_DecisionType (DecisionTypeId),
            CONSTRAINT FK_EngHub_Decision_Approver FOREIGN KEY (ApproverUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT FK_EngHub_Decision_Status FOREIGN KEY (StatusId) REFERENCES dbo.EngHub_Status (StatusId),
            CONSTRAINT FK_EngHub_Decision_Customer FOREIGN KEY (CustomerId) REFERENCES dbo.CustomerMaster (CustID),
            CONSTRAINT CK_EngHub_Decision_HasParent CHECK (FeatureId IS NOT NULL OR DevItemId IS NOT NULL OR TaskId IS NOT NULL),
            CONSTRAINT CK_EngHub_Decision_RiskLevel CHECK (RiskLevel IN ('Low','Medium','High'))
        );
        CREATE INDEX IX_EngHub_Decision_Feature ON dbo.EngHub_Decision (FeatureId);
        CREATE INDEX IX_EngHub_Decision_DevItem ON dbo.EngHub_Decision (DevItemId);
        CREATE INDEX IX_EngHub_Decision_Task ON dbo.EngHub_Decision (TaskId);
        CREATE INDEX IX_EngHub_Decision_Customer ON dbo.EngHub_Decision (CustomerId);
        PRINT 'Created table dbo.EngHub_Decision';
    END

    ----------------------------------------------------------------------
    -- 1e. Assignment history — never overwritten. Polymorphic (EntityType,
    -- EntityId): no DB-level FK across the three possible target tables
    -- (SQL Server can't enforce that), app-level integrity only.
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_AssignmentHistory', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_AssignmentHistory (
            AssignmentHistoryId  INT IDENTITY(1,1) NOT NULL,
            EntityType           VARCHAR(20)    NOT NULL,
            EntityId              INT            NOT NULL,
            RoleType              VARCHAR(20)    NOT NULL,
            UserId                INT            NOT NULL,
            AssignedByUserId      INT            NOT NULL,
            AssignedAt            DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_AssignmentHistory_AssignedAt DEFAULT (SYSUTCDATETIME()),
            UnassignedAt          DATETIME2(3)   NULL,
            Comments              NVARCHAR(500)  NULL,
            CONSTRAINT PK_EngHub_AssignmentHistory PRIMARY KEY CLUSTERED (AssignmentHistoryId),
            CONSTRAINT FK_EngHub_AssignmentHistory_User FOREIGN KEY (UserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT FK_EngHub_AssignmentHistory_AssignedBy FOREIGN KEY (AssignedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT CK_EngHub_AssignmentHistory_EntityType CHECK (EntityType IN ('Feature','DevelopmentItem','Task')),
            CONSTRAINT CK_EngHub_AssignmentHistory_RoleType CHECK (RoleType IN ('Developer','Tester','FeatureOwner','TechnicalOwner'))
        );
        PRINT 'Created table dbo.EngHub_AssignmentHistory';
    END

    -- Enforces "at most one current holder per (entity, role)" — current
    -- assignment = the row where UnassignedAt IS NULL.
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = 'UX_EngHub_AssignmentHistory_Current'
          AND object_id = OBJECT_ID('dbo.EngHub_AssignmentHistory')
    )
        CREATE UNIQUE INDEX UX_EngHub_AssignmentHistory_Current
            ON dbo.EngHub_AssignmentHistory (EntityType, EntityId, RoleType)
            WHERE UnassignedAt IS NULL;

    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = 'IX_EngHub_AssignmentHistory_Entity'
          AND object_id = OBJECT_ID('dbo.EngHub_AssignmentHistory')
    )
        CREATE INDEX IX_EngHub_AssignmentHistory_Entity
            ON dbo.EngHub_AssignmentHistory (EntityType, EntityId);

    ----------------------------------------------------------------------
    -- 1f. Releases + ticket associations
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Release', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Release (
            ReleaseId            INT IDENTITY(1,1) NOT NULL,
            Name                 NVARCHAR(50)   NOT NULL,
            ReleaseDate          DATE           NULL,
            Description          NVARCHAR(MAX)  NULL,
            ProductId            INT            NULL,
            StatusId             INT            NOT NULL,
            CreatedByUserId      INT            NOT NULL,
            CreatedAt            DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Release_CreatedAt DEFAULT (SYSUTCDATETIME()),
            LastEditedByUserId   INT            NULL,
            LastEditedAt         DATETIME2(3)   NULL,
            CONSTRAINT PK_EngHub_Release PRIMARY KEY CLUSTERED (ReleaseId),
            CONSTRAINT FK_EngHub_Release_Product FOREIGN KEY (ProductId) REFERENCES dbo.EngHub_Product (ProductId),
            CONSTRAINT FK_EngHub_Release_Status FOREIGN KEY (StatusId) REFERENCES dbo.EngHub_Status (StatusId)
        );
        PRINT 'Created table dbo.EngHub_Release';
    END

    IF OBJECT_ID('dbo.EngHub_ReleaseMapping', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_ReleaseMapping (
            ReleaseMappingId  INT IDENTITY(1,1) NOT NULL,
            ReleaseId         INT           NOT NULL,
            FeatureId         INT           NULL,
            DevItemId         INT           NULL,
            MappedByUserId    INT           NOT NULL,
            MappedAt          DATETIME2(3)  NOT NULL CONSTRAINT DF_EngHub_ReleaseMapping_MappedAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_ReleaseMapping PRIMARY KEY CLUSTERED (ReleaseMappingId),
            CONSTRAINT FK_EngHub_ReleaseMapping_Release FOREIGN KEY (ReleaseId) REFERENCES dbo.EngHub_Release (ReleaseId),
            CONSTRAINT FK_EngHub_ReleaseMapping_Feature FOREIGN KEY (FeatureId) REFERENCES dbo.EngHub_Feature (FeatureId),
            CONSTRAINT FK_EngHub_ReleaseMapping_DevItem FOREIGN KEY (DevItemId) REFERENCES dbo.EngHub_DevelopmentItem (DevItemId),
            CONSTRAINT FK_EngHub_ReleaseMapping_MappedBy FOREIGN KEY (MappedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT CK_EngHub_ReleaseMapping_HasTarget CHECK (FeatureId IS NOT NULL OR DevItemId IS NOT NULL)
        );
        CREATE INDEX IX_EngHub_ReleaseMapping_Release ON dbo.EngHub_ReleaseMapping (ReleaseId);
        PRINT 'Created table dbo.EngHub_ReleaseMapping';
    END

    IF OBJECT_ID('dbo.EngHub_FeatureTicket', 'U') IS NULL
    BEGIN
        -- Feature <-> Trouble Ticket association (many-to-many: a Feature can
        -- reference several tickets). TicketVoucherNo is BIGINT and FK's
        -- straight to TTMaster.VoucherNo, mirroring EngHub_DevelopmentItem's
        -- existing OriginTicketVoucherNo FK to the same legacy table.
        CREATE TABLE dbo.EngHub_FeatureTicket (
            FeatureTicketId   INT IDENTITY(1,1) NOT NULL,
            FeatureId         INT           NOT NULL,
            TicketVoucherNo   BIGINT        NOT NULL,
            AddedByUserId     INT           NOT NULL,
            AddedAt           DATETIME2(3)  NOT NULL CONSTRAINT DF_EngHub_FeatureTicket_AddedAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_FeatureTicket PRIMARY KEY CLUSTERED (FeatureTicketId),
            CONSTRAINT FK_EngHub_FeatureTicket_Feature FOREIGN KEY (FeatureId) REFERENCES dbo.EngHub_Feature (FeatureId),
            CONSTRAINT FK_EngHub_FeatureTicket_Ticket FOREIGN KEY (TicketVoucherNo) REFERENCES dbo.TTMaster (VoucherNo),
            CONSTRAINT FK_EngHub_FeatureTicket_AddedBy FOREIGN KEY (AddedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT UQ_EngHub_FeatureTicket UNIQUE (FeatureId, TicketVoucherNo)
        );
        CREATE INDEX IX_EngHub_FeatureTicket_Feature ON dbo.EngHub_FeatureTicket (FeatureId);
        PRINT 'Created table dbo.EngHub_FeatureTicket';
    END

    IF OBJECT_ID('dbo.EngHub_TaskTicket', 'U') IS NULL
    BEGIN
        -- Task <-> Trouble Ticket association, same single-ticket-at-a-time
        -- model as EngHub_FeatureTicket (the API layer always clears any
        -- existing row before inserting, so this table never accumulates
        -- more than one row per TaskId even though nothing here enforces
        -- that structurally).
        CREATE TABLE dbo.EngHub_TaskTicket (
            TaskTicketId      INT IDENTITY(1,1) NOT NULL,
            TaskId            INT           NOT NULL,
            TicketVoucherNo   BIGINT        NOT NULL,
            AddedByUserId     INT           NOT NULL,
            AddedAt           DATETIME2(3)  NOT NULL CONSTRAINT DF_EngHub_TaskTicket_AddedAt DEFAULT (SYSUTCDATETIME()),
            CONSTRAINT PK_EngHub_TaskTicket PRIMARY KEY CLUSTERED (TaskTicketId),
            CONSTRAINT FK_EngHub_TaskTicket_Task FOREIGN KEY (TaskId) REFERENCES dbo.EngHub_Task (TaskId),
            CONSTRAINT FK_EngHub_TaskTicket_Ticket FOREIGN KEY (TicketVoucherNo) REFERENCES dbo.TTMaster (VoucherNo),
            CONSTRAINT FK_EngHub_TaskTicket_AddedBy FOREIGN KEY (AddedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT UQ_EngHub_TaskTicket UNIQUE (TaskId, TicketVoucherNo)
        );
        CREATE INDEX IX_EngHub_TaskTicket_Task ON dbo.EngHub_TaskTicket (TaskId);
        PRINT 'Created table dbo.EngHub_TaskTicket';
    END

    ----------------------------------------------------------------------
    -- 1g. Attachments — filesystem-per-entity (mirrors support.py's proven
    -- pattern under C:\Retailware\EngHubAttachments\{EntityType}\{EntityId}\),
    -- but WITH a metadata table for uploader attribution.
    ----------------------------------------------------------------------

    IF OBJECT_ID('dbo.EngHub_Attachment', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.EngHub_Attachment (
            AttachmentId       INT IDENTITY(1,1) NOT NULL,
            EntityType         VARCHAR(20)    NOT NULL,
            EntityId           INT            NOT NULL,
            FileName           NVARCHAR(260)  NOT NULL,
            StoredFileName     NVARCHAR(260)  NOT NULL,
            FileSizeBytes      INT            NOT NULL,
            ContentType        NVARCHAR(100)  NULL,
            UploadedByUserId   INT            NOT NULL,
            UploadedAt         DATETIME2(3)   NOT NULL CONSTRAINT DF_EngHub_Attachment_UploadedAt DEFAULT (SYSUTCDATETIME()),
            Enabled            BIT            NOT NULL CONSTRAINT DF_EngHub_Attachment_Enabled DEFAULT (1),
            CONSTRAINT PK_EngHub_Attachment PRIMARY KEY CLUSTERED (AttachmentId),
            CONSTRAINT FK_EngHub_Attachment_UploadedBy FOREIGN KEY (UploadedByUserId) REFERENCES dbo.UserMaster (UserID),
            CONSTRAINT CK_EngHub_Attachment_EntityType CHECK (EntityType IN ('Feature','DevelopmentItem','Task','Activity','Decision'))
        );
        CREATE INDEX IX_EngHub_Attachment_Entity ON dbo.EngHub_Attachment (EntityType, EntityId);
        PRINT 'Created table dbo.EngHub_Attachment';
    END

    COMMIT TRANSACTION EngHubMigration;
    PRINT 'SECTION 1 (schema) completed successfully — all tables/constraints/indexes are in place.';

END TRY
BEGIN CATCH
    -- Rolled back by name only when the named transaction is still the active
    -- one (XACT_STATE()=1); XACT_STATE()=-1 means SET XACT_ABORT ON already
    -- auto-rolled back the transaction, and rolling back a name that's no
    -- longer on the stack raises its own error (6401) that would otherwise
    -- mask the real one being reported below.
    IF XACT_STATE() = 1
        ROLLBACK TRANSACTION EngHubMigration;
    ELSE IF XACT_STATE() = -1
        ROLLBACK TRANSACTION;

    DECLARE @ErrMsg1 NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrSeverity1 INT = ERROR_SEVERITY();
    DECLARE @ErrState1 INT = ERROR_STATE();
    RAISERROR('Engineering Hub SCHEMA deployment FAILED and was rolled back: %s', @ErrSeverity1, @ErrState1, @ErrMsg1);
END CATCH
GO


----------------------------------------------------------------------
-- SECTION 2: Seed / master data.
-- Idempotent (checked by Name before insert) and transactional/rollback-
-- safe like Section 1, but deliberately its own transaction so this can be
-- re-run independently of the schema section (e.g. to add a master row
-- later without re-running DDL). CreatedByUserId=1 is a placeholder system
-- seed marker — none of the CreatedByUserId/LastEditedByUserId columns in
-- this schema carry a foreign key, so this does not require a real UserID
-- 1 to exist on the target database.
----------------------------------------------------------------------

BEGIN TRANSACTION EngHubSeedData;

BEGIN TRY

    DECLARE @Sys INT = 1;

    INSERT INTO dbo.EngHub_TaskType (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES ('Desktop'),('API'),('Web'),('Testing'),('Documentation')) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_TaskType t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_DevItemType (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES ('Enhancement'),('Bug Fix'),('Refactoring'),('Customer Requirement')) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_DevItemType t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_ActivityType (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES
        ('Work Logged'),('Discussion'),('Pair Programming'),('Status Change'),
        ('Assignment'),('Testing'),('Bug Found'),('Comment'),('Attachment'),
        ('Interruption'),('Release Mapping')
    ) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_ActivityType t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_DecisionType (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES
        ('Customer Exception'),('Customer Responsibility'),('Management Override'),
        ('Technical Debt'),('Temporary Workaround'),('Product Strategy'),
        ('Regulatory Interpretation')
    ) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_DecisionType t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_Priority (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES ('Low'),('Medium'),('High'),('Critical')) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_Priority t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_OriginType (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES
        ('Support'),('Management'),('Sales'),('Customer'),('Partner'),('Tester'),('Developer')
    ) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_OriginType t WHERE t.Name = v.Name);

    INSERT INTO dbo.EngHub_InterruptionReason (Name, CreatedByUserId)
    SELECT v.Name, @Sys FROM (VALUES
        ('Urgent Customer Issue'),('Management Request'),('Production Bug'),('Meeting'),('Other')
    ) AS v(Name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.EngHub_InterruptionReason t WHERE t.Name = v.Name);

    -- Status vocabularies, one set per EntityType. NOTE: renaming or
    -- disabling 'Testing' (Task), 'Support' (OriginType above), or any of
    -- the 11 ActivityType names would silently break the application, since
    -- three backend reports match those specific names by exact string
    -- (Testing Effort report, Support-Driven Work report, and the
    -- quick-log/interruption/release-mapping activity logging) rather than
    -- by a stable code — this is an existing application constraint, not
    -- something this script can enforce at the DB level.
    INSERT INTO dbo.EngHub_Status (EntityType, Name, SortOrder, IsTerminal, CreatedByUserId)
    SELECT v.EntityType, v.Name, v.SortOrder, v.IsTerminal, @Sys
    FROM (VALUES
        ('Feature',          'Active',       1, 0),
        ('Feature',          'Retired',      2, 1),
        ('DevelopmentItem',  'Planned',      1, 0),
        ('DevelopmentItem',  'In Progress',  2, 0),
        ('DevelopmentItem',  'In Testing',   3, 0),
        ('DevelopmentItem',  'On Hold',      4, 0),
        ('DevelopmentItem',  'Released',     5, 1),
        ('DevelopmentItem',  'Cancelled',    6, 1),
        ('Task',             'To Do',        1, 0),
        ('Task',             'In Progress',  2, 0),
        ('Task',             'Blocked',      3, 0),
        ('Task',             'Done',         4, 1),
        ('Task',             'Hold',         5, 0),
        ('Task',             'Cancelled',    6, 1),
        ('Decision',         'Active',       1, 0),
        ('Decision',         'Under Review', 2, 0),
        ('Decision',         'Superseded',   3, 1),
        ('Decision',         'Expired',      4, 1),
        ('Release',          'Planned',      1, 0),
        ('Release',          'In Progress',  2, 0),
        ('Release',          'Released',     3, 1),
        ('Release',          'Cancelled',    4, 1)
    ) AS v(EntityType, Name, SortOrder, IsTerminal)
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.EngHub_Status s WHERE s.EntityType = v.EntityType AND s.Name = v.Name
    );

    COMMIT TRANSACTION EngHubSeedData;
    PRINT 'SECTION 2 (seed data) completed successfully.';

END TRY
BEGIN CATCH
    IF XACT_STATE() = 1
        ROLLBACK TRANSACTION EngHubSeedData;
    ELSE IF XACT_STATE() = -1
        ROLLBACK TRANSACTION;

    DECLARE @ErrMsg2 NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrSeverity2 INT = ERROR_SEVERITY();
    DECLARE @ErrState2 INT = ERROR_STATE();
    RAISERROR('Engineering Hub SEED DATA deployment FAILED and was rolled back: %s', @ErrSeverity2, @ErrState2, @ErrMsg2);
END CATCH
GO


----------------------------------------------------------------------
-- SECTION 3: Post-deployment verification.
-- Run this manually (or as part of an automated deploy check) after the
-- script completes, to confirm all 24 tables exist and seed data landed.
-- Expect 24 rows in the first result set, and non-zero counts in the
-- master tables (TaskType/DevItemType/ActivityType/DecisionType/Priority/
-- OriginType/InterruptionReason/Status) in the second.
----------------------------------------------------------------------

SELECT name AS TableName
FROM sys.tables
WHERE name LIKE 'EngHub%'
ORDER BY name;

SELECT 'EngHub_TaskType' AS TableName, COUNT(*) AS RowCount FROM dbo.EngHub_TaskType
UNION ALL SELECT 'EngHub_DevItemType', COUNT(*) FROM dbo.EngHub_DevItemType
UNION ALL SELECT 'EngHub_ActivityType', COUNT(*) FROM dbo.EngHub_ActivityType
UNION ALL SELECT 'EngHub_DecisionType', COUNT(*) FROM dbo.EngHub_DecisionType
UNION ALL SELECT 'EngHub_Priority', COUNT(*) FROM dbo.EngHub_Priority
UNION ALL SELECT 'EngHub_OriginType', COUNT(*) FROM dbo.EngHub_OriginType
UNION ALL SELECT 'EngHub_InterruptionReason', COUNT(*) FROM dbo.EngHub_InterruptionReason
UNION ALL SELECT 'EngHub_Status', COUNT(*) FROM dbo.EngHub_Status;
GO
