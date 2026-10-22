/* Exact definitions of the 6 procs as they were on 22-Oct-2026 (captured with
   OBJECT_DEFINITION), CREATE turned into ALTER so this file restores them as-is.
   Offline fallback for mobile_dashboard_import_rollback.sql. */
USE [Worldnettech];
GO
SET ANSI_NULLS ON;
GO
SET QUOTED_IDENTIFIER ON;
GO

-- ---------------------------------------------------- dbo.Mobile_Proc_SIPOutgoingCallAttendance
ALTER PROC [dbo].[Mobile_Proc_SIPOutgoingCallAttendance] (@LevelNo int,@Rowno nvarchar(20)) 
AS       
BEGIN           
	------Declare  @SQL as nvarchar(max)             
	------Declare @Billdate datetime;           
	------Declare @Strfilter1 nvarchar(50)      
    ------DECLARE @FieldName nvarchar(100)                
    ------DECLARE @ColumnName nvarchar(100)           
    ------Select @Billdate=BillDate From Options;           
    ------ Declare @DateQuery as nvarchar(2000)            
    ------ DECLARE @ColumnId nvarchar(100)          
    ------ DECLARE @IsDateRange bit              
    ------ declare @Query nvarchar(4000)           
    ------ DECLARE @FromDate datetime         
    ------DECLARE @ToDate datetime         
    ------ set @Strfilter1 = ''         
    ------Select @Billdate=BillDate From Options;           
    ------BEGIN TRY           
    ------ EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;           ------END TRY           ------BEGIN CATCH           ------ Select @Billdate=BillDate From Options;           ------END CATCH;        
	------Declare c1 cursor for               ------select Q.ColumnId,F.FieldName,F.ColumnName,IsDateRange,replace(Q.Query,'% ','%')Query                 ------from RSPL_ReportList R Inner Join RSpl_ReportFilterMaster F On R.ReportID=F.ReportID            
    ------Inner Join RSPL_ReportFilterQuery Q On  F.ReportID=Q.ReportID and F.ColumnID=Q.ColumnId                     ------where ReportSQL='Mobile_Proc_SIPOutgoingCallAttendance'          ------  open c1                                                
	------Fetch next from c1 into @ColumnId,@FieldName,@ColumnName,@IsDateRange,@Query               ------While @@Fetch_Status=0                                                   ------Begin                    ------ if @FieldName ='Date'                  -
	----- Begin                               
	------  set @DateQuery=replace(upper(@Query),upper('between'),'')                                                                             ------  set @DateQuery=replace(@DateQuery,'(','')                      
    ------  set @DateQuery=replace(@DateQuery,')','')                       ------  print 'DateQry'             ------  print @DateQuery               ------  set @Fromdate=substring(SUBSTRING(@DateQuery,1,CHARINDEX ('and',@DateQuery,1) -2),Len(SUBSTRING(@DateQuery,1,CHARINDEX ('and',@DateQuery,1) -2))-11,12)                                                                      ------  print '@Fromdate1'             ------  print @Fromdate               --
	----  set @Fromdate = replace(@Fromdate,'''','')                                                                 ------  set @Todate=SUBSTRING(@DateQuery,CHARINDEX ('and',@DateQuery,1)+3,LEN(@datequery)-1)                                                  
    ------  set @Todate = replace(@Todate,'''','')                            ------  print @Fromdate               ------  print @Todate               ------ End                   ------ set @Strfilter1=@Strfilter1 + @Query + ' and '       
    ------ Fetch next from c1 into @ColumnId,@FieldName,@ColumnName,@IsDateRange,@Query                                         ------End                                                 ------close c1                             
    ------deallocate c1              ------Print 'Fromdate'             ------print @Fromdate             ------Print 'Todate'             ------print @Todate          ------if @Strfilter1 = ''             ------Begin             ------ select @Fromdate=Billdate From Options             ------ select @Todate=Billdate From Options             ------End          ------Print 'Fromdate'             ------print @Fromdate             ------Print 'Todate'             ------print @Todate          
	------Select R.SourceName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]            ------From  synapsecdr.dbo.SIPEventRegisterCDR R            ------Where R.SourceEntityTypeID=1 and R.CallDate=@Billdate and R.CallType='External-outgoing'           ------Group By R.SourceName           ------Order By  [C|TotalCalls] Desc,[C|Mins]  Desc           --------Select R.SourceName Executive ,count(distinct R.DestinationNo)[C|No.of cust],Count(*) [C|TotalCalls] , cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]             --------From  synapsecdr.dbo.SIPEventRegisterCDR R            --------Where R.SourceEntityTypeID=1 and R.CallDate between @FromDate and @ToDate  and R.CallType='External-outgoing'            
	--------Group By R.SourceName           
	--------Order By  [C|TotalCalls] Desc,[C|Mins]  Desc           
	Declare @Billdate datetime;     
	Select @Billdate=BillDate From Options;     
	BEGIN TRY       
	EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;     
	END TRY    
	BEGIN CATCH       
	Select @Billdate=BillDate From Options;     
	END CATCH;     
	--Select R.SourceName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]      
	--From  synapsecdr.dbo.SIPEventRegisterCDR R      --Where R.SourceEntityTypeID=1 and R.CallDate=@Billdate and R.CallType='External-outgoing'     --Group By R.SourceName     --Order By  [C|TotalCalls] Desc,[C|Mins]  Desc      
	-- Changes done on 03-May-25 (Alolika and Pracchi)  --Select R.SourceName Executive ,count(distinct R.DestinationNo)[C|No.of cust],Count(*) [C|TotalCalls] , cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]        --From  synapsecdr.dbo.SIPEventRegisterCDR R   Inner Join   (select * From vwEntityContactList where EntityTypeID =1) vw on cast(R.SourceNo as nvarchar(1000))=Vw.ContactNo   
	-- Where R.SourceEntityTypeID=1 and R.CallDate=@Billdate and R.CallType='External-outgoing'       --Group By R.SourceName      --Order By  [C|TotalCalls] Desc,[C|Mins]  Desc    
	Select vw.CompanyName  Executive,count(distinct R.DestinationNo)[C|No.of cust],Count(*) [C|TotalCalls] , 
	cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]   ,
	(select COUNT(Voucherno) From TTMaster where DATE=@Billdate and UserID=vw.EntityID) TTCount     
	From  synapsecdr.dbo.SIPEventRegisterCDR R   
	Inner Join   (select * From vwEntityContactList where EntityTypeID =1) vw on cast(R.SourceNo as nvarchar(1000))=Vw.ContactNo    
	Where R.CallDate=@Billdate and R.CallType='External-outgoing' and R.BillSeconds >0    
	Group By vw.CompanyName,Vw.EntityID      Order By  [C|TotalCalls] Desc,[C|Mins]  Desc    
END  


GO

-- ---------------------------------------------------- dbo.Mobile_Proc_SIPIncomingCallAttendance
--exec [Mobile_Proc_SIPIncomingCallAttendance] 1,1  
  
ALTER PROC [dbo].[Mobile_Proc_SIPIncomingCallAttendance] (@LevelNo int,@Rowno nvarchar(20)) AS    
BEGIN    
 --  Declare  @SQL as nvarchar(max)      
 --Declare @Billdate datetime;    
 --Declare @Strfilter1 nvarchar(50)        
 --DECLARE @FieldName nvarchar(100)         
 --DECLARE @ColumnName nvarchar(100)    
 --Select @Billdate=BillDate From Options;    
 -- Declare @DateQuery as nvarchar(2000)     
 -- DECLARE @ColumnId nvarchar(100)   
 -- DECLARE @IsDateRange bit       
 -- declare @Query nvarchar(4000)    
 -- DECLARE @FromDate datetime  
 --DECLARE @ToDate datetime  
    
 -- set @Strfilter1 = ''  
 --BEGIN TRY    
 -- EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;    
 --END TRY    
 --BEGIN CATCH    
 -- Select @Billdate=BillDate From Options;    
 --END CATCH;    
    
 -- Declare c1 cursor for        
 --select Q.ColumnId,F.FieldName,F.ColumnName,IsDateRange,replace(Q.Query,'% ','%')Query          
 --from RSPL_ReportList R Inner Join RSpl_ReportFilterMaster F On R.ReportID=F.ReportID            
 --Inner Join RSPL_ReportFilterQuery Q On  F.ReportID=Q.ReportID and F.ColumnID=Q.ColumnId              
 --where ReportSQL='Mobile_Proc_SIPIncomingCallAttendance'   
 --  open c1                                          
 --Fetch next from c1 into @ColumnId,@FieldName,@ColumnName,@IsDateRange,@Query        
 --While @@Fetch_Status=0                                            
 --Begin             
 -- if @FieldName ='Date'           
 -- Begin                        
 --  set @DateQuery=replace(upper(@Query),upper('between'),'')                                                                      
 --  set @DateQuery=replace(@DateQuery,'(','')                                                                      
 --  set @DateQuery=replace(@DateQuery,')','')                
 --  print 'DateQry'      
 --  print @DateQuery        
 --  set @Fromdate=substring(SUBSTRING(@DateQuery,1,CHARINDEX ('and',@DateQuery,1) -2),Len(SUBSTRING(@DateQuery,1,CHARINDEX ('and',@DateQuery,1) -2))-11,12)                                                               
 --  print '@Fromdate1'      
 --  print @Fromdate        
 --  set @Fromdate = replace(@Fromdate,'''','')                                                          
 --  set @Todate=SUBSTRING(@DateQuery,CHARINDEX ('and',@DateQuery,1)+3,LEN(@datequery)-1)                                                             
 --  set @Todate = replace(@Todate,'''','')                     
 --  print @Fromdate        
 --  print @Todate        
 -- End            
 -- set @Strfilter1=@Strfilter1 + @Query + ' and '                              
 -- Fetch next from c1 into @ColumnId,@FieldName,@ColumnName,@IsDateRange,@Query                                  
 --End                                          
 --close c1                                          
 --deallocate c1       
 --Print 'Fromdate'      
 --print @Fromdate      
 --Print 'Todate'      
 --print @Todate   
 --if @Strfilter1 = ''      
 --Begin      
 -- select @Fromdate=Billdate From Options      
 -- select @Todate=Billdate From Options      
 --End   
 --Print 'Fromdate'      
 --print @Fromdate      
 --Print 'Todate'      
 --print @Todate   
 
 --Select R.DestinationName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]     
 --From  synapsecdr.dbo.SIPEventRegisterCDR R     
 --Where R.DestinationEntityTypeID=1 and R.CallDate =@Billdate and R.CallType='External-incoming'    
 --Group By R.DestinationName    
 --Order By  [C|TotalCalls] Desc    
 
 ----Select R.DestinationName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]     
 ----From  synapsecdr.dbo.SIPEventRegisterCDR R     
 ----Where R.DestinationEntityTypeID=1 and R.CallDate between @FromDate and @ToDate and R.CallType='External-incoming'    
 ----Group By R.DestinationName    
 ----Order By  [C|TotalCalls] Desc    
   
 ----set @SQL = ' Select R.DestinationName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]     
 ----From  synapsecdr.dbo.SIPEventRegisterCDR R     
 ----Where R.DestinationEntityTypeID=1 and R.CallDate between ''' +  @Fromdate + ''' and + ''' + @Todate + ''' and R.CallType=''External-incoming''    
 ----Group By R.DestinationName  Order By  [C|TotalCalls] Desc  '  
   
 --print @SQl  
     
     Declare @Billdate datetime;  
 Select @Billdate=BillDate From Options;  
  
 BEGIN TRY  
  EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;  
 END TRY  
 BEGIN CATCH  
  Select @Billdate=BillDate From Options;  
 END CATCH;  
  
   
 Select R.DestinationName Executive,Count(*)  [C|TotalCalls],cast(Sum(BillSeconds)/60.0 as numeric(18,2)) [C|Mins]   
 From  synapsecdr.dbo.SIPEventRegisterCDR R   
 Where R.DestinationEntityTypeID=1 and R.CallDate=@Billdate and R.CallType='External-incoming'  
 Group By R.DestinationName  
 Order By  [C|TotalCalls] Desc  
END    
------------------------------------------------------------------------------------------------------------    
  
GO

-- ---------------------------------------------------- dbo.Mobile_Proc_SIPMissedCalls
------------------------------------------------------------------------------------------------------------              
ALTER PROC [dbo].[Mobile_Proc_SIPMissedCalls] (@LevelNo int,@Rowno nvarchar(20)) AS              
BEGIN              
               
	BEGIN TRY              
		EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;
	END TRY              
	BEGIN CATCH              
	END CATCH;

	Select * from
	(
		Select '1IN' [R|TY] ,EntityName [R|Name],EntityPhone [R|Phone], format(MinCallTime,'hh:mm') [R|Time],Cnt  [R|Cnt]  
		From dbo.webfun_SIPMissedCalls(null,null) Where EntityID>0 and Calltype='External-incoming_missed' and AMCAmount=0
		Union all
		Select '2IN' [R|TY] ,EntityName [R|Name],EntityPhone [R|Phone], format(MinCallTime,'hh:mm') [R|Time],Cnt  [R|Cnt]  
		From dbo.webfun_SIPMissedCalls(null,null) Where EntityID>0 and Calltype='External-incoming_missed' and AMCAmount>0
		Union all
		Select '3OT' [R|TY] ,EntityName [R|Name],EntityPhone [R|Phone], format(MinCallTime,'hh:mm') [R|Time],Cnt  [R|Cnt]  
		From dbo.webfun_SIPMissedCalls(null,null) Where EntityID>0 and Calltype='External-outgoing_missed'
		Union all
		Select '4IN' [R|TY] ,EntityName [R|Name],EntityPhone [R|Phone], format(MinCallTime,'hh:mm') [R|Time],Cnt  [R|Cnt]  
		From dbo.webfun_SIPMissedCalls(null,null) Where EntityID=0 and Calltype='External-incoming_missed'
		Union all
		Select '5OT' [R|TY] ,EntityName [R|Name],EntityPhone [R|Phone], format(MinCallTime,'hh:mm') [R|Time],Cnt  [R|Cnt]  
		From dbo.webfun_SIPMissedCalls(null,null) Where EntityID=0 and Calltype='External-outgoing_missed'
	) X Order By [R|TY], [R|Time]

END              
------------------------------------------------------------------------------------------------------------     

GO

-- ---------------------------------------------------- dbo.RSPL_SupportDashborad_RepeatCalls
ALTER PROC [dbo].[RSPL_SupportDashborad_RepeatCalls]  as          
 Begin   
 
  BEGIN TRY            
   EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;            
  END TRY            
  BEGIN CATCH                    
  END CATCH;                                    
 declare @Billdate Datetime          
 select @Billdate=Billdate from options          
 select Customer,sum(calls)Calls,sum(billseconds)/60 Mins   from (          
 select Cm.Displayname Customer ,Count(*) Calls ,sum(billseconds)    billseconds      
 from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.SourceEntityID=CM.Custid          
 where (SourceEntityTypeid =3 )          
 and CallDate =@Billdate and CallType not like '%missed%'          
 and custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')          
 group by Cm.Displayname          
 union ALL          
 select Cm.Displayname Customer ,Count(*) Calls ,sum(billseconds)    billseconds         
 from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.DestinationEntityID=CM.Custid          
 where (DestinationEntityTypeid =3 )          
 and CallDate =@Billdate and CallType not like '%missed%'          
 and custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')          
 group by Cm.Displayname)          
 Tbl Group by customer       
 Having  sum(calls)>=3 Order by sum(Calls) desc          
end          
---- Report 1 : Repeat Calls 
GO

-- ---------------------------------------------------- dbo.RSPL_SupportDashborad_NONAMCCustomerCallsRcved
ALTER PROC [dbo].[RSPL_SupportDashborad_NONAMCCustomerCallsRcved] as                    
Begin                
  BEGIN TRY            
   EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;            
  END TRY            
  BEGIN CATCH                    
  END CATCH;                                    
      
  
  declare @Billdate Datetime                    
  select @Billdate=Billdate from options                    
  select [User],Customer,sum(InComming)[In],sum(Outgoing)[Out],convert(varchar(20),ExpiryDate,106)[ExpiryDt],                
  parsename(convert(varchar,convert(money,AMCAmt  ),1),2)AMCAmt from (                    
  select Um.Name [User],Cm.Displayname Customer ,Count(*) InComming, 0 Outgoing ,AMCDueDate ExpiryDate,AMCAmount AMCAmt                    
  from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.SourceEntityID=CM.Custid                    
  Inner Join USermaster Um On Um.userid=CDR.DestinationEntityID                    
  Inner join (select * from VWAMCForAllCustomers where AMCDueDate<(select Billdate from options))F On F.CustID=CM.Custid                    
  where (SourceEntityTypeid =3 )                    
  and CallDate =@Billdate and CallType not like '%missed%'                    
  and CM.custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')                    
  group by Um.Name ,Cm.Displayname,AMCDueDate,AMCAmount                    
  union ALL                    
  select Um.Name [User],Cm.Displayname Customer ,0 InComming, Count(*) Outgoing,AMCDueDate ExpiryDate,AMCAmount AMCAmt                    
  from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.DestinationEntityID=CM.Custid                    
  Inner Join USermaster Um On Um.userid=CDR.SourceEntityID                    
  Inner join (select * from VWAMCForAllCustomers where AMCDueDate<(select Billdate from options))F On F.CustID=CM.Custid                    
  where (DestinationEntityTypeid =3 )                     
  and CallDate =@Billdate and CallType not like '%missed%'                    
  and Cm.custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')                    
  group by Um.Name ,Cm.Displayname,AMCDueDate,AMCAmount)                    
  Tbl Group by [User],customer,ExpiryDate,AMCAmt Order by [User],customer,ExpiryDate,AMCAmt                    
end 
GO

-- ---------------------------------------------------- dbo.RSPL_SupportDashborad_CallDurationMorethan20mins
ALTER PROC [dbo].[RSPL_SupportDashborad_CallDurationMorethan20mins] as            
Begin      
  BEGIN TRY            
   EXEC synapsecdr.dbo.procSIP_SIPEventRegisterCDRAdd;            
  END TRY            
  BEGIN CATCH                    
  END CATCH;                                    
    
     
            
 declare @Billdate Datetime            
 select @Billdate=Billdate from options            
 select Customer,[User],          
 [In]=case when sum(InComming)<>0 then cast(round(sum(InComming)/60,0)as nvarchar)+' mins' end ,          
 [Out]=case when sum(Outgoing)<>0 then cast(round(sum(Outgoing)/60,0) as nvarchar)+' mins' End   from (            
 select Um.Name [User],Cm.Displayname Customer ,sum(billseconds) InComming, 0 Outgoing             
 from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.SourceEntityID=CM.Custid            
 Inner Join USermaster Um On Um.userid=CDR.DestinationEntityID             
 where (SourceEntityTypeid =3 )            
 and CallDate =@Billdate and CallType not like '%missed%'            
 and CM.custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')            
 group by Um.Name ,Cm.Displayname            
 union ALL            
 select Um.Name [User],Cm.Displayname Customer ,0 InComming, sum(billseconds)Outgoing            
 from synapsecdr.dbo.SIPEventRegisterCDR CDR Inner Join CustomerMaster CM On CDR.DestinationEntityID=CM.Custid            
 Inner Join USermaster Um On Um.userid=CDR.SourceEntityID             
 where (DestinationEntityTypeid =3 )             
 and CallDate =@Billdate and CallType not like '%missed%'            
 and Cm.custid in(select distinct custid from customerattributes where attributeid=1 and attrvalue='RETAILWARE')            
 group by Um.Name ,Cm.Displayname)            
 Tbl Group by [User],customer             
 having (        
         
 (sum(InComming)/60)>=20 or         
 (sum(Outgoing)/60)>=20 or         
 ((sum(InComming)/60)+(sum(Outgoing)/60))>=20        
         
 )             
 Order by customer,[User]            
end 
GO
