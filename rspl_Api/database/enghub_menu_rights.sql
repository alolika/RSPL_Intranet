----------------------------------------------------------------------
-- Engineering Hub - per-user menu rights (see app/routers/enghub_menu_rights.py).
--
-- One row = that user may see/open that Engineering Hub menu. No row = the
-- menu is hidden and its pages are blocked. Rights admins (app.rights.
-- RIGHTS_ADMIN_USER_IDS) always see every menu and don't need rows here.
--
-- Idempotent: safe to run more than once.
----------------------------------------------------------------------

IF OBJECT_ID('dbo.EngHub_UserMenuRights', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EngHub_UserMenuRights (
        UserId           INT          NOT NULL,
        MenuCode         VARCHAR(50)  NOT NULL,
        GrantedByUserId  INT          NOT NULL,
        GrantedAt        DATETIME2(0) NOT NULL CONSTRAINT DF_EngHub_UserMenuRights_GrantedAt DEFAULT SYSDATETIME(),
        CONSTRAINT PK_EngHub_UserMenuRights PRIMARY KEY (UserId, MenuCode)
    );
    PRINT 'Created dbo.EngHub_UserMenuRights';
END
ELSE
    PRINT 'dbo.EngHub_UserMenuRights already exists - skipped';
GO

----------------------------------------------------------------------
-- Initial rights (2026-09-30). Idempotent - only inserts missing rows.
--  * Menu Rights admins 24 (Navnath Lanke) and 142 (Maruti Sangle): all 9 menus.
--  * Every other enabled user: all menus EXCEPT Masters / Features /
--    Development Items (those 3 are admin-only, enforced in the API too).
----------------------------------------------------------------------

;WITH Menus AS (
    SELECT MenuCode FROM (VALUES ('DASHBOARD'),('FEATURES'),('DEV_ITEMS'),('TASKS'),('ACTIVITIES'),
                                 ('DECISIONS'),('RELEASES'),('REPORTS'),('MASTERS')) m(MenuCode)
)
INSERT INTO dbo.EngHub_UserMenuRights (UserId, MenuCode, GrantedByUserId)
SELECT u.UserID, m.MenuCode, 142
FROM dbo.UserMaster u
CROSS JOIN Menus m
WHERE u.Enabled = 1
  AND (u.UserID IN (24, 142) OR m.MenuCode NOT IN ('MASTERS', 'FEATURES', 'DEV_ITEMS'))
  AND NOT EXISTS (SELECT 1 FROM dbo.EngHub_UserMenuRights r WHERE r.UserId = u.UserID AND r.MenuCode = m.MenuCode);
GO
