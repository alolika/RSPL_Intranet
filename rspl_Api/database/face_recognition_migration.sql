-- Face Recognition Migration
-- Run once against worldnettech database
-- Adds face enrollment columns to UserMaster and creates FaceAttendance table.

-- Step 1: Add face columns to UserMaster (safe to re-run; columns already exist will be skipped)
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='UserMaster' AND COLUMN_NAME='FaceEmbedding')
    ALTER TABLE UserMaster ADD FaceEmbedding NVARCHAR(MAX) NULL;

IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='UserMaster' AND COLUMN_NAME='FacePhotoFile')
    ALTER TABLE UserMaster ADD FacePhotoFile NVARCHAR(255) NULL;

IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='UserMaster' AND COLUMN_NAME='FaceRegisteredAt')
    ALTER TABLE UserMaster ADD FaceRegisteredAt NVARCHAR(50) NULL;

-- Step 2: Create FaceAttendance table
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='FaceAttendance')
    CREATE TABLE FaceAttendance (
        UserID          INT            NOT NULL,
        AttendanceDate  DATE           NOT NULL,
        ArrivalTime     NVARCHAR(30)   NOT NULL,
        Confidence      FLOAT          NULL,
        Lat             FLOAT          NULL,
        Lng             FLOAT          NULL,
        AccuracyM       FLOAT          NULL,
        City            NVARCHAR(100)  NULL,
        [State]         NVARCHAR(100)  NULL,
        Country         NVARCHAR(100)  NULL,
        CreationDate    DATETIME       NOT NULL DEFAULT GETDATE(),
        CONSTRAINT PK_FaceAttendance PRIMARY KEY (UserID, AttendanceDate)
    );
