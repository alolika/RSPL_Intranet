"""Mirrors Section_Marketing/e-ReferralRegister.aspx.vb. Blocked: its data
source `vw_web_eReferralVoucher` reads through the missing `[License]`
linked server (confirmed live — same category of gap as GST/Support-Modules/
Section_Admin's CustomerAns/CustomerGracePeriod). Written correctly against
the source's referenced columns anyway, per the established project pattern.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts

router = APIRouter(prefix="/marketing/referral", tags=["marketing-referral"])


class LookupOption(BaseModel):
    label: str
    value: int


class EReferralRow(BaseModel):
    request_sr_no: int
    referral_voucher_no: str
    shop_name: str
    shop_address: str
    shop_city: str
    shop_contact_person: str
    shop_mobile_no: str
    email_address: str
    introduced_by_shop_name: str
    introduce_by_name: str
    introduced_by_shop_city: str
    introduced_by_mobile_no: str
    creation_date: str
    creation_time: str
    remark: str


class EReferralPage(BaseModel):
    rows: list[EReferralRow]
    total: int


@router.get("/shops", response_model=list[LookupOption])
def get_e_referral_shops(search: str = "") -> list[LookupOption]:
    # Was TOP 1000 unfiltered with only client-side [filter] on top — capped to
    # top 100 like every other lookup in the app, re-queried by name as the
    # user types (search param), same load-on-demand shape as /customers-for-search.
    with get_cursor() as cursor:
        if search.strip():
            cursor.execute(
                "SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE Disabled = 0 AND DisplayName LIKE ? ORDER BY DisplayName",
                f"%{search}%",
            )
        else:
            cursor.execute("SELECT TOP 100 CustID, DisplayName FROM CustomerMaster WHERE Disabled = 0 ORDER BY DisplayName")
        return [LookupOption(label=r["DisplayName"] or "", value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/rows", response_model=EReferralPage)
def get_e_referral_rows(shop_client_id: int = 0, page: int = 0, page_size: int = 20) -> EReferralPage:
    # True server-side paging (this app's other "paginated" reports all fetch
    # the full filtered result set and page it client-side in the browser —
    # here the OFFSET/FETCH happens in SQL Server itself, so only one page's
    # worth of rows is ever transferred/held at a time regardless of how large
    # vw_web_eReferralVoucher is).
    where = ""
    params: list = []
    if shop_client_id:
        where = " Where IntroduceByClientID = ?"
        params.append(shop_client_id)
    with get_cursor() as cursor:
        cursor.execute(f"Select Count(*) Cnt from vw_web_eReferralVoucher{where}", *params)
        total = cursor.fetchone()[0]
        sql = (
            "Select RequestSrNo,ReferralVoucherNo,ShopName,ShopAddress,ShopCity,ShopContactPerson,ShopMobileNo,"
            "EmailAddress,IntroduceByClientID,IntroducedByShopName,IntroduceByName,IntroducedByShopCity,IntroducedByMobileNo,"
            "replace(convert(varchar(12),CreationDate,106),' ','-') CreationDate,"
            "replace(convert(varchar(20),CreationTime,113),' ','-') CreationTime,Remark "
            f"from vw_web_eReferralVoucher{where} Order By RequestSrNo Desc "
            "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        )
        cursor.execute(sql, *params, page * page_size, page_size)
        rows = rows_to_dicts(cursor)
    return EReferralPage(
        total=total,
        rows=[
            EReferralRow(
                request_sr_no=r["RequestSrNo"], referral_voucher_no=r["ReferralVoucherNo"] or "",
                shop_name=r["ShopName"] or "", shop_address=r["ShopAddress"] or "", shop_city=r["ShopCity"] or "",
                shop_contact_person=r["ShopContactPerson"] or "", shop_mobile_no=r["ShopMobileNo"] or "",
                email_address=r["EmailAddress"] or "", introduced_by_shop_name=r["IntroducedByShopName"] or "",
                introduce_by_name=r["IntroduceByName"] or "", introduced_by_shop_city=r["IntroducedByShopCity"] or "",
                introduced_by_mobile_no=r["IntroducedByMobileNo"] or "", creation_date=r["CreationDate"] or "",
                creation_time=r["CreationTime"] or "", remark=r["Remark"] or "",
            )
            for r in rows
        ],
    )
