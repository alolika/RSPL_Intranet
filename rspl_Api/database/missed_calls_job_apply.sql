/* ============================================================================
   Missed Calls slowness fix  (22-Oct-2026)                         APPLY SCRIPT
   ----------------------------------------------------------------------------
   Server  : SERVER2022\ACCOUNTS     Run as: sa (or sysadmin)     Tool: SSMS
   What it does (all-or-nothing, inside one transaction):
     1. BACKUP  - copies the current dbo.WebProc_SIPMissedCalls (Worldnettech)
                  to dbo.WebProc_SIPMissedCalls_Backup_20261022, exactly as it
                  is on the server at the moment this script runs.
     2. JOB     - SQL Agent job 'Get data from SIP':
                  step 1 'fetch' (command unchanged) now goes on to step 2 on
                  success; new step 2 'process' runs
                  SynapseCDR.dbo.procSIP_SIPEventRegisterCDRAdd.
     3. PROC    - removes the procSIP_SIPEventRegisterCDRAdd call from
                  dbo.WebProc_SIPMissedCalls. Nothing else in the proc changes.
   Rollback : missed_calls_job_rollback.sql
   Fallback : WebProc_SIPMissedCalls_original_20261022.sql (exact original text)
   Safe to re-run: it refuses (and changes nothing) if already applied.
   ============================================================================ */
USE [Worldnettech];
SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    ------------------------------------------------------------------ checks
    IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = N'Get data from SIP')
        THROW 50001, 'Job "Get data from SIP" not found.', 1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps s JOIN msdb.dbo.sysjobs j ON j.job_id = s.job_id
               WHERE j.name = N'Get data from SIP' AND s.step_id > 1)
        THROW 50002, 'Job already has more than one step - already applied? Nothing changed.', 1;
    IF OBJECT_ID(N'dbo.WebProc_SIPMissedCalls_Backup_20261022', N'P') IS NOT NULL
        THROW 50003, 'Backup proc already exists - already applied? Nothing changed.', 1;

    ------------------------------------------------------------------ 1. backup
    DECLARE @def nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'dbo.WebProc_SIPMissedCalls'));
    IF @def IS NULL OR CHARINDEX(N'CREATE PROC [dbo].[WebProc_SIPMissedCalls]', @def) = 0
        THROW 50004, 'Unexpected definition of dbo.WebProc_SIPMissedCalls - stopping.', 1;
    SET @def = REPLACE(@def, N'CREATE PROC [dbo].[WebProc_SIPMissedCalls]', N'CREATE PROC [dbo].[WebProc_SIPMissedCalls_Backup_20261022]');
    EXEC sys.sp_executesql @def;

    ------------------------------------------------------------------ 2. job
    EXEC msdb.dbo.sp_add_jobstep
        @job_name          = N'Get data from SIP',
        @step_id           = 2,
        @step_name         = N'process',
        @subsystem         = N'TSQL',
        @database_name     = N'SynapseCDR',
        @command           = N'exec dbo.procSIP_SIPEventRegisterCDRAdd;',
        @on_success_action = 1,   -- quit reporting success
        @on_fail_action    = 2,   -- quit reporting failure
        @retry_attempts    = 0;

    EXEC msdb.dbo.sp_update_jobstep
        @job_name          = N'Get data from SIP',
        @step_id           = 1,
        @on_success_action = 3;   -- go to next step (was 1 = quit with success)

    ------------------------------------------------------------------ 3. proc
    EXEC (N'

ALTER PROC [dbo].[WebProc_SIPMissedCalls] (@fromDate datetime=null,@ToDate datetime=null,@Software Nvarchar(100)='''') AS  
BEGIN  
	SET NOCOUNT ON;  
	-- 22-Oct-2026: the SIPEventRegisterCDR import (synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd)
	-- no longer runs here. It is now step 2 of the SQL Agent job ''Get data from SIP'' (every 30s).
	-- Running it inside every report call made concurrent users block each other on
	-- SIPEventRegisterCDR. Original version kept as dbo.WebProc_SIPMissedCalls_Backup_20261022.
   

	If @Software='''' 
	Begin
		Set @Software=''RETAILWARE'';
	End
	--Select s.*,S.Software as ''Software Name''  From dbo.webfun_SIPMissedCalls(@fromDate,@ToDate)  s 	
	--Where Software=@Software Order by RecordNo   
	--print ''execution of webfun_SIPMissedCalls started''
	Select s.RecordNo,s.EntityID,s.EntityType, s.EntityName,
	--(case when ca1.AttrValue >0 then s.EntityName +'' [Type:''+ isnull(ca1.AttrValue,'''') +'']'' else s.EntityName end) as EntityName,
	s.EntityPhone,s.CallDate,s.CallTime,s.MinCallTime,s.CallType,
	s.AMCAmount,s.AMCExpiryDate,s.Processed,s.ActionStateID,s.Cnt,s.Ledgerbalance,s.Software,s.ExecutiveName,s.SIDateDifflessThanOneMonth,
	ca.AttrValue ''Software Name'',isnull(cam.AttrValue,'''')AdvanceAMC   
	From dbo.webfun_SIPMissedCalls(@fromDate,@ToDate)  s 
	Left Outer join CustomerAttributes CA On s.EntityID=ca.CustID  And ca.AttributeID=1 And ca.DepID=1
	--Left Outer join CustomerAttributes CA1 On s.EntityID=ca1.CustID  And ca1.AttributeID=38 And ca1.DepID=1
	left outer join CustomerAttributes cam on s.EntityID=cam.CustID and cam.AttributeID = 37 and cam.AttrValue=''ADVANCE AMC''
	Where Software=@Software
	Order by RecordNo   
	--print ''execution of webfun_SIPMissedCalls completed''

END');

    COMMIT TRANSACTION;
    PRINT 'Applied. Backup proc: dbo.WebProc_SIPMissedCalls_Backup_20261022';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    PRINT 'NOT applied - everything rolled back.';
    THROW;
END CATCH;
GO

/* ---------------------------------------------------------------- verify
SELECT s.step_id, s.step_name, s.command, s.on_success_action, s.on_fail_action
FROM msdb.dbo.sysjobsteps s JOIN msdb.dbo.sysjobs j ON j.job_id = s.job_id
WHERE j.name = N'Get data from SIP' ORDER BY s.step_id;

-- within a minute or two the import should have caught up:
SELECT MAX(RecordNo) FROM SynapseCDR.dbo.SIPEventRegisterCDR;
SELECT MAX(recid)    FROM SynapseCDR.dbo.RecordsFrom_SIP_MySQL;   -- same or slightly ahead
---------------------------------------------------------------------------- */
