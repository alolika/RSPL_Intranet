"""Mirrors Section_Marketing/CustRegistration.aspx.vb (class
Section_Marketing_CustRegistration) — flagged in the source as the most
complex form in the app. The Angular rebuild already simplified two things
before this backend was written: the per-campaign "inform" checkbox loop
(which called webProc_AddCustomer/webProc_ModifyCustomer once per checked
campaign in the source — a real legacy quirk, not replicated) collapses to
a single MultiSelect whose first selection is sent as one @Campaign value;
and the GSTIN auto-fill button calls an external LicenseInfo COM API
(GSTIN_TaxPayerSearch, hardcoded creds) that has no equivalent in this
stack — stubbed to always report "not verified" rather than faking a
successful lookup.

webProc_AddCustomer and webProc_ModifyCustomer are both 40-param procs;
verified the source's call sites supply exactly 40 for both — unlike
CustEnquiry's Add call and the AMC/CustEnquiryAction procs, these two were
NOT short-by-N (worth checking every proc call this way; two of four found
in this project so far were actually broken).
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/marketing/registration", tags=["marketing-registration"])


class LookupOption(BaseModel):
    label: str
    value: int


class CustRegistrationLookups(BaseModel):
    groups: list[LookupOption]
    case_types: list[LookupOption]
    business_natures: list[LookupOption]
    enquiry_sources: list[LookupOption]
    received_by_users: list[LookupOption]
    segments: list[LookupOption]
    providers: list[LookupOption]
    campaigns: list[LookupOption]
    continents: list[LookupOption]
    zones: list[LookupOption]
    countries: list[LookupOption]
    states: list[LookupOption]
    districts: list[LookupOption]
    cities: list[LookupOption]


class CustRegistrationDetail(BaseModel):
    cust_id: int = 0
    group_id: int = 0
    name: str = ""
    last_name: str = ""
    mobile_no: str = ""
    telephone_no: str = ""
    email: str = ""
    address1: str = ""
    address2: str = ""
    area: str = ""
    pin_code: str = ""
    contact_person: str = ""
    gstin: str = ""
    arn: str = ""
    case_type: str = ""
    hardware_partner: str = ""
    narration: str = ""
    continent_id: int = 1
    country_id: int = 91
    state_id: int = 15
    district_id: int = 0
    city_id: int = 0
    zone_id: int = 0
    enquiry_date: date
    received_by: int = 0
    enquiry_source: int = 0
    source_detail: str = ""
    business_nature: int = 0
    segment: int = 0
    provider: int = 0
    referral_card_no: str = ""
    hardware: str = ""
    campaign_id: int = 0
    referred_by_name: str = ""
    assigned_to_name: str = ""


# GroupID for the fixed "CUSTOMERS" group used as the initial referral card generator context;
# not otherwise special-cased here.


def _little_master(cursor, master_name: str) -> list[LookupOption]:
    cursor.execute("EXEC Webproc_LittleMaster ?", master_name)
    return [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]


@router.get("/lookups", response_model=CustRegistrationLookups)
def get_cust_registration_lookups() -> CustRegistrationLookups:
    with get_cursor() as cursor:
        cursor.execute("SELECT GroupID, Name FROM CustomerGroupMaster WHERE GroupType = 1 ORDER BY Name")
        groups = [LookupOption(label=r["Name"], value=r["GroupID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT MasterValue FROM Web_LittleMaster WHERE MasterName = 'Case Type'")
        case_types = [LookupOption(label=r["MasterValue"], value=i) for i, r in enumerate(rows_to_dicts(cursor))]

        business_natures = _little_master(cursor, "BusinessNature")
        enquiry_sources = _little_master(cursor, "EnquirySourceType")
        segments = _little_master(cursor, "SegmentMaster")
        providers = _little_master(cursor, "Providers")

        cursor.execute(
            "SELECT UserID, name FROM usermaster WHERE enabled = 1 AND (UserType IN (1,6,3,5) OR userId IN (119,120)) ORDER BY name"
        )
        received_by_users = [LookupOption(label=r["name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT Name, CampaignID FROM CampaignMaster WHERE Disabled = 0 ORDER BY Name")
        campaigns = [LookupOption(label=r["Name"], value=r["CampaignID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT ContinentID, ContinentName FROM geo_ContinentMaster WHERE Disabled = 0")
        continents = [LookupOption(label=r["ContinentName"], value=r["ContinentID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT ZoneID, ZoneName FROM geo_ZoneMaster WHERE Disabled = 0")
        zones = [LookupOption(label=r["ZoneName"], value=r["ZoneID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT CountryID, CountryName FROM geo_CountryMaster WHERE Disabled = 0 ORDER BY CountryName")
        countries = [LookupOption(label=r["CountryName"], value=r["CountryID"]) for r in rows_to_dicts(cursor)]

        cursor.execute("SELECT StateID, StateName FROM geo_StateMaster WHERE Disabled = 0 ORDER BY StateName")
        states = [LookupOption(label=r["StateName"], value=r["StateID"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT DistrictID, DistrictName FROM Geo_DistrictMaster WHERE Disabled = 0 AND StateID IN (0, 15) ORDER BY DistrictName"
        )
        districts = [LookupOption(label=r["DistrictName"], value=r["DistrictID"]) for r in rows_to_dicts(cursor)]

        cursor.execute(
            "SELECT CityID, CityName FROM geo_CityMaster WHERE Disabled = 0 AND StateID IN (0, 15) ORDER BY CityName"
        )
        cities = [LookupOption(label=r["CityName"], value=r["CityID"]) for r in rows_to_dicts(cursor)]

    return CustRegistrationLookups(
        groups=groups, case_types=case_types, business_natures=business_natures, enquiry_sources=enquiry_sources,
        received_by_users=received_by_users, segments=segments, providers=providers, campaigns=campaigns,
        continents=continents, zones=zones, countries=countries, states=states, districts=districts, cities=cities,
    )


# Mirrors ComboFill.asmx's FillCustomerWithGroupID1 (rcbRefferedBy, shown when "Potential
# Customer" is unchecked) and FillPotentialCustomer (rcbrefcust, shown when it's checked) —
# both were genuinely load-on-demand RadComboBoxes in the source (EnableLoadOnDemand="True"),
# capped to a fixed TOP-N and re-queried by DisplayName as the user types, never a full
# CustomerMaster list. The earlier Angular port had stubbed both to a permanently empty list.
@router.get("/referred-by-customers", response_model=list[LookupOption])
def get_referred_by_customers(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CM.CustID, CM.DisplayName FROM CustomerMaster CM "
                "INNER JOIN CustomerGroupMaster G ON CM.GroupID = G.GroupID "
                "WHERE G.GroupID = 1 AND CM.DisplayName LIKE ? ORDER BY CM.DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT TOP 100 CM.CustID, CM.DisplayName FROM CustomerMaster CM "
                "INNER JOIN CustomerGroupMaster G ON CM.GroupID = G.GroupID "
                "WHERE G.GroupID = 1 ORDER BY CM.DisplayName"
            )
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/potential-customers", response_model=list[LookupOption])
def get_potential_customers(search: str = "") -> list[LookupOption]:
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE GroupId <> 1 AND DisplayName LIKE ? ORDER BY DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute(
                "SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE GroupId <> 1 ORDER BY DisplayName"
            )
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


# Mirrors ComboFill.asmx's FillCustomerWithGroupType1 (cmbCustomerName, the Modify-mode
# "Customer Name" search box) — same load-on-demand shape as referred-by-customers /
# potential-customers above (TOP 100, re-queried by DisplayName as the user types), scoped
# to GroupType=1 (the real "customer" groups, as opposed to Employees/Relatives/Business).
# The source additionally restricts results to the logged-in user's own customers when
# Session("usertype")=101 — a restricted sales-rep role; replicated via CurrentUser.user_type.
@router.get("/customer-name-search", response_model=list[LookupOption])
def search_customer_name(search: str = "", current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        own_only = current_user.user_type == "101"
        sql = (
            "SELECT TOP 100 CM.CustID, CM.DisplayName FROM CustomerMaster CM "
            "INNER JOIN CustomerGroupMaster G ON CM.GroupID = G.GroupID "
            "WHERE G.GroupType = 1"
        )
        params: list = []
        if own_only:
            sql += " AND CM.userID = ?"
            params.append(current_user.user_id)
        if search.strip():
            sql += " AND CM.DisplayName LIKE ?"
            params.append(f"%{search}%")
        sql += " ORDER BY CM.DisplayName"
        cursor.execute(sql, *params)
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/cities-districts")
def get_cities_and_districts(state_id: int) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT CityID, CityName FROM geo_CityMaster WHERE Disabled = 0 AND StateID IN (0, ?) ORDER BY CityName",
            state_id,
        )
        cities = [{"label": r["CityName"], "value": r["CityID"]} for r in rows_to_dicts(cursor)]
        cursor.execute(
            "SELECT DistrictID, DistrictName FROM Geo_DistrictMaster WHERE Disabled = 0 AND StateID IN (0, ?) ORDER BY DistrictName",
            state_id,
        )
        districts = [{"label": r["DistrictName"], "value": r["DistrictID"]} for r in rows_to_dicts(cursor)]
    return {"cities": cities, "districts": districts}


@router.get("/detail/{cust_id}")
def get_cust_registration_detail(cust_id: int) -> dict | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT C.GroupId, C.Name, C.Lastname, C.MobileNo, C.PhNo, C.Email, C.BldgName, C.Road, C.Area, "
            "C.Pincode, C.LandMark, C.GSTIN, C.ARN, C.Referance, C.ContinentID, C.CountryID, C.StateID, "
            "C.DistrictID, C.CityID, C.ZoneID FROM CustomerMaster C "
            "INNER JOIN CustomerGroupMaster G ON C.GroupId = G.GroupID WHERE C.CustID = ?",
            cust_id,
        )
        row = cursor.fetchone()
        if not row:
            return None
        c = dict(zip([col[0] for col in cursor.description], row))

        cursor.execute("EXEC Webproc_ViewEnquiry ?", cust_id)
        enq_rows = rows_to_dicts(cursor)
        e = enq_rows[0] if enq_rows else {}

    return {
        "cust_id": cust_id,
        "group_id": c.get("GroupId") or 0,
        "name": c.get("Name") or "",
        "last_name": c.get("Lastname") or "",
        "mobile_no": c.get("MobileNo") or "",
        "telephone_no": c.get("PhNo") or "",
        "email": c.get("Email") or "",
        "address1": c.get("BldgName") or "",
        "address2": c.get("Road") or "",
        "area": c.get("Area") or "",
        "pin_code": c.get("Pincode") or "",
        "contact_person": c.get("LandMark") or "",
        "gstin": c.get("GSTIN") or "",
        "arn": c.get("ARN") or "",
        "case_type": c.get("Referance") or "",
        "hardware_partner": "",
        "narration": e.get("Narration") or "",
        "continent_id": c.get("ContinentID") or 1,
        "country_id": c.get("CountryID") or 91,
        "state_id": c.get("StateID") or 15,
        "district_id": c.get("DistrictID") or 0,
        "city_id": c.get("CityID") or 0,
        "zone_id": c.get("ZoneID") or 0,
        "enquiry_date": e["EnquiryDate"].date().isoformat() if e.get("EnquiryDate") else datetime.now().date().isoformat(),
        "received_by": int(e.get("ReceivedBy") or 0),
        "enquiry_source": int(e.get("Sourcetype") or 0),
        "source_detail": e.get("SourceNarration") or "",
        "business_nature": int(e.get("NatureOfBusiness") or 0),
        "segment": int(e.get("Segment") or 0),
        "provider": int(e.get("Providers") or 0),
        "referral_card_no": e.get("ReferralCardNo") or "",
        "hardware": e.get("HardwarePatner") or "",
        "campaign_id": int(e.get("Campaign") or 0),
        "referred_by_name": e.get("ReferredByName") or "",
        "assigned_to_name": e.get("AssignedToName") or "",
    }


def _city_name(cursor, city_id: int) -> str:
    if not city_id:
        return ""
    cursor.execute("SELECT CityName FROM geo_CityMaster WHERE CityID = ?", city_id)
    row = cursor.fetchone()
    return row[0] if row else ""


@router.post("/add")
def add_customer_registration(
    body: CustRegistrationDetail, current_user: CurrentUser = Depends(get_current_user)
) -> dict:
    with get_cursor() as cursor:
        city_name = _city_name(cursor, body.city_id)
        params = [
            body.name, body.group_id, body.mobile_no, body.email, body.address1, body.address2,
            city_name, body.contact_person, body.narration, body.last_name, body.telephone_no,
            body.area, body.pin_code, body.case_type, body.narration, current_user.user_id,
            body.received_by, body.enquiry_source, body.source_detail, body.business_nature, body.segment,
            0, "", 1, body.referral_card_no, body.continent_id, body.country_id, body.state_id,
            body.district_id, body.city_id, body.zone_id, 1, 0, body.referred_by_name, 0,
            body.gstin, body.arn, body.provider, body.campaign_id, body.hardware,
        ]
        placeholders = ", ".join("?" for _ in params)
        cursor.execute(f"EXEC webProc_AddCustomer {placeholders}", *params)
        rows = rows_to_dicts(cursor)
    if not rows:
        return {"success": False, "custId": 0, "message": "Customer registration failed."}
    r = rows[0]
    if r.get("Campaign") and int(r.get("Campaign") or 0) > 0:
        with get_cursor() as cursor:
            cursor.execute("EXEC Proc_AssignCampaignToCust ?, ?", r["Campaign"], r["CustID"])
    return {"success": bool(r.get("Success")), "custId": r.get("CustID") or 0, "message": r.get("ResultMessage") or ""}


@router.post("/modify")
def modify_customer_registration(
    body: CustRegistrationDetail, current_user: CurrentUser = Depends(get_current_user)
) -> dict:
    with get_cursor() as cursor:
        city_name = _city_name(cursor, body.city_id)
        params = [
            body.cust_id, body.name, body.group_id, body.mobile_no, body.email, body.address1, body.address2,
            city_name, body.contact_person, body.narration, body.last_name, body.telephone_no,
            body.area, body.pin_code, body.case_type, body.narration, current_user.user_id,
            body.received_by, body.enquiry_source, body.source_detail, body.business_nature, body.segment,
            0, "", body.referral_card_no, body.continent_id, body.country_id, body.state_id,
            body.district_id, body.city_id, body.zone_id, body.enquiry_date, 0, body.referred_by_name, 0,
            body.gstin, body.arn, body.provider, body.campaign_id, body.hardware,
        ]
        placeholders = ", ".join("?" for _ in params)
        cursor.execute(f"EXEC webProc_ModifyCustomer {placeholders}", *params)
        rows = rows_to_dicts(cursor)
    if not rows:
        return {"success": False, "message": "Customer update failed."}
    r = rows[0]
    if r.get("Campaign") and int(r.get("Campaign") or 0) > 0:
        with get_cursor() as cursor:
            cursor.execute("EXEC Proc_AssignCampaignToCust ?, ?", r["Campaign"], r["CustID"])
    return {"success": bool(r.get("Success")), "message": r.get("ResultMessage") or ""}


@router.get("/check-mobile")
def check_mobile_availability(mobile_no: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 1 CustID, Name FROM Customermaster WHERE RIGHT(MobileNo, 10) = RIGHT(?, 10)", mobile_no
        )
        row = cursor.fetchone()
    if row:
        return {"available": False, "custId": row[0]}
    return {"available": True}


@router.get("/verify-gstin")
def verify_gstin(gstin: str) -> dict:
    # Stubbed — see module docstring. The source's GSTIN_TaxPayerSearch call hits an
    # external license server with hardcoded credentials; no equivalent in this stack.
    return {"verified": False}


@router.get("/lookup-pincode")
def lookup_pincode(pin_code: str) -> dict | None:
    with get_cursor() as cursor:
        cursor.execute("SELECT TOP 1 Tehsil, District, State FROM PinCodeCityMaster WHERE Pincode = ?", pin_code)
        row = cursor.fetchone()
        if not row:
            return None
        district_name, state_name = row[1], row[2]
        cursor.execute("SELECT StateID FROM geo_StateMaster WHERE StateName = ?", state_name)
        state_row = cursor.fetchone()
        state_id = state_row[0] if state_row else 0
        cursor.execute("SELECT DistrictID FROM Geo_DistrictMaster WHERE DistrictName = ? AND StateID = ?", district_name, state_id)
        district_row = cursor.fetchone()
        district_id = district_row[0] if district_row else 0
    return {"stateId": state_id, "districtId": district_id}


@router.post("/create-city")
def create_city(state_id: int, city_name: str) -> dict:
    with get_cursor() as cursor:
        cursor.execute("EXEC WebProc_CreateCity ?, ?", state_id, city_name)
        rows = rows_to_dicts(cursor)
    if not rows:
        return {"cityId": 0, "message": "City creation failed."}
    r = rows[0]
    return {"cityId": r.get("CityID") or 0, "message": r.get("ResultMessage") or ""}
