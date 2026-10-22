/* Exact definition of Worldnettech.dbo.WebProc_SIPMissedCalls as it was on the
   server on 22-Oct-2026 (captured with OBJECT_DEFINITION), with CREATE turned into
   ALTER so it can be run as-is to restore the proc. Offline fallback for the
   rollback script. */
USE [Worldnettech];
GO


ALTER PROC [dbo].[WebProc_SIPMissedCalls] (@fromDate datetime=null,@ToDate datetime=null,@Software Nvarchar(100)='') AS  
BEGIN  
	SET NOCOUNT ON;  
	BEGIN TRY  
		print 'execution of synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd started'
		EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;  
		print 'execution of synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd completed'
	END TRY  
	BEGIN CATCH  
	END CATCH;   

	If @Software='' 
	Begin
		Set @Software='RETAILWARE';
	End
	--Select s.*,S.Software as 'Software Name'  From dbo.webfun_SIPMissedCalls(@fromDate,@ToDate)  s 	
	--Where Software=@Software Order by RecordNo   
	--print 'execution of webfun_SIPMissedCalls started'
	Select s.RecordNo,s.EntityID,s.EntityType, s.EntityName,
	--(case when ca1.AttrValue >0 then s.EntityName +' [Type:'+ isnull(ca1.AttrValue,'') +']' else s.EntityName end) as EntityName,
	s.EntityPhone,s.CallDate,s.CallTime,s.MinCallTime,s.CallType,
	s.AMCAmount,s.AMCExpiryDate,s.Processed,s.ActionStateID,s.Cnt,s.Ledgerbalance,s.Software,s.ExecutiveName,s.SIDateDifflessThanOneMonth,
	ca.AttrValue 'Software Name',isnull(cam.AttrValue,'')AdvanceAMC   
	From dbo.webfun_SIPMissedCalls(@fromDate,@ToDate)  s 
	Left Outer join CustomerAttributes CA On s.EntityID=ca.CustID  And ca.AttributeID=1 And ca.DepID=1
	--Left Outer join CustomerAttributes CA1 On s.EntityID=ca1.CustID  And ca1.AttributeID=38 And ca1.DepID=1
	left outer join CustomerAttributes cam on s.EntityID=cam.CustID and cam.AttributeID = 37 and cam.AttrValue='ADVANCE AMC'
	Where Software=@Software
	Order by RecordNo   
	--print 'execution of webfun_SIPMissedCalls completed'

END
GO
