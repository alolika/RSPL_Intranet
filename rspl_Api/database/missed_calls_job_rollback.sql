/* ============================================================================
   Missed Calls slowness fix  (22-Oct-2026)                      ROLLBACK SCRIPT
   ----------------------------------------------------------------------------
   Puts everything back exactly as it was before missed_calls_job_apply.sql:
     1. dbo.WebProc_SIPMissedCalls  <- definition saved in
        dbo.WebProc_SIPMissedCalls_Backup_20261022 (the backup proc is kept).
     2. Job 'Get data from SIP': step 2 removed, step 1 back to
        on_success_action = 1 (quit with success), on_fail_action = 2.
   If the backup proc is missing for any reason, run
   WebProc_SIPMissedCalls_original_20261022.sql instead for part 1.
   ============================================================================ */
USE [Worldnettech];
SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    ------------------------------------------------------------------ 1. proc
    DECLARE @def nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'dbo.WebProc_SIPMissedCalls_Backup_20261022'));
    IF @def IS NULL
        THROW 50011, 'Backup proc not found - use WebProc_SIPMissedCalls_original_20261022.sql.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[WebProc_SIPMissedCalls_Backup_20261022]',
                             N'ALTER PROC [dbo].[WebProc_SIPMissedCalls]');
    EXEC sys.sp_executesql @def;

    ------------------------------------------------------------------ 2. job
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps s JOIN msdb.dbo.sysjobs j ON j.job_id = s.job_id
               WHERE j.name = N'Get data from SIP' AND s.step_id = 2)
        EXEC msdb.dbo.sp_delete_jobstep @job_name = N'Get data from SIP', @step_id = 2;

    EXEC msdb.dbo.sp_update_jobstep
        @job_name          = N'Get data from SIP',
        @step_id           = 1,
        @on_success_action = 1,
        @on_fail_action    = 2;

    COMMIT TRANSACTION;
    PRINT 'Rolled back to the 22-Oct-2026 state.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    PRINT 'Rollback NOT applied - nothing changed.';
    THROW;
END CATCH;
GO

-- Optional, once you no longer need it:
-- DROP PROC dbo.WebProc_SIPMissedCalls_Backup_20261022;
