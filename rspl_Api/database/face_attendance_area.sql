-- Face Attendance: area (suburb/locality) name for check-in and check-out location.
-- Safe to re-run. Run once on production at deploy.

IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='FaceAttendance' AND COLUMN_NAME='InArea')
    ALTER TABLE FaceAttendance ADD InArea NVARCHAR(150) NULL;

IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='FaceAttendance' AND COLUMN_NAME='OutArea')
    ALTER TABLE FaceAttendance ADD OutArea NVARCHAR(150) NULL;
