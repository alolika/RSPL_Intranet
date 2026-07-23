"""Mirrors Section_Admin/Advance.aspx.vb (class Section_Admin_frmAdvance),
PaymentVoucher.aspx.vb (class Section_General_PaymentVoucher, shared page
for Payment/Prepayment voucher via ?IsPaymentVoucher=), VoucherDetail.aspx.vb
(outstanding-bills popup from LedgerBalance), and AddTag.aspx.vb (class
Section_Admin_AddTag, hardcoded to TagID=3 / KeyField='PayRegNo').

Note: PaymentVoucher.aspx's "credit account" combo (cmbCreditAcno) is
cosmetic — the real `PaymentVoucher`/`Proc_PrePaymentVoucher` stored procs
have no parameter for it at all (confirmed via sys.parameters); the source
even re-populates that same combo from a *different* table (BankMaster
instead of AccountMaster) depending on which code path runs. The print
preview's "through" bank name comes from PaymentRegister.PartyAcId, which
the proc sets internally, not from the combo's selected value.
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/admin/vouchers", tags=["admin-vouchers"])


class LookupOption(BaseModel):
    label: str
    value: int


class AdvanceVoucherRow(BaseModel):
    voucher_no: int
    date: str
    customer_id: int
    customer_name: str
    contact_person: str
    narration: str
    amount: float


class SaveAdvanceRequest(BaseModel):
    customer_id: int
    contact_person: str
    narration: str
    amount: float


class PaymentVoucherRow(BaseModel):
    voucher_no: int
    is_payment_voucher: bool
    date: str
    debit_account: str
    debit_account_id: int
    credit_account: str
    credit_account_id: int
    paid_to: str
    payment_for: str
    amount: float


class SavePaymentVoucherRequest(BaseModel):
    is_payment_voucher: bool
    debit_account: str
    paid_to: str
    payment_for: str
    amount: float


class OutstandingVoucherRow(BaseModel):
    voucher_no: int
    date: str
    sales_man: str
    bill_amt: float
    outstanding: float


class TagTypeOption(BaseModel):
    sr_no: int
    name: str


class TagValueOption(BaseModel):
    id: int
    name: str


class AssignedTagGroup(BaseModel):
    tag_type_sr_no: int
    tag_type_name: str
    values: list[TagValueOption]


class SaveTagsRequest(BaseModel):
    groups: list[AssignedTagGroup]


def _dmy(d) -> str:
    return d.strftime("%d-%b-%Y")


@router.get("/customers", response_model=list[LookupOption])
def get_customers(search: str = "") -> list[LookupOption]:
    # Found the actual source webmethod on a closer look: cmbcustomername on
    # Advance.aspx (EnableLoadOnDemand="True") calls ComboFill.asmx's
    # FillCustomerNameCombo — genuinely load-on-demand (TOP 50, GroupID=1,
    # label built from Name+MiddleName+LastName, not DisplayName), not the
    # plain unfiltered TOP-1000-by-DisplayName list this endpoint had been
    # using as an approximation. Rebuilt to match the confirmed source query
    # and capped to 100 (same as Referred By Customer/Customer Name) with
    # live search instead of loading a fixed list up front. Shared with
    # CustomerAns's Customer filter (admin_customers.py) — same generic
    # Top-100/search customer picker, not Advance-specific despite living in
    # this router.
    with get_cursor() as cursor:
        name_expr = "(ISNULL(Name,'') + ' ' + ISNULL(MiddleName,'') + ' ' + ISNULL(Lastname,''))"
        sql = f"SELECT TOP 100 CustID, {name_expr} AS FullName FROM CustomerMaster WHERE Disabled = 0 AND GroupId = 1"
        params: list = []
        if search.strip():
            sql += f" AND {name_expr} LIKE ?"
            params.append(f"%{search}%")
        sql += " ORDER BY FullName"
        cursor.execute(sql, *params)
        return [LookupOption(label=(r["FullName"] or "").strip(), value=r["CustID"]) for r in rows_to_dicts(cursor)]


@router.get("/advance-numbers", response_model=list[LookupOption])
def get_advance_voucher_numbers() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("SELECT TOP 500 VoucherNo FROM Advance ORDER BY Date DESC, VoucherNo DESC")
        return [LookupOption(label=str(r["VoucherNo"]), value=r["VoucherNo"]) for r in rows_to_dicts(cursor)]


def _advance_row(r: dict) -> AdvanceVoucherRow:
    return AdvanceVoucherRow(
        voucher_no=r["VoucherNo"], date=r["Date"].isoformat() if r["Date"] else "", customer_id=r["CustId"],
        customer_name=r["Displayname"] or "", contact_person=r["ContactPerson"] or "",
        narration=r["Narration"] or "", amount=float(r["Amount"] or 0),
    )


@router.get("/advance/last", response_model=AdvanceVoucherRow | None)
def get_last_advance() -> AdvanceVoucherRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TOP 1 A.VoucherNo, A.Date, A.CustId, Cm.Displayname, A.ContactPerson, A.Narration, A.Amount "
            "FROM Advance A INNER JOIN CustomerMaster Cm ON A.CustId = Cm.CustID ORDER BY A.Date DESC, A.VoucherNo DESC"
        )
        rows = rows_to_dicts(cursor)
    return _advance_row(rows[0]) if rows else None


@router.get("/advance/{voucher_no}", response_model=AdvanceVoucherRow | None)
def get_advance(voucher_no: int) -> AdvanceVoucherRow | None:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT A.VoucherNo, A.Date, A.CustId, Cm.Displayname, A.ContactPerson, A.Narration, A.Amount "
            "FROM Advance A INNER JOIN CustomerMaster Cm ON A.CustId = Cm.CustID WHERE A.VoucherNo = ?",
            voucher_no,
        )
        rows = rows_to_dicts(cursor)
    return _advance_row(rows[0]) if rows else None


@router.get("/customer-address/{customer_id}")
def get_customer_address_block(customer_id: int) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT cm.Floor, cm.FlatNo, cm.BldgName, cm.Road, cm.Landmark, cm.Area, cm.City, cm.Phno, "
            "cm.MobileNo, cm.Pincode, cm.GroupId FROM Custdependents cd INNER JOIN CustomerMaster cm "
            "ON cd.CustID = cm.CustID WHERE cd.DepID = 1 AND cm.CustID = ?",
            customer_id,
        )
        r = cursor.fetchone()
        if not r:
            return "Details Does Not Exist"
        row = dict(zip([c[0] for c in cursor.description], r))
        cursor.execute("SELECT Name FROM CustomerGroupMaster WHERE GroupId = ?", row["GroupId"])
        grp_row = cursor.fetchone()
        group_name = grp_row[0] if grp_row else ""

    lines = [f"Group:{group_name}"]
    for field in ("Floor", "FlatNo", "BldgName", "Road", "Landmark", "Area", "City", "Phno", "MobileNo", "Pincode"):
        if row.get(field):
            lines.append(str(row[field]))
    return "\n".join(lines) if len(lines) > 1 else "Details Does Not Exist"


@router.post("/advance")
def save_advance(body: SaveAdvanceRequest, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    # Was `EXEC pro_InsertUpdateAdvance 0, ?, 13, ...` (VoucherNo=0 tells the
    # proc to generate one) — confirmed live this always fails: the proc's
    # own body calls `EXEC Proc_NextVoucherNo 'ADVANCE',1,1,@VoucherNo output`
    # with only 4 positional args, but Proc_NextVoucherNo's real signature
    # (confirmed via sys.parameters / OBJECT_DEFINITION) is
    # (@TableName, @FirmID, @CounterNo, @BranchID, @MaxVoucherNo output) — 5
    # params. The missing @BranchID argument shifts @VoucherNo onto
    # @BranchID's position, which isn't declared OUTPUT, so SQL Server raises
    # error 8162 on every call. Even with that fixed, Proc_NextVoucherNo has
    # no case for 'ADVANCE' at all among its table-specific branches, so
    # @MaxVoucherNo would come back unset (still 0) regardless — this proc
    # pairing was never actually wired up for the Advance table. A
    # pre-existing bug in the shared database, not something introduced by
    # this migration; not something we can fix by changing our call.
    #
    # Insert directly instead, replicating pro_InsertUpdateAdvance's own
    # (otherwise-correct) INSERT statement and Expiry calculation
    # byte-for-byte, but computing the next voucher number the same way
    # get_last_advance/get_advance_voucher_numbers already treat "latest"
    # (ORDER BY Date DESC, VoucherNo DESC) rather than a raw MAX(VoucherNo) —
    # confirmed live that the numeric max (999912000157) is an orphaned
    # 2012-era value using an old branch/year-encoded numbering scheme, while
    # every recent row (e.g. 1167000030) follows a plain +1 sequence.
    with get_cursor() as cursor:
        cursor.execute("SELECT BillDate FROM Options")
        bill_date_row = cursor.fetchone()
        bill_date = bill_date_row[0] if bill_date_row else datetime.now()

        cursor.execute("SELECT TOP 1 VoucherNo FROM Advance ORDER BY Date DESC, VoucherNo DESC")
        last_row = cursor.fetchone()
        next_voucher_no = (last_row[0] if last_row else 0) + 1

        cursor.execute("SELECT ExpiryDatebased, ExpiryDefaultDays, ExpiryDefaultDate FROM paymenttypemaster WHERE TableName = 'Advance'")
        expiry_row = cursor.fetchone()
        if expiry_row and expiry_row[0]:
            expiry = expiry_row[2]
        else:
            expiry = bill_date + timedelta(days=(expiry_row[1] if expiry_row and expiry_row[1] is not None else 0))

        cursor.execute(
            "INSERT INTO Advance ("
            "Voucherno, Userid, CounterNo, Date, Expiry, Custid, Firmid, Cash, "
            "otherpay, Amount, BalanceAmt, RefundAmt, ContactPerson, "
            "ContactPersonId, Servicecharges, Narration, Time, ModUserid, "
            "ModCounterno, Settled, BlankCheque, EFFECTIVEDATE"
            ") VALUES (?, ?, 13, ?, ?, ?, 1, ?, 0, ?, ?, 0, ?, 0, 0, ?, GETDATE(), 0, 0, 0, 0, ?)",
            next_voucher_no, current_user.user_id, bill_date, expiry, body.customer_id,
            body.amount, body.amount, body.amount, body.contact_person, body.narration, bill_date,
        )
    return {"success": True, "voucherNo": next_voucher_no}


@router.get("/accounts", response_model=list[LookupOption])
def get_account_options(current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT A.Name, A.AccountId FROM AccountMaster A INNER JOIN Rspl_UserWiseLedger R "
            "ON A.AccountId = R.Ledgerid WHERE R.AssignUserid = ? AND A.Disabled = 0 AND R.Enabled = 1 "
            "AND R.CrDr = 1 ORDER BY A.Name",
            current_user.user_id,
        )
        return [LookupOption(label=r["Name"], value=r["AccountId"]) for r in rows_to_dicts(cursor)]


@router.get("/banks", response_model=list[LookupOption])
def get_bank_options(current_user: CurrentUser = Depends(get_current_user)) -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT A.Name, A.BankId FROM BankMaster A INNER JOIN Rspl_UserWiseLedger R "
            "ON A.BankId = R.Ledgerid WHERE R.AssignUserid = ? AND R.Enabled = 1 AND R.CrDr = 0 ORDER BY A.Name",
            current_user.user_id,
        )
        return [LookupOption(label=r["Name"], value=r["BankId"]) for r in rows_to_dicts(cursor)]


@router.get("/payment-voucher-numbers", response_model=list[LookupOption])
def get_payment_voucher_numbers(is_payment_voucher: bool = True) -> list[LookupOption]:
    # Was "ORDER BY payregNo DESC" — confirmed live that PaymentRegister has
    # an orphaned 2012-era numbering scheme (999912001999...) that numerically
    # outranks every genuinely recent voucher (current scheme e.g.
    # 1167000122, dated 2026), so the dropdown only ever showed 500 ancient
    # rows and every real recent voucher was unreachable. Same bug/fix as
    # Advance's voucher list (get_advance_voucher_numbers) — order by date
    # first, matching "latest" the way a user actually means it.
    table = "PaymentRegister" if is_payment_voucher else "PrePaymentRegister"
    with get_cursor() as cursor:
        cursor.execute(f"SELECT TOP 500 payregNo FROM {table} ORDER BY Date DESC, payregNo DESC")
        return [LookupOption(label=str(r["payregNo"]), value=r["payregNo"]) for r in rows_to_dicts(cursor)]


@router.get("/payment-voucher/{voucher_no}", response_model=PaymentVoucherRow | None)
def get_payment_voucher(voucher_no: int, is_payment_voucher: bool = True) -> PaymentVoucherRow | None:
    table = "PaymentRegister" if is_payment_voucher else "PrePaymentRegister"
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT pr.Date, pr.Amount, pr.PaymentFor, pr.paidTo, isnull(am.Name,'') AS Name, pr.AccountId, pr.PartyAcId "
            f"FROM {table} pr LEFT OUTER JOIN AccountMaster am ON pr.AccountId = am.AccountId WHERE pr.payregNo = ?",
            voucher_no,
        )
        r = cursor.fetchone()
        if not r:
            return None
        row = dict(zip([c[0] for c in cursor.description], r))
        cursor.execute("SELECT Name FROM BankMaster WHERE BankId = ?", row["PartyAcId"])
        bank_row = cursor.fetchone()
    return PaymentVoucherRow(
        voucher_no=voucher_no, is_payment_voucher=is_payment_voucher,
        date=row["Date"].isoformat() if row["Date"] else "", debit_account=row["Name"] or "",
        debit_account_id=row["AccountId"] or 0,
        credit_account=bank_row[0] if bank_row else "", credit_account_id=row["PartyAcId"] or 0,
        paid_to=row["paidTo"] or "",
        payment_for=row["PaymentFor"] or "", amount=float(row["Amount"] or 0),
    )


@router.post("/payment-voucher")
def save_payment_voucher(
    body: SavePaymentVoucherRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict:
    proc = "PaymentVoucher" if body.is_payment_voucher else "Proc_PrePaymentVoucher"
    table = "PaymentRegister" if body.is_payment_voucher else "PrePaymentRegister"
    paid_to = body.paid_to.replace("'", "")
    payment_for = body.payment_for.replace("'", "")
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT A.AccountId FROM AccountMaster A INNER JOIN Rspl_UserWiseLedger R ON A.AccountId = R.Ledgerid "
            "WHERE R.AssignUserid = ? AND A.Disabled = 0 AND R.Enabled = 1 AND R.CrDr = 1 AND A.Name = ?",
            current_user.user_id, body.debit_account,
        )
        acc_row = cursor.fetchone()
        debit_account_id = acc_row[0] if acc_row else 0
        cursor.execute(
            f"EXEC {proc} ?, 13, ?, ?, ?, ?",
            body.amount, current_user.user_id, payment_for, debit_account_id, paid_to,
        )
        rows = rows_to_dicts(cursor)
        # Both procs' SELECT actually aliases this column "Resultmsg"
        # (lowercase m) — confirmed live. rows_to_dicts() preserves SQL
        # Server's exact column casing and a plain dict lookup is
        # case-sensitive, so "ResultMsg" (capital M) never matched, and this
        # KeyError'd on every single save attempt. Same class of
        # case-sensitivity trap already found elsewhere in this project.
        message = rows[0].get("Resultmsg", "Voucher saved.") if rows else "Voucher saved."
        # Was "SELECT MAX(payregNo)" — confirmed live that PaymentRegister
        # has an orphaned 2012-era numbering scheme (999912001999) that
        # numerically outranks every real voucher including the one just
        # inserted, so this always reported the SAME ancient wrong voucher
        # number back to the frontend on every single save (breaking the
        # print preview and "Add Tag" handoff, which both key off this
        # value). The proc itself already guards against this internally
        # (PaymentVoucher's own @Payregno calc is scoped to
        # "Date>='01-apr-2022'") — mirroring that same "latest by date" logic
        # here instead of a raw MAX, consistent with how Advance's own
        # voucher lookups were fixed earlier.
        cursor.execute(f"SELECT TOP 1 payregNo FROM {table} ORDER BY Date DESC, payregNo DESC")
        voucher_row = cursor.fetchone()
    return {"success": True, "voucherNo": voucher_row[0] if voucher_row else 0, "message": message}


@router.get("/outstanding/{cust_id}", response_model=list[OutstandingVoucherRow])
def get_outstanding_vouchers(cust_id: int) -> list[OutstandingVoucherRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT sm.VoucherNo, sm.Date, sl.Name AS SalesMan, sm.TotalAmount AS BillAmt, sm.Outstanding "
            "FROM SalesMaster sm INNER JOIN SalesDetail sd ON sd.VoucherNo = sm.VoucherNo "
            "INNER JOIN Salesman sl ON sl.SalesmanId = sd.SalesmanId "
            "WHERE sm.Outstanding > 0 AND sm.CustId = ?",
            cust_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        OutstandingVoucherRow(
            voucher_no=r["VoucherNo"], date=r["Date"].isoformat() if r["Date"] else "",
            sales_man=r["SalesMan"] or "", bill_amt=float(r["BillAmt"] or 0), outstanding=float(r["Outstanding"] or 0),
        )
        for r in rows
    ]


_ADD_TAG_TAG_ID = 3  # TagOnTable.TagID where KeyField = 'PayRegNo'


@router.get("/tag-types", response_model=list[TagTypeOption])
def get_tag_types() -> list[TagTypeOption]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TagType, SrNO FROM tagUsingTable WHERE Disabled = 0 AND TagId = ? ORDER BY TagType",
            _ADD_TAG_TAG_ID,
        )
        return [TagTypeOption(sr_no=r["SrNO"], name=r["TagType"]) for r in rows_to_dicts(cursor)]


@router.get("/tag-type-values", response_model=list[TagValueOption])
def get_tag_type_values(tag_type_sr_no: int, search: str = "") -> list[TagValueOption]:
    if tag_type_sr_no == 8:
        # Was a fully unbounded "SELECT DISTINCT ... FROM CustomerMaster" —
        # confirmed live that's 80,902 rows and ~3.2s server-side alone
        # (before JSON-serializing and rendering all of them as checkboxes
        # in the browser), returned in full every time "Customer" was picked
        # as the tag type, even before typing any search text. Capped to
        # Top 100 + search, same load-on-demand shape as every other
        # customer picker in this app (e.g. Advance's getCustomers).
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT TOP 100 Displayname AS Name, CustID AS ID FROM CustomerMaster "
                "WHERE Disabled = 0 AND (? = '' OR Displayname LIKE ?) ORDER BY Displayname",
                search, f"%{search}%",
            )
            return [TagValueOption(id=r["ID"], name=r["Name"] or "") for r in rows_to_dicts(cursor)]

    with get_cursor() as cursor:
        cursor.execute(
            "SELECT QueryString FROM tagUsingTable WHERE Disabled = 0 AND TagId = ? AND SrNO = ?",
            _ADD_TAG_TAG_ID, tag_type_sr_no,
        )
        row = cursor.fetchone()
        if not row:
            return []
        cursor.execute(row[0])
        # tagUsingTable.QueryString aliases the id column inconsistently across
        # tag types ("Id" for most, "ID" for User/City) — look up case-insensitively.
        results = []
        for r in rows_to_dicts(cursor):
            id_key = next(k for k in r if k.lower() == "id")
            results.append(TagValueOption(id=r[id_key], name=r["Name"] or ""))
        return results


@router.get("/assigned-tags/{voucher_no}", response_model=list[AssignedTagGroup])
def get_assigned_tags(voucher_no: int) -> list[AssignedTagGroup]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT TagsMasterAttributeID, TagsMasterBasedData, TagsKey FROM TagValues "
            "WHERE TagId = ? AND KeyValue = ?",
            _ADD_TAG_TAG_ID, voucher_no,
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return []
        attr_ids = row[0].split("~")
        master_data = row[1].split("~")
        keys = row[2].split("~")

        groups: list[AssignedTagGroup] = []
        for i, attr_id in enumerate(attr_ids):
            cursor.execute(
                "SELECT TagType FROM tagUsingTable WHERE SrNo = ? AND TagId = ?", attr_id, _ADD_TAG_TAG_ID
            )
            type_row = cursor.fetchone()
            tag_type_name = type_row[0] if type_row else ""
            names = master_data[i].split(",") if i < len(master_data) else []
            ids = keys[i].split(",") if i < len(keys) else []
            values = [
                TagValueOption(id=int(v_id), name=name)
                for name, v_id in zip(names, ids)
                if v_id.strip().lstrip("-").isdigit()
            ]
            groups.append(AssignedTagGroup(tag_type_sr_no=int(attr_id), tag_type_name=tag_type_name, values=values))
    return groups


@router.post("/{voucher_no}/tags")
def save_tags(voucher_no: int, body: SaveTagsRequest) -> dict:
    attr_ids = "~".join(str(g.tag_type_sr_no) for g in body.groups)
    master_data = "~".join(",".join(v.name for v in g.values) for g in body.groups)
    keys = "~".join(",".join(str(v.id) for v in g.values) for g in body.groups)

    with get_cursor() as cursor:
        cursor.execute(
            "SELECT isnull(SrNo,0) FROM TAGVALUES WHERE TagId = ? AND Keyvalue = ?", _ADD_TAG_TAG_ID, voucher_no
        )
        sr_row = cursor.fetchone()
        sr_no = sr_row[0] if sr_row else 0
        if sr_no:
            cursor.execute("DELETE FROM TagValues WHERE SrNo = ? AND KeyValue = ?", sr_no, voucher_no)
        if attr_ids and master_data and keys:
            cursor.execute(
                "EXEC WebProc_Insert_TagVlaues 3, ?, ?, ?, ?, 0", voucher_no, attr_ids, master_data, keys
            )
    return {"success": True}
