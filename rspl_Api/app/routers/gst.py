"""Mirrors Section_GST/*.aspx.vb.

NOTE: every view here (vw_web_StateMaster, vw_web_softwaremaster,
vw_web_VatMaster, vw_Web_ClientSoftwareMenuRequest) and
proc_web_CalculateGSTTax ultimately read from
[License].[License_Retailware] via a linked server named `License`.
That linked server is not configured on the rspldemosql.retailware.in
instance this API currently points at (confirmed via `sys.servers`), so
every endpoint in this router will 500 until ops adds it. The code is
written to the confirmed view/proc signatures so it's ready to go once
that's in place; none of it has been exercised against live data.
"""

from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/gst", tags=["gst"])


class LookupOption(BaseModel):
    label: str
    value: int


class GstState(BaseModel):
    state_code: str
    state_name: str
    gst_start_date: str | None
    hsn_code_start_date: str | None
    e_way_bill_start_date: str | None
    e_way_bill_minimum_value: float
    sale_turnover: float


class GstTaxRow(BaseModel):
    software_name: str
    tax_id: int
    tax_name: str
    tax_per: float
    sgst_per: float
    cgst_per: float
    igst_per: float
    utgst_per: float
    cess_based_on: str
    cess_per: float
    cess_amt_per_unit: float
    slab_applicable: bool
    disabled: bool


class CalculateTaxRequest(BaseModel):
    gst_tax_type_id: int
    software_id: int
    rate: float
    qty: float
    inclusive: bool


class GstTaxCalcRow(BaseModel):
    tax_id: int | None = None
    tax_name: str | None = None
    sale_rate: float | None = None
    tax_amount: float | None = None
    net_amount: float | None = None


class MenuRequestSummaryRow(BaseModel):
    menu_name: str
    view_count: int
    last_viewed_on: str | None
    client_count: int


class MenuRequestDetailRow(BaseModel):
    customer_id: int
    customer_name: str
    menu_name: str
    last_user_name: str
    last_machine_name: str
    last_viewed_on: str | None
    view_count: int


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


@router.get("/software-options", response_model=list[LookupOption])
def get_software_options() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT SoftwareID, SoftwareName FROM vw_web_softwaremaster")
        return [LookupOption(label=r["SoftwareName"], value=r["SoftwareID"]) for r in rows_to_dicts(cursor)]


@router.get("/tax-types", response_model=list[LookupOption])
def get_gst_tax_types() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT ID, MasterValue FROM Web_LittleMaster WHERE MasterName = 'GSTTaxType'")
        return [LookupOption(label=r["MasterValue"], value=r["ID"]) for r in rows_to_dicts(cursor)]


# --- Mirrors Section_GST/GSTStateMaster.aspx.vb ---


@router.get("/state-master", response_model=list[GstState])
def get_state_master() -> list[GstState]:
    with get_cursor() as cursor:
        cursor.execute("SELECT * FROM vw_web_StateMaster")
        rows = rows_to_dicts(cursor)

    return [
        GstState(
            state_code=r["GSTStateCode"] or "",
            state_name=r["StateName"] or "",
            gst_start_date=_iso(r["GSTStartDate"]),
            hsn_code_start_date=_iso(r["HSNCodeStartDate"]),
            e_way_bill_start_date=_iso(r["EWayBillStartDate"]),
            e_way_bill_minimum_value=float(r["EWayBillMinimumValue"] or 0),
            sale_turnover=float(r["SaleTurnover"] or 0),
        )
        for r in rows
    ]


# --- Mirrors Section_GST/GSTTaxMaster.aspx.vb ---


@router.get("/tax-master", response_model=list[GstTaxRow])
def get_tax_master(software_id: int = 0) -> list[GstTaxRow]:
    query = """
        SELECT SM.SoftwareName, VM.TaxID, VM.TaxName, VM.TaxPer, VM.SGSTPer, VM.CGSTPer, VM.IGSTPer,
               VM.UTGSTPer, VM.CessBasedOn, VM.CessPer, VM.CessAmtPerUnit, VM.SlabApplicable, VM.Disabled
        FROM vw_web_VatMaster VM
        INNER JOIN vw_web_softwaremaster SM ON SM.SoftwareID = VM.SoftwareID
    """
    params: list = []
    if software_id > 0:
        query += " WHERE VM.SoftwareID = ?"
        params.append(software_id)

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [
        GstTaxRow(
            software_name=r["SoftwareName"] or "",
            tax_id=r["TaxID"],
            tax_name=r["TaxName"] or "",
            tax_per=float(r["TaxPer"] or 0),
            sgst_per=float(r["SGSTPer"] or 0),
            cgst_per=float(r["CGSTPer"] or 0),
            igst_per=float(r["IGSTPer"] or 0),
            utgst_per=float(r["UTGSTPer"] or 0),
            cess_based_on=str(r["CessBasedOn"]) if r["CessBasedOn"] is not None else "",
            cess_per=float(r["CessPer"] or 0),
            cess_amt_per_unit=float(r["CessAmtPerUnit"] or 0),
            slab_applicable=bool(r["SlabApplicable"]),
            disabled=bool(r["Disabled"]),
        )
        for r in rows
    ]


# --- Mirrors Section_GST/GSTTaxCalculator.aspx.vb ---


@router.post("/calculate-tax", response_model=list[GstTaxCalcRow])
def calculate_tax(body: CalculateTaxRequest) -> list[GstTaxCalcRow]:
    with get_cursor() as cursor:
        cursor.execute("SELECT TaxID FROM vw_web_VatMaster WHERE SoftwareID = ?", body.software_id)
        tax_ids = [r["TaxID"] for r in rows_to_dicts(cursor)]

        results: list[GstTaxCalcRow] = []
        for tax_id in tax_ids:
            cursor.execute(
                "EXEC dbo.proc_web_CalculateGSTTax ?, ?, ?, ?, ?",
                body.gst_tax_type_id,
                tax_id,
                1 if body.inclusive else 0,
                body.rate,
                body.qty,
            )
            rows = rows_to_dicts(cursor)
            if not rows:
                continue
            row = rows[0]
            if not row.get("saleRate") and not row.get("SaleRate"):
                continue
            results.append(
                GstTaxCalcRow(
                    tax_id=row.get("TaxID", tax_id),
                    tax_name=row.get("TaxName"),
                    sale_rate=row.get("saleRate") or row.get("SaleRate"),
                    tax_amount=row.get("TaxAmount") or row.get("taxAmount"),
                    net_amount=row.get("NetAmount") or row.get("netAmount"),
                )
            )

    return results


# --- Mirrors Section_GST/ClientSoftwareMenuRequest.aspx.vb ---


@router.get("/customers", response_model=list[LookupOption])
def get_customers_for_software(software_id: int) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT CustID, Displayname FROM CustomerMaster Cm
            INNER JOIN vw_Web_ClientSoftwareMenuRequest CSMR ON Cm.CustID = CSMR.ClientID
            WHERE SoftwareID = ?
            ORDER BY Displayname
            """,
            software_id,
        )
        return [LookupOption(label=r["Displayname"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/menu-request-summary", response_model=list[MenuRequestSummaryRow])
def get_menu_request_summary(software_id: int = 0) -> list[MenuRequestSummaryRow]:
    query = """
        SELECT MenuName, SUM(ViewCount) ViewCount, MAX(LastviewedOn) LastviewedOn, COUNT(ClientID) ClientCount
        FROM vw_Web_ClientSoftwareMenuRequest
    """
    params: list = []
    if software_id > 0:
        query += " WHERE SoftwareID = ?"
        params.append(software_id)
    query += " GROUP BY SoftwareID, SubSoftwareID, MenuName ORDER BY ViewCount DESC, LastViewedOn DESC, MenuName"

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [
        MenuRequestSummaryRow(
            menu_name=r["MenuName"] or "",
            view_count=r["ViewCount"] or 0,
            last_viewed_on=_iso(r["LastviewedOn"]),
            client_count=r["ClientCount"] or 0,
        )
        for r in rows
    ]


@router.get("/menu-request-details", response_model=list[MenuRequestDetailRow])
def get_menu_request_details(software_id: int = 0, customer_id: int = 0) -> list[MenuRequestDetailRow]:
    query = """
        SELECT CSMR.ClientID AS CustomerID, Cm.Displayname AS CustomerName, CSMR.MenuName,
               CSMR.LastUserName, CSMR.LastMachineName, CSMR.LastViewedOn, CSMR.ViewCount
        FROM vw_Web_ClientSoftwareMenuRequest CSMR
        INNER JOIN CustomerMaster cm ON cm.CustID = CSMR.ClientID
        WHERE 1 = 1
    """
    params: list = []
    if software_id > 0:
        query += " AND SoftwareID = ?"
        params.append(software_id)
    if customer_id > 0:
        query += " AND ClientID = ?"
        params.append(customer_id)

    with get_cursor() as cursor:
        cursor.execute(query, *params)
        rows = rows_to_dicts(cursor)

    return [
        MenuRequestDetailRow(
            customer_id=r["CustomerID"],
            customer_name=r["CustomerName"] or "",
            menu_name=r["MenuName"] or "",
            last_user_name=r["LastUserName"] or "",
            last_machine_name=r["LastMachineName"] or "",
            last_viewed_on=_iso(r["LastViewedOn"]),
            view_count=r["ViewCount"] or 0,
        )
        for r in rows
    ]
