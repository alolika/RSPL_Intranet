/* ============================================================================
   Remove the SynapseCDR import from the mobile + dashboard procs (22-Oct-2026)
                                                               ROLLBACK SCRIPT
   ----------------------------------------------------------------------------
   Restores each proc below from dbo.<name>_Backup_20261022 (backups are kept):
       - dbo.Mobile_Proc_SIPOutgoingCallAttendance
       - dbo.Mobile_Proc_SIPIncomingCallAttendance
       - dbo.Mobile_Proc_SIPMissedCalls
       - dbo.RSPL_SupportDashborad_RepeatCalls
       - dbo.RSPL_SupportDashborad_NONAMCCustomerCallsRcved
       - dbo.RSPL_SupportDashborad_CallDurationMorethan20mins
   All-or-nothing. If a backup proc is missing, nothing is changed - use
   mobile_dashboard_procs_original_20261022.sql instead.
   ============================================================================ */
USE [Worldnettech];
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @def nvarchar(max);

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.Mobile_Proc_SIPOutgoingCallAttendance_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[Mobile_Proc_SIPOutgoingCallAttendance_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.Mobile_Proc_SIPOutgoingCallAttendance_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[Mobile_Proc_SIPOutgoingCallAttendance_Backup_20261022]', N'ALTER PROC [dbo].[Mobile_Proc_SIPOutgoingCallAttendance]');
    EXEC sys.sp_executesql @def;

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.Mobile_Proc_SIPIncomingCallAttendance_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[Mobile_Proc_SIPIncomingCallAttendance_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.Mobile_Proc_SIPIncomingCallAttendance_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[Mobile_Proc_SIPIncomingCallAttendance_Backup_20261022]', N'ALTER PROC [dbo].[Mobile_Proc_SIPIncomingCallAttendance]');
    EXEC sys.sp_executesql @def;

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.Mobile_Proc_SIPMissedCalls_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[Mobile_Proc_SIPMissedCalls_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.Mobile_Proc_SIPMissedCalls_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[Mobile_Proc_SIPMissedCalls_Backup_20261022]', N'ALTER PROC [dbo].[Mobile_Proc_SIPMissedCalls]');
    EXEC sys.sp_executesql @def;

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.RSPL_SupportDashborad_RepeatCalls_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[RSPL_SupportDashborad_RepeatCalls_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.RSPL_SupportDashborad_RepeatCalls_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[RSPL_SupportDashborad_RepeatCalls_Backup_20261022]', N'ALTER PROC [dbo].[RSPL_SupportDashborad_RepeatCalls]');
    EXEC sys.sp_executesql @def;

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.RSPL_SupportDashborad_NONAMCCustomerCallsRcved_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[RSPL_SupportDashborad_NONAMCCustomerCallsRcved_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.RSPL_SupportDashborad_NONAMCCustomerCallsRcved_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[RSPL_SupportDashborad_NONAMCCustomerCallsRcved_Backup_20261022]', N'ALTER PROC [dbo].[RSPL_SupportDashborad_NONAMCCustomerCallsRcved]');
    EXEC sys.sp_executesql @def;

    SET @def = OBJECT_DEFINITION(OBJECT_ID(N'dbo.RSPL_SupportDashborad_CallDurationMorethan20mins_Backup_20261022'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[RSPL_SupportDashborad_CallDurationMorethan20mins_Backup_20261022]', @def) = 0
        THROW 50111, 'dbo.RSPL_SupportDashborad_CallDurationMorethan20mins_Backup_20261022 missing or unexpected - nothing changed.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[RSPL_SupportDashborad_CallDurationMorethan20mins_Backup_20261022]', N'ALTER PROC [dbo].[RSPL_SupportDashborad_CallDurationMorethan20mins]');
    EXEC sys.sp_executesql @def;

    COMMIT TRANSACTION;
    PRINT 'Rolled back: 6 procs restored from their _Backup_20261022 copies.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    PRINT 'Rollback NOT applied - nothing changed.';
    THROW;
END CATCH;
GO

-- Optional, once no longer needed:
-- DROP PROC dbo.Mobile_Proc_SIPOutgoingCallAttendance_Backup_20261022;
-- DROP PROC dbo.Mobile_Proc_SIPIncomingCallAttendance_Backup_20261022;
-- DROP PROC dbo.Mobile_Proc_SIPMissedCalls_Backup_20261022;
-- DROP PROC dbo.RSPL_SupportDashborad_RepeatCalls_Backup_20261022;
-- DROP PROC dbo.RSPL_SupportDashborad_NONAMCCustomerCallsRcved_Backup_20261022;
-- DROP PROC dbo.RSPL_SupportDashborad_CallDurationMorethan20mins_Backup_20261022;
