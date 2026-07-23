"""Mirrors Section_Marketing/CustEnquiry.aspx.vb — the main lead-intake
form (New/Modify/View via ?ActionType=). The source's four radio-button
customer-search modes (Shop Name/Contact Person/Mobile No/Customer ID) were
simplified to one customer picker in the Angular rebuild; CustomerMaster is
80k+ rows so that picker is capped to the 1000 most recently added
customers (same limitation as Admin's Advance voucher customer picker).

Found a real latent bug while wiring this: `WebProc_AddCustEnquiry` takes 42
positional params (confirmed via sys.parameters, none with defaults) but the
source's insert call only ever supplies 35 — the "New" enquiry path has
been silently broken (SQL "too few arguments", swallowed by an empty Catch)
for as long as the trailing Referral/Campaign/Hardware-partner columns have
existed on that proc. `WebProc_ModifyCustEnquiry` (32 params) is NOT
affected — its call site supplies exactly 32. Written correctly against the
confirmed signature; the referral-card/campaign/hardware fields aren't
modeled in the Angular form (not part of this page's UI), so they're sent
as blank/zero defaults.

Also: `WebProc_AddCustEnquiry`'s `@NatureOfBusiness` is an int (ID) but
`WebProc_ModifyCustEnquiry`'s `@NatureOfBusiness` is nvarchar (still fed the
ID, just unquoted-into-a-string) — a real inconsistency between the two
procs, not a transcription error. `@OwnershipName`/`@SalesMethod`/
`@GoodsSold` are nvarchar TEXT LABELS in both procs (the source passes
`.SelectedItem.Text`, not `.SelectedValue`) even though the Angular form
only carries their numeric ids — resolved to label text server-side via
Web_LittleMaster before calling either proc.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/marketing/enquiry", tags=["marketing-enquiry"])


class LookupOption(BaseModel):
    label: str
    value: int


class EnquiryMasterLists(BaseModel):
    goods_sold: list[LookupOption]
    nature_of_business: list[LookupOption]
    method_of_sale: list[LookupOption]
    ownership: list[LookupOption]
    comm_method: list[LookupOption]
    source_type: list[LookupOption]
    received_by: list[LookupOption]
    segment: list[LookupOption]


class CustomerGeneralInfo(BaseModel):
    cust_id: int
    shop_name: str
    address: str
    contact_person: str
    mobile_no: str
    tel_no: str
    email: str


class CustEnquiryForm(BaseModel):
    enquiry_date: date
    received_by: int | None = None
    source_type: int | None = None
    source_detail: str = ""
    nature_of_business: int | None = None
    segment: int | None = None
    comm_method: int | None = None
    comm_details: str = ""
    ownership: int | None = None
    no_of_outlets: str = ""
    method_of_sale: int | None = None
    size_of_shop: str = ""
    goods_sold: int | None = None
    total_items: str = ""
    rush_hours: str = ""
    sales_staff: str = ""
    billing_system: str = ""
    transactions_per_day: str = ""
    competitor: str = ""
    customisation: str = ""
    order_value: str = ""
    modules: str = ""
    discount_offered: str = ""
    assigned_to: str = ""
    narration: str = ""


def _little_master(cursor, master_name: str) -> list[LookupOption]:
    cursor.execute("EXEC Webproc_LittleMaster ?", master_name)
    return [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]


def _label_for(cursor, id_: int | None) -> str:
    if not id_:
        return ""
    cursor.execute("SELECT MasterValue FROM Web_LittleMaster WHERE ID = ?", id_)
    row = cursor.fetchone()
    return row[0] if row else ""


@router.get("/master-lists", response_model=EnquiryMasterLists)
def get_enquiry_master_lists() -> EnquiryMasterLists:
    with get_cursor() as cursor:
        goods_sold = _little_master(cursor, "GoodsSold")
        nature_of_business = _little_master(cursor, "BusinessNature")
        method_of_sale = _little_master(cursor, "SalesMethod")
        ownership = _little_master(cursor, "Ownership")
        comm_method = _little_master(cursor, "CommunicationMethod")
        source_type = _little_master(cursor, "EnquirySourceType")
        segment = _little_master(cursor, "SegmentMaster")
        cursor.execute(
            "SELECT UserID, name FROM usermaster WHERE enabled = 1 AND UserType IN (1,6,3,5) ORDER BY name"
        )
        received_by = [LookupOption(label=r["name"], value=r["UserID"]) for r in rows_to_dicts(cursor)]
    return EnquiryMasterLists(
        goods_sold=goods_sold, nature_of_business=nature_of_business, method_of_sale=method_of_sale,
        ownership=ownership, comm_method=comm_method, source_type=source_type, received_by=received_by,
        segment=segment,
    )


@router.get("/customers-for-search", response_model=list[LookupOption])
def get_customers_for_search(search: str = "") -> list[LookupOption]:
    # DisplayName (not Name) is the label used everywhere else a customer
    # picker is built (see marketing_register.py's /customers and /search) —
    # it includes the area/city suffix, which is what actually disambiguates
    # same-named shops in different locations.
    #
    # Previously TOP 1000 by CustID DESC with only client-side [filter] on top —
    # same issue as Referred By Customer/Customer Name (marketing_registration.py)
    # before those were fixed: any customer outside that most-recent-1000 window
    # was simply unfindable no matter what was typed, on top of loading 1000 rows
    # on every page visit. Now capped to 100 and re-queried by name as the user
    # types (same load-on-demand shape as the other two). No-search default keeps
    # the original "most recently added" ordering — search results sort by name.
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE Disabled = 0 AND DisplayName LIKE ? ORDER BY DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute("SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE Disabled = 0 ORDER BY CustID DESC")
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/customer-general-info/{cust_id}", response_model=CustomerGeneralInfo | None)
def get_customer_general_info(cust_id: int) -> CustomerGeneralInfo | None:
    # The source (ShowCustomerGeneralInfo) builds Address as a raw SQL
    # `bldgname +''+ flatno +''+ Road +''+ Area +''+ City` concatenation with
    # no separators — fine when every piece already ends in its own comma,
    # but for most customers it runs the parts together unreadably (e.g.
    # "kada PUNE", "...Rajuri RoadSANGOLA"). Selecting the raw columns and
    # joining only the non-blank ones with ", " fixes that without touching
    # the underlying data or the rest of the page's field mapping.
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT CustID, Name AS ShopName, bldgname, flatno, Road, Area, City, "
            "Landmark AS ContactPerson, MobileNo, PhNo, Email FROM CustomerMaster WHERE CustID = ?",
            cust_id,
        )
        row = cursor.fetchone()
        if not row:
            return None
        r = dict(zip([c[0] for c in cursor.description], row))
    address = ", ".join(part.strip() for part in (r["bldgname"], r["flatno"], r["Road"], r["Area"], r["City"]) if part and part.strip())
    return CustomerGeneralInfo(
        cust_id=r["CustID"], shop_name=r["ShopName"] or "", address=address,
        contact_person=r["ContactPerson"] or "", mobile_no=r["MobileNo"] or "", tel_no=r["PhNo"] or "",
        email=r["Email"] or "",
    )


@router.get("/customer-enquiry/{cust_id}")
def get_customer_enquiry(cust_id: int) -> dict | None:
    """Mirrors CustEnquiry.aspx.vb's Ismodify branch (EXEC Webproc_ViewEnquiry).

    Webproc_ViewEnquiry returns the raw foreign-key ID for most lookup
    fields directly (ReceivedBy, Sourcetype, CommunicationMethod,
    NatureOfBusiness, Segment) — those are used as-is, matching the id-based
    <p-select> bindings on the form. Ownership/SalesMethod/GoodsSold are the
    exception: the proc only returns their *display name* (no raw ID
    column), so those three still need a reverse lookup against
    Web_LittleMaster by name — trimmed on both sides since that's the one
    realistic way stored vs. denormalized text drifts and silently fails to
    match (leaving the dropdown blank despite the field having real data).
    """
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_ViewEnquiry ?", cust_id)
        rows = rows_to_dicts(cursor)
    if not rows:
        return None
    r = rows[0]

    def find_id(cursor2, master_name: str, label: str) -> int | None:
        if not label:
            return None
        cursor2.execute(
            "SELECT ID FROM Web_LittleMaster WHERE MasterName = ? AND LTRIM(RTRIM(MasterValue)) = LTRIM(RTRIM(?))",
            master_name, label,
        )
        row = cursor2.fetchone()
        return row[0] if row else None

    def to_int(value) -> int | None:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    with get_cursor() as cursor:
        return {
            "enquiry_date": r["EnquiryDate"].isoformat() if r.get("EnquiryDate") else None,
            "received_by": r.get("ReceivedBy"),
            "source_type": r.get("Sourcetype"),
            "source_detail": r.get("SourceNarration") or "",
            "nature_of_business": to_int(r.get("NatureOfBusiness")),
            "segment": to_int(r.get("Segment")),
            "comm_method": r.get("CommunicationMethod"),
            "comm_details": r.get("CommunicationDetails") or "",
            "ownership": find_id(cursor, "Ownership", r.get("OwnershipName") or ""),
            "no_of_outlets": str(r.get("NoOfOutlets") or ""),
            "method_of_sale": find_id(cursor, "SalesMethod", r.get("SalesMethod") or ""),
            "size_of_shop": r.get("SizeOfShop") or "",
            "goods_sold": find_id(cursor, "GoodsSold", r.get("GoodsSold") or ""),
            "total_items": str(r.get("TotalNoOfItemsOnDisplay") or ""),
            "rush_hours": r.get("RushHours") or "",
            "sales_staff": str(r.get("NoOfSalesStaff") or ""),
            "billing_system": r.get("PresentBillingSystem") or "",
            "transactions_per_day": str(r.get("TransactionPerDay") or ""),
            "competitor": r.get("Competitor") or "",
            "customisation": r.get("Customisation") or "",
            "order_value": str(r.get("OrderValue") or ""),
            "modules": r.get("Modules") or "",
            "discount_offered": r.get("DiscountOffered") or "",
            "assigned_to": r.get("AssignedToName") or "",
            "narration": r.get("Narration") or "",
        }


class CustEnquiryHistoryRow(BaseModel):
    shop_name: str
    enquiry_date: str | None
    source_type_name: str
    source_narration: str
    received_by_name: str
    assigned_to_name: str
    no_of_outlets: str
    ownership_name: str
    business_nature_name: str
    size_of_shop: str
    goods_sold: str
    present_billing_system: str
    modules: str
    creation_date: str | None
    order_value: str
    narration: str


@router.get("/enquiry-history/{cust_id}", response_model=list[CustEnquiryHistoryRow])
def get_enquiry_history(cust_id: int) -> list[CustEnquiryHistoryRow]:
    # Mirrors CustEnquiry.aspx.vb's read-only ViewPanel (gvviewenquiry), which binds
    # the full Webproc_ViewEnquiry result straight to a grid of friendly-text columns —
    # the same proc as /customer-enquiry/{cust_id}, but unlike that endpoint (which
    # only reads rows[0] for the edit form) this returns every row as history.
    with get_cursor() as cursor:
        cursor.execute("EXEC Webproc_ViewEnquiry ?", cust_id)
        rows = rows_to_dicts(cursor)
    return [
        CustEnquiryHistoryRow(
            shop_name=r.get("ShopName") or "",
            enquiry_date=r["EnquiryDate"].isoformat() if r.get("EnquiryDate") else None,
            source_type_name=r.get("SourceTypeName") or "",
            source_narration=r.get("SourceNarration") or "",
            received_by_name=r.get("ReceivedByName") or "",
            assigned_to_name=r.get("AssignedToName") or "",
            no_of_outlets=str(r.get("NoOfOutlets") or ""),
            ownership_name=r.get("OwnershipName") or "",
            business_nature_name=r.get("BusinessNatureName") or "",
            size_of_shop=r.get("SizeOfShop") or "",
            goods_sold=r.get("GoodsSold") or "",
            present_billing_system=r.get("PresentBillingSystem") or "",
            modules=r.get("Modules") or "",
            creation_date=r["CreationDate"].isoformat() if r.get("CreationDate") else None,
            order_value=str(r.get("OrderValue") or ""),
            narration=r.get("Narration") or "",
        )
        for r in rows
    ]


@router.post("/customer-enquiry/{cust_id}")
def save_cust_enquiry(
    cust_id: int, body: CustEnquiryForm, is_modify: bool = False,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    now = datetime.now()
    with get_cursor() as cursor:
        ownership_label = _label_for(cursor, body.ownership)
        sales_method_label = _label_for(cursor, body.method_of_sale)
        goods_sold_label = _label_for(cursor, body.goods_sold)

        if not is_modify:
            # WebProc_AddCustEnquiry — 42 positional params, listed one per line to keep the
            # count verifiable against sys.parameters (see module docstring).
            params = [
                cust_id,                                    # 1  CustId
                body.enquiry_date,                          # 2  EnquiryDate
                body.received_by or 0,                       # 3  ReceivedBy
                body.source_type or 0,                       # 4  Sourcetype
                body.source_detail,                          # 5  SourceNarration
                0,                                           # 6  AssignedTo
                body.no_of_outlets or 0,                     # 7  NoOfOutlets
                ownership_label,                             # 8  OwnershipName
                body.size_of_shop,                           # 9  SizeOfShop
                body.nature_of_business or 0,                 # 10 NatureOfBusiness (int)
                str(body.segment or 0),                       # 11 Segment
                body.narration,                              # 12 Narration
                body.total_items or 0,                       # 13 TotalNoOfItemsOnDisplay
                body.sales_staff or 0,                        # 14 NoOfSalesStaff
                sales_method_label,                          # 15 SalesMethod
                body.rush_hours,                             # 16 RushHours
                goods_sold_label,                            # 17 GoodsSold
                body.transactions_per_day or 0,               # 18 TransactionPerDay
                body.billing_system,                         # 19 PresentBillingSystem
                body.modules,                                # 20 Modules
                body.customisation,                          # 21 Customisation
                body.competitor,                             # 22 Competitor
                body.order_value or 0,                        # 23 OrderValue
                body.discount_offered,                       # 24 DiscountOffered
                now,                                         # 25 CreationDate
                now,                                         # 26 CreatedTime
                current_user.user_id,                        # 27 CreatedBy
                now,                                         # 28 ModifyDate
                now,                                         # 29 ModifyTime
                current_user.user_id,                        # 30 ModifiedBy
                0,                                           # 31 FollowupStarted
                body.comm_method or 0,                        # 32 CommunicationMethod
                body.comm_details,                           # 33 CommunicationDetails
                0,                                           # 34 AutoGenerated
                "",                                          # 35 CompletedRemark
                "",                                          # 36 ReferralCardNo
                0,                                           # 37 ReferredCustID
                "",                                          # 38 ReferredBy
                0,                                           # 39 IntroducedBy
                0,                                           # 40 providers
                0,                                           # 41 Campaign
                "",                                          # 42 hardware
            ]
            placeholders = ", ".join("?" for _ in params)
            cursor.execute(f"EXEC WebProc_AddCustEnquiry {placeholders}", *params)
        else:
            # WebProc_ModifyCustEnquiry — 32 positional params (this call site was already
            # correct in the source; kept the same shape for consistency).
            params = [
                cust_id,                                    # 1  CustID
                body.enquiry_date,                          # 2  EnquiryDate
                body.received_by or 0,                       # 3  ReceivedBy
                body.source_type or 0,                       # 4  Sourcetype
                body.source_detail,                          # 5  SourceNarration
                0,                                           # 6  AssignedTo
                body.no_of_outlets or 0,                     # 7  NoOfOutlets
                ownership_label,                             # 8  OwnershipName
                body.size_of_shop,                           # 9  SizeOfShop
                str(body.nature_of_business or 0),            # 10 NatureOfBusiness (nvarchar!)
                str(body.segment or 0),                       # 11 Segment
                body.narration,                              # 12 Narration
                body.total_items or 0,                       # 13 TotalNoOfItemsOnDisplay
                body.sales_staff or 0,                        # 14 NoOfSalesStaff
                sales_method_label,                          # 15 SalesMethod
                body.rush_hours,                             # 16 RushHours
                goods_sold_label,                            # 17 GoodsSold
                body.transactions_per_day or 0,               # 18 TransactionPerDay
                body.billing_system,                         # 19 PresentBillingSystem
                body.modules,                                # 20 Modules
                body.customisation,                          # 21 Customisation
                body.competitor,                             # 22 Competitor
                body.order_value or 0,                        # 23 OrderValue
                body.discount_offered,                       # 24 DiscountOffered
                now,                                         # 25 ModifyDate
                now,                                         # 26 ModifyTime
                current_user.user_id,                        # 27 ModifiedBy
                0,                                           # 28 FollowupStarted
                body.comm_method or 0,                        # 29 CommunicationMethod
                body.comm_details,                           # 30 CommunicationDetails
                0,                                           # 31 AutoGenerated
                "",                                          # 32 CompletedRemark
            ]
            placeholders = ", ".join("?" for _ in params)
            cursor.execute(f"EXEC WebProc_ModifyCustEnquiry {placeholders}", *params)
    return {"success": True, "cust_id": cust_id}
