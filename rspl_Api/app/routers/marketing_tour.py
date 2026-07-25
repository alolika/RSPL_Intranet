"""Mirrors Section_Marketing/TourDetails.aspx.vb and TourReport.aspx.vb.

TourReport's source has an elaborate dynamic "Choose Columns" /
reorder-and-persist-to-ReportOptions feature (hundreds of lines of
column-shuffling code) — per MIGRATION_CHECKLIST.md this was deliberately
not replicated in the Angular rebuild ("always shows all columns"), so this
endpoint only implements the source's Case-1 "select all columns" query
path. VisitType/ModeOfTravel filter options have no dedicated lookup table
in the source (the two RadComboBoxes are static markup, not DB-filled) —
sourced here from the distinct values actually present in rspl_tourdata.
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing/tour", tags=["marketing-tour"])


class LookupOption(BaseModel):
    label: str
    value: int


class TourCityRow(BaseModel):
    city: str
    tour_count: int


class TourReportRow(BaseModel):
    user_name: str
    visit_type: str
    mode_of_travel: str
    city: str
    customer: str
    date: str | None
    payment_voucher_no: str
    month: str
    amount: float


@router.get("/salesmen", response_model=list[LookupOption])
def get_tour_salesmen() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("Select SalesmanID,Name from Salesman Where Enabled=1 Order by Name")
        return [LookupOption(label=r["Name"], value=r["SalesmanID"]) for r in rows_to_dicts(cursor)]


@router.get("/city-details", response_model=list[TourCityRow])
def get_tour_city_details(from_date: date, to_date: date, salesman_name: str = "") -> list[TourCityRow]:
    # WebProc_GetCityWiseTourdetails (confirmed via OBJECT_DEFINITION) requires an
    # EXACT match — `s.TagValue = @SalesmanName` — with no branch for "any salesman".
    # The frontend's Salesman filter defaults to "(any)", which sends an empty
    # string here; that matches zero rows in the proc every single time, regardless
    # of the date range, so the report always showed "Total: 0" for the single most
    # common filter combination. Verified live: the exact same date range returns
    # real rows once a real salesman name is passed. Can't alter the production
    # proc, so the "any salesman" case runs an equivalent direct query instead —
    # same join/grouping the proc itself uses — with that one restrictive filter
    # simply omitted, rather than trying to pass a value that could ever match it.
    with get_cursor() as cursor:
        if salesman_name.strip():
            cursor.execute("EXEC WebProc_GetCityWiseTourdetails ?, ?, ?", from_date, to_date, salesman_name)
        else:
            cursor.execute(
                "SELECT C.TagValue AS City, COUNT(*) AS TourCount "
                "FROM vwWeb_CityTourCnt S "
                "INNER JOIN vwWeb_CityTourCnt C ON S.PayRegNo = C.PayRegNo AND C.TagTypeId = 7 "
                "WHERE S.TagTypeId = 11 AND S.Date BETWEEN ? AND ? "
                "GROUP BY C.TagValue",
                from_date,
                to_date,
            )
        rows = rows_to_dicts(cursor)
    result = [TourCityRow(city=r["City"] or "", tour_count=r["TourCount"] or 0) for r in rows]
    total = sum(r.tour_count for r in result)
    result.append(TourCityRow(city="Total", tour_count=total))
    return result


@router.get("/report/filters")
def get_tour_report_filters() -> dict:
    # Customer alone was an unfiltered `SELECT DISTINCT DisplayName ... WHERE
    # disabled=0` over CustomerMaster — 80,902 rows, ~1.6s just for the query,
    # loaded into a plain client-side-filtered <p-select> on every page open.
    # Capped to top 100 here like every other lookup in the app; re-searched
    # by name as the user types via /report/customers below.
    with get_cursor() as cursor:
        cursor.execute(
            "select CityName as Name,CityID as Id from geo_CityMaster CM inner join rspl_tourdata RT "
            "on CM.CityName=RT.City group by CM.CityName,CM.CityID Order by cm.CityName"
        )
        cities = [{"label": "All", "value": 0}] + [{"label": r["Name"], "value": r["Id"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "select UM.Name as Name,Um.UserID as ID from UserMaster UM inner join rspl_tourdata RT "
            "on Um.name=rt.username where Enabled=1 group by UM.Name,UM.UserID Order by Name"
        )
        users = [{"label": "All", "value": 0}] + [{"label": r["Name"], "value": r["ID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("select distinct top 100 Displayname as Name,CustID as ID from CustomerMaster where disabled=0 Order by Displayname")
        customers = [{"label": "All", "value": 0}] + [{"label": r["Name"], "value": r["ID"]} for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT DISTINCT VisitType FROM rspl_tourdata WHERE VisitType IS NOT NULL AND VisitType <> '' ORDER BY VisitType")
        visit_types = ["All"] + [r["VisitType"] for r in rows_to_dicts(cursor)]
        cursor.execute("SELECT DISTINCT ModeOfTravel FROM rspl_tourdata WHERE ModeOfTravel IS NOT NULL AND ModeOfTravel <> '' ORDER BY ModeOfTravel")
        modes = ["All"] + [r["ModeOfTravel"] for r in rows_to_dicts(cursor)]
    return {"cities": cities, "users": users, "customers": customers, "visitTypes": visit_types, "modesOfTravel": modes}


@router.get("/report/customers", response_model=list[LookupOption])
def get_tour_report_customers(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            # Smart keyword search — see marketing_enquiry.get_customers_for_search.
            keywords = search.split()
            where_clause = " and ".join(["Displayname LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]
            cursor.execute(
                "select distinct top 100 Displayname as Name, CustID as ID from CustomerMaster "
                f"where disabled=0 and {where_clause} Order by Displayname",
                *params,
            )
        else:
            cursor.execute("select distinct top 100 Displayname as Name, CustID as ID from CustomerMaster where disabled=0 Order by Displayname")
        return [LookupOption(label=r["Name"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.get("/report", response_model=list[TourReportRow])
def get_tour_report_rows(
    user_name: str = "", customer: str = "", city: str = "", visit_type: str = "", mode_of_travel: str = "",
    payment_voucher_no: str = "", from_date: date | None = None, to_date: date | None = None,
) -> list[TourReportRow]:
    where = " where 1=1 "
    params: list = []
    if user_name and user_name != "All":
        where += " and username = ?"
        params.append(user_name)
    if customer and customer != "All":
        where += " and Customer = ?"
        params.append(customer)
    if city and city != "All":
        where += " and City = ?"
        params.append(city)
    if visit_type and visit_type != "All":
        where += " and visittype = ?"
        params.append(visit_type)
    if mode_of_travel and mode_of_travel != "All":
        where += " and ModeofTravel = ?"
        params.append(mode_of_travel)
    if payment_voucher_no:
        where += " and PaymentVoucherNo = ?"
        params.append(payment_voucher_no)
    if from_date and to_date:
        where += " and date >= ? and date <= ?"
        params += [from_date, to_date]
    sql = (
        "select username,VisitType,ModeOfTravel,City,Customer,"
        "((LEFT(DATENAME(dd,date),2))+'-'+(left(DATENAME(mm,date),3))+'-'+(RIGHT(DATENAME(yy,date),4))) as Date,"
        "PaymentVoucherNo,(left(DATENAME(mm,date),3)) as month,CONVERT(varchar,Amount, 1) as Amount "
        f"from rspl_tourdata{where}"
    )
    with get_cursor() as cursor:
        cursor.execute(sql, *params)
        rows = rows_to_dicts(cursor)
    return [
        TourReportRow(
            user_name=r["username"] or "", visit_type=r["VisitType"] or "", mode_of_travel=r["ModeOfTravel"] or "",
            city=r["City"] or "", customer=r["Customer"] or "", date=r["Date"] or None,
            payment_voucher_no=str(r["PaymentVoucherNo"] or ""), month=r["month"] or "",
            amount=float((r["Amount"] or "0").replace(",", "")),
        )
        for r in rows
    ]
