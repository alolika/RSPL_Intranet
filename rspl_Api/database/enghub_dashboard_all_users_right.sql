-- Engineering Hub: "Dashboard: View All Developers" right (MenuCode
-- DASHBOARD_ALL_USERS, assignable on the Menu Rights page). Users with it can
-- pick any developer on the Dashboard; everyone else sees only their own.
-- Initially granted to 24 (Navnath Lanke) and 142 (Maruti Sangle).
-- Idempotent; safe to re-run. Run on production at deploy (after
-- enghub_menu_rights.sql, which creates the table).
INSERT INTO dbo.EngHub_UserMenuRights (UserId, MenuCode, GrantedByUserId)
SELECT v.UserId, 'DASHBOARD_ALL_USERS', 142
FROM (VALUES (24), (142)) AS v(UserId)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.EngHub_UserMenuRights r WHERE r.UserId = v.UserId AND r.MenuCode = 'DASHBOARD_ALL_USERS'
);
GO
