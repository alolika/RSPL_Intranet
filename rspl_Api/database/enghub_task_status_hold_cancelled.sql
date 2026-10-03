-- Adds "Hold" (pending) and "Cancelled" (closed/terminal) Task statuses.
-- Idempotent; safe to re-run. Run on production at deploy.
INSERT INTO dbo.EngHub_Status (EntityType, Name, SortOrder, IsTerminal, CreatedByUserId)
SELECT v.EntityType, v.Name, v.SortOrder, v.IsTerminal, 1
FROM (VALUES
    ('Task', 'Hold',      5, 0),
    ('Task', 'Cancelled', 6, 1)
) AS v(EntityType, Name, SortOrder, IsTerminal)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.EngHub_Status s WHERE s.EntityType = v.EntityType AND s.Name = v.Name
);
GO
