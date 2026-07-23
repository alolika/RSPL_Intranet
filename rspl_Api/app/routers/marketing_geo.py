"""Mirrors Section_Marketing/CustHeatMap.aspx.vb, CustMapLocation.aspx.vb
(both are pure client-side pages in the source — their code-behind has no
data logic at all; the real query lives in App_Service/CRMMap.asmx's
GetLatLong web method, reproduced here), and EntityLatLong.aspx.vb.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing/geo", tags=["marketing-geo"])


class LookupOption(BaseModel):
    label: str
    value: int


class CustGeoRow(BaseModel):
    cust_id: int
    customer_name: str
    vertical_id: int
    vertical_name: str
    segment_name: str
    enquiry_source_type_name: str
    location_address: str
    latitude: float
    longitude: float


class EntityLatLongOption(BaseModel):
    cust_id: int
    display_name: str
    location_name: str
    latitude: str
    longitude: str


@router.get("/locations", response_model=list[CustGeoRow])
def get_cust_geo_locations() -> list[CustGeoRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "Select C.CustID,C.DisplayName CustomerName,C.Latitude,C.Longitude,"
            "isnull(L.VerticalID,1) VerticalID,L.SegmentID,L.EnquirySourceTypeID,"
            "L.VerticalName, L.SegmentName, L.EnquirySourceTypeName,"
            "ltrim(C.BldgName+' '+C.Road+' '+C.Area+' '+C.City) LocationAddress "
            "From CustomerMaster C "
            "Left Outer Join vwWeb_LeadEnquiryMaster L On C.CustID=L.CustID "
            "Where C.GroupID=1 and C.Disabled=0 and C.Latitude<>'0' and C.Longitude<>'0'"
        )
        rows = rows_to_dicts(cursor)
    return [
        CustGeoRow(
            cust_id=r["CustID"], customer_name=r["CustomerName"] or "", vertical_id=r["VerticalID"] or 0,
            vertical_name=r["VerticalName"] or "", segment_name=r["SegmentName"] or "",
            enquiry_source_type_name=r["EnquirySourceTypeName"] or "", location_address=r["LocationAddress"] or "",
            latitude=float(r["Latitude"] or 0), longitude=float(r["Longitude"] or 0),
        )
        for r in rows
    ]


@router.get("/entity-customers", response_model=list[LookupOption])
def get_entity_lat_long_customers(edit_mode: bool = True, search: str = "") -> list[LookupOption]:
    # The "unmapped" pool (edit_mode=False, the default on page load) alone is
    # 9503 rows and took ~3.9s unfiltered — capped to top 100 like every other
    # lookup in the app, re-queried by name as the user types (onFilter below),
    # same load-on-demand shape as /customers-for-search.
    where = "GroupID=1 and Latitude<>'0'" if edit_mode else "GroupID=1 and (Latitude='0' Or Latitude Is Null or Latitude='')"
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                f"Select TOP 100 CustID,DisplayName From CustomerMaster Where {where} and DisplayName LIKE ? Order By DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute(f"Select TOP 100 CustID,DisplayName From CustomerMaster Where {where} Order By DisplayName")
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/entity/{cust_id}", response_model=EntityLatLongOption | None)
def get_entity_lat_long(cust_id: int) -> EntityLatLongOption | None:
    with get_cursor() as cursor:
        cursor.execute(
            "Select CustID,rtrim(ltrim(Road+' '+Area+' '+City)) LocationName,Latitude,Longitude "
            "From CustomerMaster Where CustID = ?",
            cust_id,
        )
        rows = rows_to_dicts(cursor)
    if not rows:
        return None
    r = rows[0]
    return EntityLatLongOption(
        cust_id=r["CustID"], display_name="", location_name=r["LocationName"] or "",
        latitude=str(r["Latitude"] or ""), longitude=str(r["Longitude"] or ""),
    )


@router.post("/entity/{cust_id}")
def save_entity_lat_long(cust_id: int, latitude: str, longitude: str) -> dict:
    if not latitude:
        return {"success": False}
    with get_cursor() as cursor:
        cursor.execute(
            "Update CustomerMaster Set WebSendable=1, Latitude = ?, Longitude = ? Where CustID = ?",
            latitude, longitude, cust_id,
        )
    return {"success": True}
