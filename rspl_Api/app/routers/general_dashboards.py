"""Mirrors App_Master/Home.master.vb's Page_Init, which builds the "Dashboard"
top-level menu dynamically from `rspl_ReportDashboardTypeMAster` (WHERE
ApplicationType='Web' AND enabled=1) and routes each child item to
Section_General/WebDashboard.aspx?gReportID={ReportTypeID}, which in turn
reads that ReportTypeID's tiles from `RSPL_ReportDashboard`. Confirmed live
this dynamic menu was never replicated in the Angular migration — only the
one static Dashboard.aspx.vb menu item (AMCCo-OrdinatorDashBoard) made it
across. Three ReportTypeIDs are enabled: 1 "Support Co-Ordinator DashBoard"
(same 8 tiles, byte-identical Report_Query proc names, as the already-wired
`RSPL_SupportCoOrdinatorDashboard`-backed /support/coordinator-dashboard —
no new endpoint needed for it), 2 "Sales Team DashBoard", 5 "AMC Dashboard"
(distinct from AMCCo-OrdinatorDashBoard.aspx, which is a separate feature).
This router covers the two genuinely new ones.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/general/dashboards", tags=["general-dashboards"])


class DashboardTile(BaseModel):
    report_id: int
    title: str
    rows: list[dict]


class SoftwareClientCount(BaseModel):
    software_name: str
    total_count: int


class HolidayListRow(BaseModel):
    holiday_date: str
    day: str
    event: str


class HolidayListTitle(BaseModel):
    name: str


def _run_tiles(report_type_id: int, current_user: CurrentUser) -> list[DashboardTile]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT Report_ID, Report_Title, Report_Query FROM RSPL_ReportDashboard "
            "WHERE ReportTypeID = ? AND Report_Enabled = 1 ORDER BY Report_ID",
            report_type_id,
        )
        tiles = rows_to_dicts(cursor)

    result = []
    for t in tiles:
        proc = (t.get("Report_Query") or "").strip()
        rows: list[dict] = []
        if proc:
            try:
                with get_cursor() as cursor:
                    if proc == "RSPL_AMCDashBoard_Analysis":
                        # Only proc among these six that takes a parameter
                        # (@userid) — confirmed via INFORMATION_SCHEMA.PARAMETERS.
                        cursor.execute(f"EXEC {proc} @userid = ?", current_user.user_id)
                    else:
                        cursor.execute(f"EXEC {proc}")
                    # RSPL_SalesDashborad_OpenLeads confirmed live at 60,546
                    # unbounded rows (no TOP/ORDER BY inside the proc to lean
                    # on) — same "unfiltered report over a large table" issue
                    # already capped elsewhere in this app (VisitLogReport,
                    # CustLedger, the AMC coordinator "AMC" tab), but capping
                    # via rows[:1000] *after* rows_to_dicts's fetchall() still
                    # pulled all 60k rows over the wire first (~7s of the
                    # ~10.5s total). fetchmany(1000) via the `limit` param
                    # stops the driver from fetching the rest at all.
                    rows = rows_to_dicts(cursor, limit=1000 if proc == "RSPL_SalesDashborad_OpenLeads" else None)
            except Exception:
                # Matches the established pattern for this exact family of
                # dashboard tiles (see support_modules.py's coordinator-
                # dashboard): one failing/blocked tile degrades to empty
                # instead of taking down the rest of the dashboard.
                rows = []
        result.append(DashboardTile(report_id=t["Report_ID"], title=t["Report_Title"] or "", rows=rows))
    return result


@router.get("/sales-team", response_model=list[DashboardTile])
def get_sales_team_dashboard(current_user: CurrentUser = Depends(get_current_user)) -> list[DashboardTile]:
    return _run_tiles(2, current_user)


@router.get("/amc", response_model=list[DashboardTile])
def get_amc_dashboard(current_user: CurrentUser = Depends(get_current_user)) -> list[DashboardTile]:
    return _run_tiles(5, current_user)


# Home.master's own DashBoard.aspx.vb (the main post-login landing page, not
# any of the ReportTypeID-driven ones above) has its own separate
# FillStatistic -> "EXEC webproc_softwarecusttotal" call for the
# "Software Client Count" tile. Confirmed live: 11 real software rows
# (RetailwarePro 1967, Retailware 1877, Jewelsoft 668, BillKwik 524, etc.) --
# the Angular DashboardDataService was still a pure hardcoded mock (3
# software names, all totalCount: 0) that had never been wired to this proc.
@router.get("/software-client-count", response_model=list[SoftwareClientCount])
def get_software_client_count() -> list[SoftwareClientCount]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_softwarecusttotal")
        rows = rows_to_dicts(cursor)
    return [SoftwareClientCount(software_name=r["Software"] or "", total_count=r["Total"] or 0) for r in rows]


# Main dashboard's "Holiday List" tile, now backed by the new
# RSPL_HolidayMaster table managed via the Accounts > Holiday Master admin
# page (see admin_holiday.py) instead of the hand-edited static HTML the
# legacy DashBoard.aspx used. Only Enabled=1 rows show here -- soft-deleted
# holidays stay in the admin page's history but drop off the dashboard.
@router.get("/holiday-list", response_model=list[HolidayListRow])
def get_holiday_list() -> list[HolidayListRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT HolidayDate, Day, Event FROM RSPL_HolidayMaster WHERE Enabled = 1 ORDER BY HolidayDate"
        )
        rows = rows_to_dicts(cursor)
    return [
        HolidayListRow(holiday_date=r["HolidayDate"].strftime("%Y-%m-%d"), day=r["Day"] or "", event=r["Event"] or "")
        for r in rows
    ]


# The dashboard card's title text -- editable via Accounts > Holiday Master
# (see admin_holiday.py's get_holiday_list_title/set_holiday_list_title,
# backed by RSPL_HolidayMasterSettings) instead of hardcoded in the Angular
# component.
@router.get("/holiday-list-title", response_model=HolidayListTitle)
def get_holiday_list_title() -> HolidayListTitle:
    with get_cursor() as cursor:
        cursor.execute("SELECT Name FROM RSPL_HolidayMasterSettings WHERE Id = 1")
        row = cursor.fetchone()
    return HolidayListTitle(name=row[0] if row else "Holiday List")
