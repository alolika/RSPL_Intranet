-- Adds an optional Expected Deadline date to Engineering Hub Tasks (set from
-- the Add/Edit Task page; not required). Idempotent; safe to re-run.
-- Run on production at deploy.
IF COL_LENGTH('dbo.EngHub_Task', 'ExpectedDeadline') IS NULL
BEGIN
    ALTER TABLE dbo.EngHub_Task ADD ExpectedDeadline DATE NULL;
    PRINT 'Added column dbo.EngHub_Task.ExpectedDeadline';
END
GO
