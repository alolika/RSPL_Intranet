/*
Sentimeter stored procedures and configurable keyword exclusions
PRD v1.4 additions: keyword analytics also returns distinctCustomers + averageDurationSeconds
(one row per call+keyword before aggregating); keyword calls accept @ColumnFiltersJson
(Excel-style, applied before paging; mood counts unaffected); new
usp_Sentimeter_GetKeywordCallFilterOptions; shared row source usp_Sentimeter_FillKeywordCalls.
Target: SQL Server 2016+ (database compatibility level 130+ for OPENJSON/STRING_SPLIT)
This script creates dbo.call_KeywordExclusions if it does not exist, seeds two default exclusions idempotently,
and creates/updates stored procedures. It does not alter the existing call source tables.
Review in a non-production database first. Run with an approved deployment account.
The exclusion administration procedures write only to the Sentimeter-owned configuration table; analytics/content procedures remain read-only against the source call tables.

Known tables:
  dbo.call_summaries: id, call_date, customer_name, executive_name, software_name,
                      call_duration (integer seconds), customer_mood, keywords (comma-separated)
  dbo.call_summary_details: call_id, facts, short_summary, long_summary, transcript, analysis_raw
Join: call_summaries.id = call_summary_details.call_id

JSON filter parameters must be JSON arrays, e.g. N'["Retailware","Jewelsoft"]'.
DateTo is inclusive at the API/UI level; procedures include all of that date by using < DATEADD(day, 1, @DateTo).
*/
GO


/* Configurable keyword exclusion rules. Safe to rerun. */
IF OBJECT_ID(N'dbo.call_KeywordExclusions', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.call_KeywordExclusions
    (
        ExclusionId int IDENTITY(1,1) NOT NULL CONSTRAINT PK_call_KeywordExclusions PRIMARY KEY,
        Keyword nvarchar(200) NOT NULL,
        Reason nvarchar(500) NULL,
        IsActive bit NOT NULL CONSTRAINT DF_call_KeywordExclusions_IsActive DEFAULT (1),
        CreatedAt datetime2 NOT NULL CONSTRAINT DF_call_KeywordExclusions_CreatedAt DEFAULT (SYSDATETIME()),
        UpdatedAt datetime2 NULL
    );
END;
GO

/* Seed placeholders without duplicating entries (case/whitespace insensitive). */
IF NOT EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions WHERE LOWER(LTRIM(RTRIM(Keyword))) = LOWER(N'None Detected'))
    INSERT dbo.call_KeywordExclusions (Keyword, Reason, IsActive)
    VALUES (N'None Detected', N'Placeholder value; not a meaningful keyword', 1);
IF NOT EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions WHERE LOWER(LTRIM(RTRIM(Keyword))) = LOWER(N'No Keyword'))
    INSERT dbo.call_KeywordExclusions (Keyword, Reason, IsActive)
    VALUES (N'No Keyword', N'Placeholder value; not a meaningful keyword', 1);
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetFilterOptions
AS
BEGIN
    SET NOCOUNT ON;

    -- Result set 1: software options
    SELECT DISTINCT LTRIM(RTRIM(software_name)) AS option_value
    FROM dbo.call_summaries
    WHERE NULLIF(LTRIM(RTRIM(software_name)), N'') IS NOT NULL
    ORDER BY option_value;

    -- Result set 2: mood options (normalise blanks/null to Unknown)
    SELECT DISTINCT COALESCE(NULLIF(LTRIM(RTRIM(customer_mood)), N''), N'Unknown') AS option_value
    FROM dbo.call_summaries
    ORDER BY option_value;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetKeywordAnalytics
    @DateFrom date = NULL,
    @DateTo date = NULL,
    @CustomerName nvarchar(200) = NULL,
    @ExecutiveName nvarchar(200) = NULL,
    @SoftwareNamesJson nvarchar(max) = N'[]',
    @MoodsJson nvarchar(max) = N'[]',
    @KeywordsJson nvarchar(max) = N'[]'
AS
BEGIN
    SET NOCOUNT ON;

    IF ISJSON(COALESCE(@SoftwareNamesJson, N'[]')) <> 1 OR
       ISJSON(COALESCE(@MoodsJson, N'[]')) <> 1 OR
       ISJSON(COALESCE(@KeywordsJson, N'[]')) <> 1
    BEGIN
        THROW 50001, 'Filter arrays must be valid JSON arrays.', 1;
    END;

    DECLARE @Software TABLE (value nvarchar(500) PRIMARY KEY);
    DECLARE @Moods TABLE (value nvarchar(200) PRIMARY KEY);
    DECLARE @Keywords TABLE (value nvarchar(200) PRIMARY KEY);

    INSERT INTO @Software(value)
    SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(COALESCE(@SoftwareNamesJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;
    INSERT INTO @Moods(value)
    SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(COALESCE(@MoodsJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;
    INSERT INTO @Keywords(value)
    SELECT DISTINCT LOWER(LTRIM(RTRIM([value]))) FROM OPENJSON(COALESCE(@KeywordsJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;

    -- v1.4: tokens are materialised ONCE into #Tokens (one row per call +
    -- normalised keyword) and every metric aggregates from that table. Using
    -- a CTE here made SQL Server re-expand STRING_SPLIT for every keyword when
    -- joining the per-keyword and per-mood aggregates (minutes, not seconds).
    CREATE TABLE #Tokens
    (
        id int NOT NULL,
        keyword_key nvarchar(400) NOT NULL,
        keyword_label nvarchar(400) NOT NULL,
        customer_mood nvarchar(200) NOT NULL,
        customer_key nvarchar(500) NULL,
        duration_seconds int NULL,
        PRIMARY KEY (keyword_key, id)
    );

    ;WITH Base AS
    (
        SELECT c.id, c.customer_name, c.call_duration, c.keywords,
               COALESCE(NULLIF(LTRIM(RTRIM(c.customer_mood)), N''), N'Unknown') AS customer_mood
        FROM dbo.call_summaries AS c
        WHERE (@DateFrom IS NULL OR c.call_date >= @DateFrom)
          AND (@DateTo IS NULL OR c.call_date < DATEADD(day, 1, @DateTo))
          AND (NULLIF(LTRIM(RTRIM(@CustomerName)), N'') IS NULL OR c.customer_name LIKE N'%' + @CustomerName + N'%')
          AND (NULLIF(LTRIM(RTRIM(@ExecutiveName)), N'') IS NULL OR c.executive_name LIKE N'%' + @ExecutiveName + N'%')
          AND (NOT EXISTS (SELECT 1 FROM @Software) OR EXISTS
               (SELECT 1 FROM @Software s WHERE s.value = LTRIM(RTRIM(c.software_name))))
          AND (NOT EXISTS (SELECT 1 FROM @Moods) OR EXISTS
               (SELECT 1 FROM @Moods m WHERE m.value = COALESCE(NULLIF(LTRIM(RTRIM(c.customer_mood)), N''), N'Unknown')))
          AND (NOT EXISTS (SELECT 1 FROM @Keywords) OR EXISTS
               (SELECT 1 FROM STRING_SPLIT(COALESCE(c.keywords, N''), N',') kt
                JOIN @Keywords k ON k.value = LOWER(LTRIM(RTRIM(kt.[value])))) )
    )
    INSERT INTO #Tokens (id, keyword_key, keyword_label, customer_mood, customer_key, duration_seconds)
    SELECT b.id, LOWER(LTRIM(RTRIM(s.[value]))),
           MAX(LTRIM(RTRIM(s.[value]))),
           MAX(b.customer_mood),
           MAX(NULLIF(LTRIM(RTRIM(b.customer_name)), N'')),
           MAX(CASE WHEN b.call_duration >= 0 THEN b.call_duration END)
    FROM Base b
    CROSS APPLY STRING_SPLIT(COALESCE(b.keywords, N''), N',') s
    WHERE NULLIF(LTRIM(RTRIM(s.[value])), N'') IS NOT NULL
    GROUP BY b.id, LOWER(LTRIM(RTRIM(s.[value])));

    DELETE t FROM #Tokens t
    WHERE EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions e
                  WHERE e.IsActive = 1 AND LOWER(LTRIM(RTRIM(e.Keyword))) = t.keyword_key);

    -- X axis / bubble size / Y axis (v1.4 metric definitions) + mood breakdown.
    SELECT ks.keyword_key AS keywordKey, ks.keyword, mc.mood, mc.callCount, ks.keywordTotal,
           ks.distinctCustomers, ks.averageDurationSeconds
    FROM (SELECT keyword_key, MAX(keyword_label) AS keyword, COUNT(*) AS keywordTotal,
                 COUNT(DISTINCT customer_key) AS distinctCustomers,
                 AVG(CAST(duration_seconds AS decimal(18, 2))) AS averageDurationSeconds
          FROM #Tokens GROUP BY keyword_key) ks
    JOIN (SELECT keyword_key, customer_mood AS mood, COUNT(*) AS callCount
          FROM #Tokens GROUP BY keyword_key, customer_mood) mc ON mc.keyword_key = ks.keyword_key
    ORDER BY ks.keywordTotal DESC, ks.keyword_key, mc.mood;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetKeywordCalls
    @SelectedKeyword nvarchar(200),
    @DateFrom date = NULL,
    @DateTo date = NULL,
    @CustomerName nvarchar(200) = NULL,
    @ExecutiveName nvarchar(200) = NULL,
    @SoftwareNamesJson nvarchar(max) = N'[]',
    @MoodsJson nvarchar(max) = N'[]',
    @KeywordsJson nvarchar(max) = N'[]',
    @LocalMood nvarchar(200) = NULL,
    @PageNumber int = 1,
    @PageSize int = 50,
    @ViewMode varchar(10) = 'compact',
    @ColumnFiltersJson nvarchar(max) = N'{}'
AS
BEGIN
    SET NOCOUNT ON;
    SET @PageNumber = CASE WHEN @PageNumber < 1 THEN 1 ELSE @PageNumber END;
    SET @PageSize = CASE WHEN @PageSize < 1 THEN 50 WHEN @PageSize > 200 THEN 200 ELSE @PageSize END;
    SET @ViewMode = CASE WHEN LOWER(@ViewMode) = 'expanded' THEN 'expanded' ELSE 'compact' END;
    SET @ColumnFiltersJson = COALESCE(NULLIF(LTRIM(RTRIM(@ColumnFiltersJson)), N''), N'{}');

    IF ISJSON(@ColumnFiltersJson) <> 1
        THROW 50001, 'Column filters must be a valid JSON object.', 1;

    CREATE TABLE #KeywordCalls
    (
        ticketNo int NOT NULL PRIMARY KEY,
        callDate datetime2 NULL,
        customerName nvarchar(500) NULL,
        executiveName nvarchar(500) NULL,
        customerMood nvarchar(200) NOT NULL,
        softwareName nvarchar(500) NULL,
        callDurationSeconds int NULL
    );
    EXEC dbo.usp_Sentimeter_FillKeywordCalls @SelectedKeyword, @DateFrom, @DateTo, @CustomerName, @ExecutiveName,
         @SoftwareNamesJson, @MoodsJson, @KeywordsJson;

    -- v1.4 per-column (Excel-style) filters. Each field maps to a fixed
    -- expression; values within a column are ORed, columns are ANDed.
    DECLARE @fTicket TABLE (v int PRIMARY KEY);
    DECLARE @fDate TABLE (v date PRIMARY KEY);
    DECLARE @fCust TABLE (v nvarchar(500) PRIMARY KEY);
    DECLARE @fExec TABLE (v nvarchar(500) PRIMARY KEY);
    DECLARE @fMood TABLE (v nvarchar(200) PRIMARY KEY);
    DECLARE @fSoft TABLE (v nvarchar(500) PRIMARY KEY);

    INSERT INTO @fTicket SELECT DISTINCT TRY_CAST([value] AS int) FROM OPENJSON(@ColumnFiltersJson, '$.ticketNo')
        WHERE TRY_CAST([value] AS int) IS NOT NULL;
    INSERT INTO @fDate SELECT DISTINCT TRY_CAST([value] AS date) FROM OPENJSON(@ColumnFiltersJson, '$.callDate.selectedDates')
        WHERE TRY_CAST([value] AS date) IS NOT NULL;
    INSERT INTO @fCust SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(@ColumnFiltersJson, '$.customerName.values');
    INSERT INTO @fExec SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(@ColumnFiltersJson, '$.executiveName.values');
    INSERT INTO @fMood SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(@ColumnFiltersJson, '$.customerMood.values');
    INSERT INTO @fSoft SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(@ColumnFiltersJson, '$.softwareName.values');

    DECLARE @cfDateFrom date = TRY_CAST(JSON_VALUE(@ColumnFiltersJson, '$.callDate.from') AS date),
            @cfDateTo date = TRY_CAST(JSON_VALUE(@ColumnFiltersJson, '$.callDate.to') AS date),
            @custLike nvarchar(200) = NULLIF(JSON_VALUE(@ColumnFiltersJson, '$.customerName.contains'), N''),
            @execLike nvarchar(200) = NULLIF(JSON_VALUE(@ColumnFiltersJson, '$.executiveName.contains'), N''),
            @softLike nvarchar(200) = NULLIF(JSON_VALUE(@ColumnFiltersJson, '$.softwareName.contains'), N''),
            @durOp varchar(10) = JSON_VALUE(@ColumnFiltersJson, '$.callDurationSeconds.operator'),
            @durV int = TRY_CAST(JSON_VALUE(@ColumnFiltersJson, '$.callDurationSeconds.value') AS int),
            @durV2 int = TRY_CAST(JSON_VALUE(@ColumnFiltersJson, '$.callDurationSeconds.valueTo') AS int);

    IF @durOp IS NOT NULL AND (@durOp NOT IN ('eq', 'gt', 'gte', 'lt', 'lte', 'between') OR @durV IS NULL
                               OR (@durOp = 'between' AND @durV2 IS NULL))
        THROW 50001, 'Invalid call duration filter.', 1;

    -- Result set 1: mood counts before the local mood pill AND before column filters.
    SELECT customerMood AS mood, COUNT_BIG(*) AS callCount
    FROM #KeywordCalls
    GROUP BY customerMood
    ORDER BY customerMood;

    -- Result set 2: paged call rows. Expanded mode intentionally returns the same metadata;
    -- summaries/transcripts are never included here and are fetched only from the content endpoint.
    -- Local mood and column filters apply before paging and the total count.
    SELECT ticketNo, callDate, customerName, executiveName, customerMood, softwareName,
           callDurationSeconds,
           CASE
             WHEN callDurationSeconds IS NULL OR callDurationSeconds < 0 THEN NULL
             ELSE (CASE WHEN callDurationSeconds / 60 < 10 THEN '0' ELSE '' END)
                  + CONVERT(varchar(12), callDurationSeconds / 60)
                  + ':' + RIGHT('0' + CONVERT(varchar(2), callDurationSeconds % 60), 2)
           END AS callDurationDisplay,
           COUNT_BIG(*) OVER() AS totalItems,
           @ViewMode AS viewMode
    FROM #KeywordCalls
    WHERE (NULLIF(LTRIM(RTRIM(@LocalMood)), N'') IS NULL OR customerMood = @LocalMood)
      AND (NOT EXISTS (SELECT 1 FROM @fTicket) OR ticketNo IN (SELECT v FROM @fTicket))
      AND (NOT EXISTS (SELECT 1 FROM @fDate) OR CAST(callDate AS date) IN (SELECT v FROM @fDate))
      AND (@cfDateFrom IS NULL OR callDate >= @cfDateFrom)
      AND (@cfDateTo IS NULL OR callDate < DATEADD(day, 1, @cfDateTo))
      AND (NOT EXISTS (SELECT 1 FROM @fCust) OR COALESCE(LTRIM(RTRIM(customerName)), N'') IN (SELECT v FROM @fCust))
      AND (@custLike IS NULL OR customerName LIKE N'%' + @custLike + N'%')
      AND (NOT EXISTS (SELECT 1 FROM @fExec) OR COALESCE(LTRIM(RTRIM(executiveName)), N'') IN (SELECT v FROM @fExec))
      AND (@execLike IS NULL OR executiveName LIKE N'%' + @execLike + N'%')
      AND (NOT EXISTS (SELECT 1 FROM @fMood) OR customerMood IN (SELECT v FROM @fMood))
      AND (NOT EXISTS (SELECT 1 FROM @fSoft) OR COALESCE(LTRIM(RTRIM(softwareName)), N'') IN (SELECT v FROM @fSoft))
      AND (@softLike IS NULL OR softwareName LIKE N'%' + @softLike + N'%')
      AND (@durOp IS NULL OR (callDurationSeconds IS NOT NULL AND (
               (@durOp = 'eq' AND callDurationSeconds = @durV)
            OR (@durOp = 'gt' AND callDurationSeconds > @durV)
            OR (@durOp = 'gte' AND callDurationSeconds >= @durV)
            OR (@durOp = 'lt' AND callDurationSeconds < @durV)
            OR (@durOp = 'lte' AND callDurationSeconds <= @durV)
            OR (@durOp = 'between' AND callDurationSeconds BETWEEN @durV AND @durV2))))
    ORDER BY callDate DESC, ticketNo DESC
    OFFSET ((@PageNumber - 1) * @PageSize) ROWS FETCH NEXT @PageSize ROWS ONLY;
END;
GO

/* v1.4: distinct values for one expanded-list column, scoped to the selected
   keyword + global filters (and active exclusions) — feeds the Excel-style
   header checkbox menus. Bounded by @MaxValues and searchable for
   high-cardinality columns; totalValues is the full distinct count. */
CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetKeywordCallFilterOptions
    @SelectedKeyword nvarchar(200),
    @DateFrom date = NULL,
    @DateTo date = NULL,
    @CustomerName nvarchar(200) = NULL,
    @ExecutiveName nvarchar(200) = NULL,
    @SoftwareNamesJson nvarchar(max) = N'[]',
    @MoodsJson nvarchar(max) = N'[]',
    @KeywordsJson nvarchar(max) = N'[]',
    @Field varchar(30),
    @Search nvarchar(200) = NULL,
    @MaxValues int = 200
AS
BEGIN
    SET NOCOUNT ON;
    SET @MaxValues = CASE WHEN @MaxValues < 1 THEN 200 WHEN @MaxValues > 1000 THEN 1000 ELSE @MaxValues END;
    SET @Search = NULLIF(LTRIM(RTRIM(@Search)), N'');
    IF @Field NOT IN ('ticketNo', 'callDate', 'customerName', 'executiveName', 'customerMood', 'softwareName')
        THROW 50001, 'Invalid filter field.', 1;

    CREATE TABLE #KeywordCalls
    (
        ticketNo int NOT NULL PRIMARY KEY,
        callDate datetime2 NULL,
        customerName nvarchar(500) NULL,
        executiveName nvarchar(500) NULL,
        customerMood nvarchar(200) NOT NULL,
        softwareName nvarchar(500) NULL,
        callDurationSeconds int NULL
    );
    EXEC dbo.usp_Sentimeter_FillKeywordCalls @SelectedKeyword, @DateFrom, @DateTo, @CustomerName, @ExecutiveName,
         @SoftwareNamesJson, @MoodsJson, @KeywordsJson;

    ;WITH V AS
    (
        SELECT CASE @Field
                 WHEN 'ticketNo' THEN CONVERT(nvarchar(500), ticketNo)
                 WHEN 'callDate' THEN CONVERT(nvarchar(500), CONVERT(char(10), CAST(callDate AS date), 23))
                 WHEN 'customerName' THEN COALESCE(LTRIM(RTRIM(customerName)), N'')
                 WHEN 'executiveName' THEN COALESCE(LTRIM(RTRIM(executiveName)), N'')
                 WHEN 'customerMood' THEN customerMood
                 WHEN 'softwareName' THEN COALESCE(LTRIM(RTRIM(softwareName)), N'')
               END AS optionValue,
               CASE @Field WHEN 'ticketNo' THEN ticketNo END AS sortNumber
        FROM #KeywordCalls
        WHERE @Field <> 'callDate' OR callDate IS NOT NULL
    ), G AS
    (
        SELECT optionValue, MAX(sortNumber) AS sortNumber, COUNT_BIG(*) AS callCount
        FROM V
        WHERE @Search IS NULL OR optionValue LIKE N'%' + @Search + N'%'
        GROUP BY optionValue
    )
    SELECT TOP (@MaxValues) optionValue, callCount, COUNT_BIG(*) OVER() AS totalValues
    FROM G
    ORDER BY CASE WHEN @Field = 'callDate' THEN optionValue END DESC,
             sortNumber DESC,
             optionValue;
END;
GO

/* Shared row source for the keyword-call procedures: fills the CALLER's
   #KeywordCalls with every call containing @SelectedKeyword under the global
   filters (and active exclusions). Must exist before the callers run. */
CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_FillKeywordCalls
    @SelectedKeyword nvarchar(200),
    @DateFrom date = NULL,
    @DateTo date = NULL,
    @CustomerName nvarchar(200) = NULL,
    @ExecutiveName nvarchar(200) = NULL,
    @SoftwareNamesJson nvarchar(max) = N'[]',
    @MoodsJson nvarchar(max) = N'[]',
    @KeywordsJson nvarchar(max) = N'[]'
AS
BEGIN
    SET NOCOUNT ON;
    IF OBJECT_ID('tempdb..#KeywordCalls') IS NULL
        THROW 50001, 'Caller must create #KeywordCalls.', 1;
    IF NULLIF(LTRIM(RTRIM(@SelectedKeyword)), N'') IS NULL
        THROW 50002, 'SelectedKeyword is required.', 1;
    IF ISJSON(COALESCE(@SoftwareNamesJson, N'[]')) <> 1 OR
       ISJSON(COALESCE(@MoodsJson, N'[]')) <> 1 OR
       ISJSON(COALESCE(@KeywordsJson, N'[]')) <> 1
        THROW 50001, 'Filter arrays must be valid JSON arrays.', 1;

    DECLARE @Software TABLE (value nvarchar(500) PRIMARY KEY);
    DECLARE @Moods TABLE (value nvarchar(200) PRIMARY KEY);
    DECLARE @Keywords TABLE (value nvarchar(200) PRIMARY KEY);

    INSERT INTO @Software(value)
    SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(COALESCE(@SoftwareNamesJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;
    INSERT INTO @Moods(value)
    SELECT DISTINCT LTRIM(RTRIM([value])) FROM OPENJSON(COALESCE(@MoodsJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;
    INSERT INTO @Keywords(value)
    SELECT DISTINCT LOWER(LTRIM(RTRIM([value]))) FROM OPENJSON(COALESCE(@KeywordsJson, N'[]'))
    WHERE NULLIF(LTRIM(RTRIM([value])), N'') IS NOT NULL;

    INSERT INTO #KeywordCalls(ticketNo, callDate, customerName, executiveName, customerMood, softwareName, callDurationSeconds)
    SELECT DISTINCT c.id, c.call_date, c.customer_name, c.executive_name,
           COALESCE(NULLIF(LTRIM(RTRIM(c.customer_mood)), N''), N'Unknown'),
           c.software_name, c.call_duration
    FROM dbo.call_summaries c
    WHERE (@DateFrom IS NULL OR c.call_date >= @DateFrom)
      AND (@DateTo IS NULL OR c.call_date < DATEADD(day, 1, @DateTo))
      AND (NULLIF(LTRIM(RTRIM(@CustomerName)), N'') IS NULL OR c.customer_name LIKE N'%' + @CustomerName + N'%')
      AND (NULLIF(LTRIM(RTRIM(@ExecutiveName)), N'') IS NULL OR c.executive_name LIKE N'%' + @ExecutiveName + N'%')
      AND (NOT EXISTS (SELECT 1 FROM @Software) OR EXISTS
           (SELECT 1 FROM @Software s WHERE s.value = LTRIM(RTRIM(c.software_name))))
      AND (NOT EXISTS (SELECT 1 FROM @Moods) OR EXISTS
           (SELECT 1 FROM @Moods m WHERE m.value = COALESCE(NULLIF(LTRIM(RTRIM(c.customer_mood)), N''), N'Unknown')))
      AND (NOT EXISTS (SELECT 1 FROM @Keywords) OR EXISTS
           (SELECT 1 FROM STRING_SPLIT(COALESCE(c.keywords, N''), N',') kt
            JOIN @Keywords k ON k.value = LOWER(LTRIM(RTRIM(kt.[value])))))
      AND NOT EXISTS
          (SELECT 1 FROM dbo.call_KeywordExclusions e
           WHERE e.IsActive = 1
             AND LOWER(LTRIM(RTRIM(e.Keyword))) = LOWER(LTRIM(RTRIM(@SelectedKeyword))))
      AND EXISTS
          (SELECT 1 FROM STRING_SPLIT(COALESCE(c.keywords, N''), N',') kw
           WHERE LOWER(LTRIM(RTRIM(kw.[value]))) = LOWER(LTRIM(RTRIM(@SelectedKeyword))));
END;
GO


CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetKeywordExclusions
    @IncludeInactive bit = 1
AS
BEGIN
    SET NOCOUNT ON;
    SELECT ExclusionId AS exclusionId, Keyword AS keyword, Reason AS reason,
           IsActive AS isActive, CreatedAt AS createdAt, UpdatedAt AS updatedAt
    FROM dbo.call_KeywordExclusions
    WHERE @IncludeInactive = 1 OR IsActive = 1
    ORDER BY LOWER(LTRIM(RTRIM(Keyword))), ExclusionId;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_AddKeywordExclusion
    @Keyword nvarchar(200),
    @Reason nvarchar(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET @Keyword = LTRIM(RTRIM(@Keyword));
    SET @Reason = NULLIF(LTRIM(RTRIM(@Reason)), N'');
    IF NULLIF(@Keyword, N'') IS NULL
        THROW 50010, 'Keyword is required.', 1;

    BEGIN TRANSACTION;
    IF EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions WITH (UPDLOCK, HOLDLOCK)
               WHERE LOWER(LTRIM(RTRIM(Keyword))) = LOWER(@Keyword))
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 50011, 'An exclusion with this keyword already exists.', 1;
    END;
    INSERT dbo.call_KeywordExclusions (Keyword, Reason, IsActive) VALUES (@Keyword, @Reason, 1);
    DECLARE @NewId int = CONVERT(int, SCOPE_IDENTITY());
    COMMIT TRANSACTION;
    SELECT ExclusionId AS exclusionId, Keyword AS keyword, Reason AS reason,
           IsActive AS isActive, CreatedAt AS createdAt, UpdatedAt AS updatedAt
    FROM dbo.call_KeywordExclusions WHERE ExclusionId = @NewId;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_UpdateKeywordExclusion
    @ExclusionId int,
    @Keyword nvarchar(200),
    @Reason nvarchar(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET @Keyword = LTRIM(RTRIM(@Keyword));
    SET @Reason = NULLIF(LTRIM(RTRIM(@Reason)), N'');
    IF @ExclusionId <= 0 OR NULLIF(@Keyword, N'') IS NULL
        THROW 50010, 'A valid exclusion ID and keyword are required.', 1;
    BEGIN TRANSACTION;
    IF NOT EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions WITH (UPDLOCK, HOLDLOCK) WHERE ExclusionId = @ExclusionId)
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 50012, 'Keyword exclusion was not found.', 1;
    END;
    IF EXISTS (SELECT 1 FROM dbo.call_KeywordExclusions WITH (UPDLOCK, HOLDLOCK)
               WHERE ExclusionId <> @ExclusionId AND LOWER(LTRIM(RTRIM(Keyword))) = LOWER(@Keyword))
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 50011, 'An exclusion with this keyword already exists.', 1;
    END;
    UPDATE dbo.call_KeywordExclusions
       SET Keyword = @Keyword, Reason = @Reason, UpdatedAt = SYSDATETIME()
     WHERE ExclusionId = @ExclusionId;
    COMMIT TRANSACTION;
    SELECT ExclusionId AS exclusionId, Keyword AS keyword, Reason AS reason,
           IsActive AS isActive, CreatedAt AS createdAt, UpdatedAt AS updatedAt
    FROM dbo.call_KeywordExclusions WHERE ExclusionId = @ExclusionId;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_SetKeywordExclusionStatus
    @ExclusionId int,
    @IsActive bit
AS
BEGIN
    SET NOCOUNT ON;
    IF @ExclusionId <= 0
        THROW 50010, 'A valid exclusion ID is required.', 1;
    UPDATE dbo.call_KeywordExclusions
       SET IsActive = @IsActive, UpdatedAt = SYSDATETIME()
     WHERE ExclusionId = @ExclusionId;
    IF @@ROWCOUNT = 0
        THROW 50012, 'Keyword exclusion was not found.', 1;
    SELECT ExclusionId AS exclusionId, Keyword AS keyword, Reason AS reason,
           IsActive AS isActive, CreatedAt AS createdAt, UpdatedAt AS updatedAt
    FROM dbo.call_KeywordExclusions WHERE ExclusionId = @ExclusionId;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_Sentimeter_GetCallContent
    @TicketNo int,
    @ContentType varchar(20)
AS
BEGIN
    SET NOCOUNT ON;

    IF @ContentType NOT IN ('shortSummary', 'longSummary', 'transcript')
        THROW 50003, 'Invalid ContentType. Allowed values: shortSummary, longSummary, transcript.', 1;

    -- A missing ticket returns zero rows. A ticket with a NULL content value returns a row with NULL content.
    SELECT c.id AS ticketNo,
           @ContentType AS contentType,
           CASE @ContentType
             WHEN 'shortSummary' THEN d.short_summary
             WHEN 'longSummary' THEN d.long_summary
             WHEN 'transcript' THEN d.transcript
           END AS content
    FROM dbo.call_summaries c
    LEFT JOIN dbo.call_summary_details d ON d.call_id = c.id
    WHERE c.id = @TicketNo;
END;
GO

/*
Suggested API-to-procedure mapping:
  GET  /api/v1/filters/options       -> dbo.usp_Sentimeter_GetFilterOptions
  GET  /api/v1/admin/keyword-exclusions -> dbo.usp_Sentimeter_GetKeywordExclusions
  POST /api/v1/admin/keyword-exclusions -> dbo.usp_Sentimeter_AddKeywordExclusion
  PUT  /api/v1/admin/keyword-exclusions/{exclusionId} -> dbo.usp_Sentimeter_UpdateKeywordExclusion
  PATCH /api/v1/admin/keyword-exclusions/{exclusionId}/status -> dbo.usp_Sentimeter_SetKeywordExclusionStatus
  POST /api/v1/analytics/keywords    -> dbo.usp_Sentimeter_GetKeywordAnalytics
  POST /api/v1/analytics/keyword-calls -> dbo.usp_Sentimeter_GetKeywordCalls @ViewMode='compact'
  POST /api/v1/analytics/keyword-calls/expanded -> dbo.usp_Sentimeter_GetKeywordCalls @ViewMode='expanded' (+ @ColumnFiltersJson)
  POST /support/sentimeter/analytics/keyword-calls/filter-options -> dbo.usp_Sentimeter_GetKeywordCallFilterOptions

  Call duration contract: call_duration is stored in seconds. The keyword-calls procedure returns
  callDurationSeconds (raw seconds) and callDurationDisplay (MM:SS, e.g. 272 -> 04:32).
  Minutes are at least two digits and are not truncated above 99 minutes. Null/negative durations
  return NULL for callDurationDisplay. The Angular UI displays callDurationDisplay.
  POST /api/v1/calls/content         -> dbo.usp_Sentimeter_GetCallContent

Security note: grant EXECUTE on analytics/content procedures to the read-only analytics identity. Grant write-procedure execution for keyword exclusion management only to an authorised administrator identity; restrict direct table DML and only the minimum required permissions on underlying tables. Validate the SQL Server ownership chain and permissions
in your environment. Do not use a db_owner account for the application.
*/
