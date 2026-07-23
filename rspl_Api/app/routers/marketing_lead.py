"""Mirrors Section_Marketing/CustLeadReport.aspx.vb and CustLeadChart.aspx.vb
— both call the same `webproc_LeadChartSummaryV2`/`webproc_LeadChartDetailV2`
procs (chart page reuses the report's summary data, per the source and the
Angular mock's own docstring), so both pages share this one router.
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing/lead", tags=["marketing-lead"])


class LeadSummaryRow(BaseModel):
    type_id: int
    type_name: str
    leads_generated: int
    leads_converted: int
    lead_conversion_rate: float
    no_of_transactions: int
    sale_revenue: float
    amc_visit_revenue: float
    total_revenue: float


class LeadSummaryDetailRow(BaseModel):
    cust_id: int
    generated: bool
    converted: bool
    assigned_to: str
    enquiry_date: str | None
    customer_name: str
    mobile_no: str
    vertical_name: str
    segment_name: str
    source_type_name: str
    source_narration: str
    city: str
    contact_person: str
    existing_software: str


@router.get("/summary", response_model=list[LeadSummaryRow])
def get_lead_summary_rows(group_by: str = "Nothing", from_date: date | None = None, to_date: date | None = None) -> list[LeadSummaryRow]:
    with get_cursor() as cursor:
        cursor.execute("EXEC webproc_LeadChartSummaryV2 ?, ?, ?", group_by, from_date or "", to_date or "")
        rows = rows_to_dicts(cursor)
    return [
        LeadSummaryRow(
            type_id=r["TypeID"], type_name=r["TypeName"] or "", leads_generated=r["LeadsGenerated"] or 0,
            leads_converted=r["LeadsConverted"] or 0, lead_conversion_rate=float(r["LeadConversionRate"] or 0),
            no_of_transactions=r["NoOfTransactions"] or 0, sale_revenue=float(r["SaleRevenue"] or 0),
            amc_visit_revenue=float(r["AMCVisitRevenue"] or 0), total_revenue=float(r["TotalRevenue"] or 0),
        )
        for r in rows
    ]


@router.get("/summary/{type_id}/detail", response_model=list[LeadSummaryDetailRow])
def get_lead_summary_detail(type_id: int, group_by: str, from_date: date | None = None, to_date: date | None = None) -> list[LeadSummaryDetailRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_LeadChartDetailV2 ?, ?, ?, ?", group_by, type_id, from_date or "", to_date or ""
        )
        rows = rows_to_dicts(cursor)
    return [
        LeadSummaryDetailRow(
            cust_id=r["CustID"], generated=bool(r["Generated"]), converted=bool(r["Converted"]),
            assigned_to=r["AssignedTo"] or "", enquiry_date=r["EnquiryDate"].isoformat() if r["EnquiryDate"] else None,
            customer_name=r["CustomerName"] or "", mobile_no=r["MobileNo"] or "",
            vertical_name=r["VerticalName"] or "", segment_name=r["SegmentName"] or "",
            source_type_name=r["EnquirySourceTypeName"] or "", source_narration=r.get("EnquirySourceNarration") or "",
            city=r.get("City") or "",
            # webproc_LeadChartDetailV2 passes these two straight through from
            # vwWeb_LeadEnquiryMaster's own aliases ("C.LandMark contactPerson",
            # "LS4.MasterValue Existingsoftware") unchanged — confirmed live via
            # cursor.description that the real casing is "contactPerson"
            # (lowercase c) and "Existingsoftware" (lowercase s), not the
            # PascalCase originally guessed here. dict.get() is case-sensitive,
            # so the mismatch silently returned None -> "" for every row
            # instead of erroring, which is why the columns rendered but
            # stayed blank.
            contact_person=r.get("contactPerson") or "",
            existing_software=r.get("Existingsoftware") or "",
        )
        for r in rows
    ]
