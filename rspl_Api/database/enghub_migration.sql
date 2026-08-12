/*
================================================================================
 Engineering Hub — SQL Server Migration Script
================================================================================
 Target engine : Microsoft SQL Server (T-SQL), via pyodbc / "ODBC Driver 17
                  for SQL Server" (app/config.py, app/db.py) — no other
                  engine is used anywhere in this application.

 Purpose        : Deploys the complete Engineering Hub schema (masters, core
                  hierarchy, activities, decisions, assignment history,
                  releases, attachments) as new, independent tables. Nothing
                  in this script alters any pre-existing table.

 Idempotent     : Every CREATE is guarded by an existence check (OBJECT_ID),
                  so this script is safe to run repeatedly, including a
                  partial re-run after an earlier failure.

 Transactional  : One explicit transaction with TRY/CATCH — SQL Server DDL is
                  fully transactional, so a failure partway through rolls
                  back the whole script, not just the DML.

 Design summary (see the approved Engineering Hub design proposal for the
 full rationale — this is just the "what", not the "why"):
   - EngHub_ prefix, PascalCase columns, IDENTITY(1,1) PKs.
   - Enabled BIT soft-delete on maintained/master entities; append-only
     history tables (Activity, ActivityParticipant, AssignmentHistory,
     ReleaseMapping) have no Enabled/LastEdited* columns — they are never
     edited or deleted, only inserted.
   - Engineering Hub owns its own Product/Module/Priority masters rather
     than reusing Agile_ProjectMaster/agile_ModuleMaster/PriorityMaster —
     a deliberate loose-coupling choice, not an oversight.
   - Real FKs into UserMaster.UserID, CustomerMaster.CustID and
     TTMaster.VoucherNo (all confirmed PKs in this same database) rather
     than soft/app-level references, since "reference, don't duplicate"
     existing data is possible here without any cross-server dependency.
   - EngHub_Activity/EngHub_Decision may attach to a Feature and/or a
     DevelopmentItem and/or a Task (at least one required) rather than
     being forced through a single mandatory Task FK.
   - EngHub_AssignmentHistory and EngHub_Attachment use a polymorphic
     (EntityType, EntityId) pair instead of one near-identical table per
     entity type — referential integrity for these two is app-level only,
     a deliberate trade-off to avoid a 3x/5x table explosion.
================================================================================
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;   -- required for the filtered unique index below

BEGIN TRANSACTION EngHubMigration;

BEGIN TRY

    ----------------------------------------------------------------------
    -- Masters
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
    -- Core hierarchy: Product -> Module -> Feature -> DevelopmentItem -> Task
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
    -- Activities (append-only). May attach to a Feature and/or a
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
    -- Decisions — may attach to a Feature and/or DevelopmentItem and/or
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
    -- Assignment history — never overwritten. Polymorphic (EntityType,
    -- EntityId): no DB-level FK across the three possible target tables
    -- (SQL Server can't enforce that), app-level integrity only. Rejected
    -- alternative was three near-identical per-entity-type tables, which
    -- would be pure duplication with no behavioral difference.
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
    -- Releases
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
    -- Attachments — filesystem-per-entity (mirrors support.py's proven
    -- pattern under C:\Retailware\EngHubAttachments\{EntityType}\{EntityId}\),
    -- but WITH a metadata table for uploader attribution, which support.py's
    -- pure folder-listing approach never had.
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
    PRINT 'Engineering Hub migration completed successfully.';

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

    DECLARE @ErrMsg NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrSeverity INT = ERROR_SEVERITY();
    DECLARE @ErrState INT = ERROR_STATE();
    RAISERROR('Engineering Hub migration FAILED and was rolled back: %s', @ErrSeverity, @ErrState, @ErrMsg);
END CATCH
GO


/*
================================================================================
 Seed data — master rows the UI needs to be usable on day one. Idempotent
 (checked by Name before insert). CreatedByUserId=1 is a placeholder system
 seed marker, matching no real login flow.
================================================================================
*/

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

-- Status vocabularies, one set per EntityType.
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

PRINT 'Engineering Hub seed data applied.';
