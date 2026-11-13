"""Schema-general customer debt, aging, and credit repayment analytics.

Derives all column bindings and joins dynamically from loaded business schemas:
1. Snapshot outstanding debt (total amount, unique active debtors, top-K table)
2. Debt aging buckets ([0-7, 8-15, >15] days) and overdue threshold (>K days)
3. Monthly storewide credit repayments (receipts strictly after order creation day)
4. Monthly per-customer [debt added, debt repaid] pairs via customer directory resolution
5. Unanswerable guard when a business schema lacks customer credit/payment tracking
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import pandas as pd

from zaic_2026_datamate.cleaner import clean_vietnamese_currency
from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates

_CANCEL_PATTERN = r"hủy|huỷ|huy|cancel"
_CREDIT_METHOD_PATTERN = r"\b(?:ghi\s*no|no|debt|mua\s*chiu|cong\s*no)\b"
_PHONE_SUFFIX_RE = re.compile(
    r"\s*[-–(]?\s*(?:(?:\+84|0)[\d\s.-]{8,14}|\d{3,4}[\s.-]*\d{3}[\s.-]*\d{3,4})\s*\)?$"
)

_TELEX_DEBT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\btrar\b", "tra"),
    (r"\btraww\b", "tra"),
    (r"\bbowts\b", "bot"),
    (r"\bchiuj\b", "chiu"),
    (r"\bdduwowcj\b", "duoc"),
    (r"\bnowj\b", "no"),
    (r"\bkhachs\b", "khach"),
    (r"\bddeens\b", "den"),
    (r"\btieenf\b", "tien"),
    (r"\bcuwar\b", "cua"),
    (r"\bthieeus\b", "thieu"),
    (r"\bnhiuf\b", "nhieu"),
    (r"\bbao\s+nhiu\b", "bao nhieu"),
    (r"\bnhiu\b", "nhieu"),
    (r"\bbnhiu\b", "bao nhieu"),
    (r"\bbn\b", "bao nhieu"),
)

_EXCLUDED_NON_DEBT_TERMS = (
    "nha cung cap",
    "ncc",
    "tra gop",
    "tien gop",
    "ky gop",
    "lai suat",
    "lai gop",
    "lai rong",
    "loi nhuan",
    "gia von",
    "ton kho",
    "trong kho",
    "bao hanh",
    "han su dung",
    "han dung",
    "giao tre",
    "phan tram",
    "ty le",
    "ti le",
)

_GUEST_NAMES = frozenset(
    {"", "nan", "none", "khách lẻ", "khach le", "vang lai", "le", "khach"}
)


def strip_accents_norm(text: Any) -> str:
    """Lowercase, strip Vietnamese diacritics, and collapse whitespace."""
    raw = str(text or "").strip().lower().replace("đ", "d")
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFD", raw)
        if unicodedata.category(ch) != "Mn"
    )
    return " ".join(stripped.split())


def normalize_debt_text(question: str) -> str:
    """Normalize accents and common Vietnamese Telex typos in debt questions."""
    norm = strip_accents_norm(question)
    for pattern, repl in _TELEX_DEBT_REPLACEMENTS:
        norm = re.sub(pattern, repl, norm)
    return " ".join(norm.split())


def _find_col(cols_map: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    """Return actual column name matching the first candidate in priority order."""
    for cand in candidates:
        if cand in cols_map:
            return cols_map[cand]
    return None


def _to_numeric_series(series: pd.Series) -> pd.Series:
    """Convert numeric or formatted Vietnamese currency strings to float."""
    num = pd.to_numeric(series, errors="coerce")
    if series.notna().sum() > 0 and num.isna().sum() > 0:
        need = num.isna() & series.notna()
        cleaned = series[need].apply(clean_vietnamese_currency)
        num = num.copy()
        num.loc[need] = pd.to_numeric(cleaned, errors="coerce")
    return num.fillna(0.0)


def _select_primary_order_table(context: BusinessContext) -> tuple[str | None, bool]:
    """Identify the primary customer order/sales header table and F&B combined flag."""
    non_sales_doc_kw = (
        "nhap",
        "phieu_chi",
        "chi_phi",
        "tra_hang",
        "the_kho",
        "lich_su",
        "bao_cao",
        "so_quy",
        "thanh_toan",
        "phieu_thu",
        "han_no",
        "giao_hang",
        "lo_hang",
        "bang_gia",
    )
    entity_master_kw = (
        "san_pham",
        "hang_hoa",
        "khach_hang",
        "nhan_vien",
        "chi_nhanh",
        "nha_cung_cap",
        "don_vi",
        "quy_doi",
        "ton_kho",
        "danh_muc",
        "thuoc",
    )
    candidates: list[str] = []
    for tname in context.tables:
        tl = tname.lower()
        if tl == "ban_hang_tong_hop" or tl.startswith("ban_hang_tong_hop_"):
            return tname, True
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        if any(k in tl for k in non_sales_doc_kw):
            continue
        has_explicit_order_doc = bool(
            re.search(r"(?:^|_)(?:hoa_don|don_hang)(?:$|_)", tl)
        )
        # Guard against cross-boundary false match like ban_hang + hoa_don -> hang_hoa
        if not has_explicit_order_doc and any(k in tl for k in entity_master_kw):
            continue
        if any(
            k in tl
            for k in (
                "don_hang",
                "hoa_don",
                "ban_hang",
                "doanh_thu",
            )
        ):
            candidates.append(tname)

    if not candidates:
        return None, False

    def _score(tname: str) -> tuple[int, int, str]:
        tl = tname.lower()
        is_detail = int("chi_tiet" in tl)
        has_header_kw = int(
            any(k in tl for k in ("hoa_don", "don_hang", "so_ban_hang"))
        )
        return (is_detail, -has_header_kw, tname)

    candidates.sort(key=_score)
    return candidates[0], False


@dataclass
class DebtLedgerSchema:
    """Resolved credit orders, payment receipts, and customer directory."""

    orders_all: pd.DataFrame
    credit_orders: pd.DataFrame | None
    id_col: str | None
    cid_col: str | None
    cust_name_col: str | None
    pay_df: pd.DataFrame | None
    p_oid_col: str | None
    p_val_col: str | None
    cust_name_map: dict[str, str]
    name_to_cids: dict[str, list[str]]
    has_debt_tracking: bool
    is_fnb_ambiguous: bool


def _build_customer_directory(
    context: BusinessContext,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build cid -> clean_name and norm(clean_name) -> unique cid list mappings."""
    cid_to_name: dict[str, str] = {}
    name_to_cids: dict[str, set[str]] = {}
    id_cands = (
        "ma_khach_hang",
        "ma_kh",
        "customer_id",
        "ma_khach_le_si",
        "kh",
        "ma_khach",
        "makh",
    )
    name_cands = (
        "ten_khach_hang",
        "ten_goi",
        "ten_kh",
        "ho_ten",
        "ten_khach",
        "khach_hang_ten",
        "khach_hang",
        "khach_le_si",
        "khach",
        "kh",
        "ten",
    )
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        if not any(k in tl for k in ("khach", "customer")):
            continue
        if any(
            k in tl
            for k in (
                "hoa_don",
                "don_hang",
                "chi_tiet",
                "phieu_nhap",
                "tra_hang",
                "so_quy",
                "the_kho",
            )
        ):
            continue
        kh_cols = {c.lower(): c for c in df.columns}
        k_id = _find_col(kh_cols, id_cands)
        # Ensure name column is distinct from id column when both 'kh' and 'ma_*' exist
        rem_name_cands = tuple(c for c in name_cands if kh_cols.get(c) != k_id)
        k_nm = _find_col(kh_cols, rem_name_cands)
        if not k_id or not k_nm:
            continue
        for cid_raw, nm_raw in zip(df[k_id], df[k_nm], strict=False):
            sid = str(cid_raw).strip()
            snm = str(nm_raw).strip()
            if (
                not sid
                or not snm
                or sid.lower() in ("nan", "none")
                or snm.lower() in ("nan", "none")
            ):
                continue
            clean_nm = _PHONE_SUFFIX_RE.sub("", snm).strip()
            if not clean_nm:
                continue
            cid_to_name[sid] = clean_nm
            nm_key = strip_accents_norm(clean_nm)
            if nm_key and nm_key not in _GUEST_NAMES:
                name_to_cids.setdefault(nm_key, set()).add(sid)

    return cid_to_name, {k: sorted(v) for k, v in name_to_cids.items()}


def _resolve_payment_table(
    context: BusinessContext,
    order_ids: set[str],
) -> tuple[pd.DataFrame | None, str | None, str | None]:
    """Find payment receipt table and resolve (oid_col, val_col) with parsed pay_ts/pay_day/pay_val."""
    ts_cands = (
        "thoi_gian",
        "thoi_gian_thu",
        "ngay_gio_thu",
        "thoi_diem",
        "ngay_thu",
        "ngay_tt",
    )
    val_cands = ("gia_tri", "so_tien_thu", "tien_thu", "so_tien", "tien", "amount")
    oid_cands = (
        "ma_hoa_don",
        "ma_don_hang",
        "ma_bill",
        "ma_giao_dich",
        "so_hd",
        "so_ct",
        "ma_don",
        "order_id",
        "so_hoa_don",
        "ma_chung_tu",
        "bill",
        "ma_phieu",
        "ma_gd",
    )
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        if "nhap" in tl or "han_no" in tl:
            continue
        if not any(k in tl for k in ("so_quy", "thanh_toan", "phieu_thu")):
            continue
        pcols = {c.lower(): c for c in df.columns}
        p_ts = _find_col(pcols, ts_cands)
        p_val = _find_col(pcols, val_cands)
        if not p_ts or not p_val:
            continue

        # Choose order FK column by priority + actual value overlap with order IDs
        avail_oids = [pcols[c] for c in oid_cands if c in pcols]
        chosen_oid: str | None = None
        if order_ids:
            best_overlap = 0
            for col in avail_oids:
                overlap = int(df[col].astype(str).str.strip().isin(order_ids).sum())
                if overlap > best_overlap:
                    best_overlap = overlap
                    chosen_oid = col
        elif avail_oids:
            chosen_oid = avail_oids[0]
        if chosen_oid is None:
            continue

        pdf = df.copy()
        pdf["_pay_ts"] = parse_mixed_excel_dates(pdf[p_ts])
        pdf["_pay_day"] = pdf["_pay_ts"].dt.normalize()
        pdf["_pay_val"] = _to_numeric_series(pdf[p_val])
        return pdf, chosen_oid, p_val
    return None, None, None


def extract_debt_ledger(context: BusinessContext) -> DebtLedgerSchema | None:
    """Resolve order ledger, payment receipts, and debt-tracking capability from schema."""
    ord_table_name, is_fnb = _select_primary_order_table(context)
    if is_fnb:
        return DebtLedgerSchema(
            orders_all=pd.DataFrame(),
            credit_orders=None,
            id_col=None,
            cid_col=None,
            cust_name_col=None,
            pay_df=None,
            p_oid_col=None,
            p_val_col=None,
            cust_name_map={},
            name_to_cids={},
            has_debt_tracking=False,
            is_fnb_ambiguous=True,
        )
    if not ord_table_name or ord_table_name not in context.tables:
        return None

    df_raw = context.tables[ord_table_name].copy()
    cols = {c.lower(): c for c in df_raw.columns}

    pay_col = _find_col(
        cols,
        (
            "phuong_thuc_thanh_toan",
            "phuong_thuc_tt",
            "hinh_thuc_thanh_toan",
            "tra_bang",
            "thanh_toan_bang",
        ),
    )
    balance_col = _find_col(
        cols,
        (
            "con_no",
            "du_no",
            "cong_no",
            "con_lai",
            "con_phai_thu",
            "khach_da_tra",
            "da_tra",
            "da_thu",
        ),
    )
    id_col = _find_col(
        cols,
        (
            "order_id",
            "ma_hoa_don",
            "ma_don_hang",
            "ma_chung_tu",
            "so_phieu",
            "so_ct",
            "so_hoa_don",
            "bill",
            "don_so",
            "ma_bill",
            "ma_don",
            "stt",
            "id",
        ),
    )

    order_id_set = set(df_raw[id_col].astype(str).str.strip()) if id_col else set()
    pay_df, p_oid_col, p_val_col = _resolve_payment_table(context, order_id_set)

    has_tracking = bool(pay_col or balance_col or pay_df is not None)
    if not has_tracking:
        return DebtLedgerSchema(
            orders_all=df_raw,
            credit_orders=None,
            id_col=id_col,
            cid_col=None,
            cust_name_col=None,
            pay_df=None,
            p_oid_col=None,
            p_val_col=None,
            cust_name_map={},
            name_to_cids={},
            has_debt_tracking=False,
            is_fnb_ambiguous=False,
        )

    # Prefer date-first column or timestamp column for exact calendar-day alignment
    ts_col = _find_col(
        cols,
        (
            "ngay_dat",
            "ngay_lap",
            "ngay_ban",
            "ngay_hd",
            "ngay_don_hang",
            "ngay_tao",
            "thoi_gian",
            "gio_tao_don",
            "thoi_gian_lap",
            "ngay_gio",
            "thoi_gian_tao",
            "ngay",
        ),
    )
    tot_col = _find_col(
        cols,
        (
            "tong_sau_giam_gia",
            "tong_thanh_toan",
            "tong_tien",
            "tong_bill",
            "tong_don",
            "phai_thu",
            "thanh_tien",
            "tien_hang_truoc_giam",
        ),
    )
    if not ts_col or not tot_col:
        return None

    if id_col and df_raw[id_col].duplicated().any():
        df_raw = df_raw.groupby(id_col, as_index=False).first()

    df_raw["_order_ts"] = parse_mixed_excel_dates(df_raw[ts_col])
    df_raw["_order_day"] = df_raw["_order_ts"].dt.normalize()
    df_raw["_order_total"] = _to_numeric_series(df_raw[tot_col])

    status_col = _find_col(
        cols,
        (
            "trang_thai",
            "tinh_trang",
            "tinh_trang_don",
            "xu_ly",
            "status",
            "trang_thai_giao",
            "ghi_chu_tt",
        ),
    )
    orders = df_raw
    if status_col:
        orders = orders[
            ~orders[status_col]
            .astype(str)
            .str.lower()
            .str.contains(_CANCEL_PATTERN, na=False)
        ].copy()

    if pay_col:
        pay_norm = orders[pay_col].map(strip_accents_norm)
        credit_orders = orders[
            pay_norm.str.contains(_CREDIT_METHOD_PATTERN, regex=True, na=False)
        ].copy()
    elif "cong_no" in cols:
        credit_orders = orders[_to_numeric_series(orders[cols["cong_no"]]) > 0].copy()
    else:
        return None

    cid_col = _find_col(
        cols,
        (
            "ma_khach_hang",
            "ma_kh",
            "customer_id",
            "ma_kh_mua",
            "ma_khach_mua",
            "ma_khach",
            "makh",
            "so_kh",
            "kh",
        ),
    )
    cust_name_col = _find_col(
        cols,
        (
            "ten_goi",
            "ten_khach_hang",
            "khach_hang",
            "ten_kh",
            "khach_hang_ten",
            "khach_le_si",
            "khach",
        ),
    )
    if cust_name_col == cid_col:
        cust_name_col = None

    cid_to_name, name_to_cids = _build_customer_directory(context)

    return DebtLedgerSchema(
        orders_all=df_raw,
        credit_orders=credit_orders,
        id_col=id_col,
        cid_col=cid_col,
        cust_name_col=cust_name_col,
        pay_df=pay_df,
        p_oid_col=p_oid_col,
        p_val_col=p_val_col,
        cust_name_map=cid_to_name,
        name_to_cids=name_to_cids,
        has_debt_tracking=True,
        is_fnb_ambiguous=False,
    )


def _compute_active_debt_orders(
    ledger: DebtLedgerSchema, cutoff_date_str: str
) -> tuple[pd.DataFrame | None, pd.Timestamp]:
    """Return active credit orders with positive remaining balance as of cutoff_date_str."""
    cutoff_day = pd.to_datetime(cutoff_date_str).normalize()
    cutoff_eod = cutoff_day + pd.Timedelta(hours=23, minutes=59, seconds=59)

    assert ledger.credit_orders is not None
    orders = ledger.credit_orders[
        ledger.credit_orders["_order_day"] <= cutoff_day
    ].copy()

    if ledger.pay_df is not None and ledger.p_oid_col and ledger.id_col:
        pays = ledger.pay_df[ledger.pay_df["_pay_ts"] <= cutoff_eod]
        paid_per_order = pays.groupby(pays[ledger.p_oid_col].astype(str).str.strip())[
            "_pay_val"
        ].sum()
        orders["_paid"] = (
            orders[ledger.id_col]
            .astype(str)
            .str.strip()
            .map(paid_per_order)
            .fillna(0.0)
        )
    else:
        cols = {c.lower(): c for c in orders.columns}
        da_tra_col = _find_col(cols, ("khach_da_tra", "da_tra", "da_thu"))
        if da_tra_col is None:
            return None, cutoff_day
        orders["_paid"] = _to_numeric_series(orders[da_tra_col])

    orders["_debt"] = orders["_order_total"] - orders["_paid"]
    active = orders[orders["_debt"] > 0].copy()
    return active, cutoff_day


def _is_cutoff_before_business_start(
    ledger: DebtLedgerSchema, cutoff_day: pd.Timestamp
) -> bool:
    """Return True if cutoff_day falls strictly before any recorded transaction."""
    min_order = ledger.orders_all["_order_day"].dropna().min()
    if pd.isna(min_order):
        return False
    return bool(cutoff_day < min_order)


def calculate_debt_snapshot_metric(
    context: BusinessContext,
    cutoff_date_str: str,
    metric_type: str = "total_amount",
    top_k: int = 5,
    days_threshold: int | None = None,
) -> tuple[str, Any, str | None]:
    """Compute snapshot debt metrics ('total_amount', 'customer_count', 'top_k_table', 'aging_buckets', 'aging_gt_days')."""
    ledger = extract_debt_ledger(context)
    if ledger is None:
        return "abstain", None, "Không xác định được schema công nợ"
    if ledger.is_fnb_ambiguous:
        return (
            "abstain",
            None,
            "Bảng bán hàng tổng hợp F&B không đủ độ tin cậy để chốt công nợ tại thời điểm",
        )
    if not ledger.has_debt_tracking:
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không ghi nhận công nợ và thanh toán của khách hàng",
        )
    if ledger.credit_orders is None:
        return "abstain", None, "Không đủ cột ngày hoặc tổng đơn để tính công nợ"

    active, cutoff_day = _compute_active_debt_orders(ledger, cutoff_date_str)
    if active is None:
        return (
            "abstain",
            None,
            "Không có dữ liệu thanh toán để chốt công nợ tại thời điểm",
        )
    if _is_cutoff_before_business_start(ledger, cutoff_day):
        return (
            "unanswerable",
            None,
            "Ngày chốt nằm trước dữ liệu hoạt động của doanh nghiệp",
        )

    if metric_type == "aging_buckets":
        ages = (cutoff_day - active["_order_day"]).dt.days
        b0_7 = int(round(active.loc[(ages >= 0) & (ages <= 7), "_debt"].sum()))
        b8_15 = int(round(active.loc[(ages >= 8) & (ages <= 15), "_debt"].sum()))
        b_gt15 = int(round(active.loc[ages > 15, "_debt"].sum()))
        return "list", [b0_7, b8_15, b_gt15], None

    if metric_type == "aging_gt_days" and days_threshold is not None:
        ages = (cutoff_day - active["_order_day"]).dt.days
        total = int(round(active.loc[ages > days_threshold, "_debt"].sum()))
        return "number", total, None

    if metric_type == "total_amount":
        return "number", int(round(active["_debt"].sum())), None

    if metric_type == "customer_count":
        if not ledger.cid_col:
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không ghi nhận mã khách hàng trên đơn",
            )
        valid_cids = active[ledger.cid_col].dropna().astype(str).str.strip()
        valid_cids = valid_cids[~valid_cids.str.lower().isin(_GUEST_NAMES)]
        return "number", int(valid_cids.nunique()), None

    if metric_type == "top_k_table":
        if not ledger.cid_col and not ledger.cust_name_col:
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không ghi nhận thông tin khách hàng trên đơn",
            )
        if ledger.cid_col and ledger.cust_name_map:
            mapped = (
                active[ledger.cid_col].astype(str).str.strip().map(ledger.cust_name_map)
            )
            if ledger.cust_name_col:
                mapped = mapped.fillna(active[ledger.cust_name_col])
            else:
                mapped = mapped.fillna(active[ledger.cid_col])
            active["_resolved_name"] = mapped
        elif ledger.cust_name_col:
            active["_resolved_name"] = active[ledger.cust_name_col]
        else:
            active["_resolved_name"] = active[ledger.cid_col]

        active["_resolved_name"] = (
            active["_resolved_name"]
            .astype(str)
            .str.replace(_PHONE_SUFFIX_RE, "", regex=True)
            .str.strip()
        )
        active = active[~active["_resolved_name"].str.lower().isin(_GUEST_NAMES)]
        top = (
            active.groupby("_resolved_name")["_debt"]
            .sum()
            .sort_values(ascending=False)
            .head(top_k)
        )
        rows = [[str(name), int(round(val))] for name, val in top.items()]
        return "table", {"columns": ["khach_hang", "so_tien_no"], "rows": rows}, None

    return "abstain", None, "Loại metric công nợ chưa hỗ trợ"


def _extract_cutoff_date_str(
    question: str, q_norm: str, as_of_date: str | None
) -> str | None:
    """Extract DD/MM/YYYY or YYYY-MM-DD cutoff date for debt snapshots and aging."""
    m_dmy = re.search(
        r"\b(?:ngay|toi|den|tai|het)\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        q_norm,
    )
    if m_dmy:
        return f"{int(m_dmy.group(3)):04d}-{int(m_dmy.group(2)):02d}-{int(m_dmy.group(1)):02d}"
    m_ymd = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", question)
    if m_ymd:
        return f"{m_ymd.group(1)}-{m_ymd.group(2)}-{m_ymd.group(3)}"
    if as_of_date and any(
        k in q_norm for k in ("hien tai", "den nay", "tinh den nay", "toi nay")
    ):
        return as_of_date[:10]
    return None


def _extract_month_ym(q_norm: str, as_of_date: str | None) -> str | None:
    """Extract YYYY-MM target month from question text or relative 'tháng này'."""
    m_my = re.search(r"\bthang\s*(\d{1,2})(?:\s*[/\-]|\s+nam\s+)(\d{4})\b", q_norm)
    if m_my:
        month, year = int(m_my.group(1)), int(m_my.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    if any(k in q_norm for k in ("thang nay", "thang ni", "th nay")) and as_of_date:
        return as_of_date[:7]
    return None


def _is_month_outside_domain(ledger: DebtLedgerSchema, month_ym: str) -> bool:
    """Return True if target month falls completely outside order and payment dates."""
    order_days = ledger.orders_all["_order_day"].dropna()
    min_d = order_days.min()
    max_d = order_days.max()
    if ledger.pay_df is not None:
        pay_days = ledger.pay_df["_pay_day"].dropna()
        if not pay_days.empty:
            p_min, p_max = pay_days.min(), pay_days.max()
            min_d = p_min if pd.isna(min_d) else min(min_d, p_min)
            max_d = p_max if pd.isna(max_d) else max(max_d, p_max)
    if pd.isna(min_d) or pd.isna(max_d):
        return False
    year, month = int(month_ym[:4]), int(month_ym[5:7])
    last_day = calendar.monthrange(year, month)[1]
    m_start = pd.Timestamp(year=year, month=month, day=1)
    m_end = pd.Timestamp(year=year, month=month, day=last_day)
    return bool(m_end < min_d or m_start > max_d)


def _month_cutoff_eod(month_ym: str, as_of_date: str | None) -> pd.Timestamp:
    """Upper inclusive timestamp cutoff for a target month capped at as_of_date."""
    year, month = int(month_ym[:4]), int(month_ym[5:7])
    last_day = calendar.monthrange(year, month)[1]
    m_end = pd.Timestamp(
        year=year, month=month, day=last_day, hour=23, minute=59, second=59
    )
    if as_of_date:
        as_of_eod = pd.to_datetime(as_of_date[:10]).normalize() + pd.Timedelta(
            hours=23, minutes=59, seconds=59
        )
        return min(m_end, as_of_eod)
    return m_end


def _calculate_monthly_credit_repayment(
    ledger: DebtLedgerSchema,
    month_ym: str,
    as_of_date: str | None,
) -> tuple[str, Any, str | None]:
    """Compute storewide credit repayments received in month_ym strictly after order placement day."""
    if not ledger.has_debt_tracking:
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không ghi nhận công nợ và thanh toán của khách hàng",
        )
    if ledger.is_fnb_ambiguous or ledger.credit_orders is None:
        return (
            "abstain",
            None,
            "Không đủ dữ liệu ghi nợ đáng tin cậy để tính tiền trả nợ",
        )
    if ledger.pay_df is None or not ledger.p_oid_col or not ledger.id_col:
        return (
            "abstain",
            None,
            "Không có bảng phiếu thu có ngày thu để tách tiền trả bớt nợ",
        )
    if _is_month_outside_domain(ledger, month_ym):
        return "unanswerable", None, "Tháng được hỏi nằm ngoài khoảng thời gian dữ liệu"

    cutoff_eod = _month_cutoff_eod(month_ym, as_of_date)
    p_sub = ledger.pay_df[
        (ledger.pay_df["_pay_ts"].dt.strftime("%Y-%m") == month_ym)
        & (ledger.pay_df["_pay_ts"] <= cutoff_eod)
    ].copy()
    if p_sub.empty:
        return "number", 0, None

    p_sub["_oid_key"] = p_sub[ledger.p_oid_col].astype(str).str.strip()
    o_meta = ledger.credit_orders[[ledger.id_col, "_order_day"]].copy()
    o_meta["_oid_key"] = o_meta[ledger.id_col].astype(str).str.strip()
    merged = p_sub.merge(o_meta[["_oid_key", "_order_day"]], on="_oid_key", how="inner")
    later_paid = merged.loc[merged["_pay_day"] > merged["_order_day"], "_pay_val"].sum()
    return "number", int(round(later_paid)), None


def _resolve_unique_customer_ids(ledger: DebtLedgerSchema, q_norm: str) -> str | None:
    """Resolve unambiguous single customer_id from khach_hang directory matching q_norm."""
    if not ledger.name_to_cids or not ledger.cid_col:
        return None
    matches = [
        nm
        for nm in ledger.name_to_cids
        if re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", q_norm)
    ]
    maximal = [
        nm
        for nm in matches
        if not any(other != nm and nm in other for other in matches)
    ]
    if len(maximal) != 1:
        return None
    cids = ledger.name_to_cids[maximal[0]]
    if len(cids) != 1:
        return None
    return cids[0]


def _calculate_customer_monthly_delta(
    ledger: DebtLedgerSchema,
    q_norm: str,
    month_ym: str,
    as_of_date: str | None,
) -> tuple[str, Any, str | None]:
    """Compute [debt_added, debt_repaid] for one unambiguously resolved customer in month_ym."""
    if not ledger.has_debt_tracking:
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không ghi nhận công nợ và thanh toán của khách hàng",
        )
    if ledger.is_fnb_ambiguous or ledger.credit_orders is None:
        return "abstain", None, "Không đủ dữ liệu công nợ đáng tin cậy"
    if (
        ledger.pay_df is None
        or not ledger.p_oid_col
        or not ledger.id_col
        or not ledger.cid_col
    ):
        return (
            "abstain",
            None,
            "Thiếu bảng phiếu thu hoặc mã khách hàng để tính biến động công nợ",
        )
    if _is_month_outside_domain(ledger, month_ym):
        return "unanswerable", None, "Tháng được hỏi nằm ngoài khoảng thời gian dữ liệu"

    target_cid = _resolve_unique_customer_ids(ledger, q_norm)
    if target_cid is None:
        return (
            "abstain",
            None,
            "Không xác định duy nhất khách hàng trong danh sách khách hàng",
        )

    cutoff_eod = _month_cutoff_eod(month_ym, as_of_date)
    cutoff_day = cutoff_eod.normalize()

    c_orders = ledger.credit_orders[
        ledger.credit_orders[ledger.cid_col].astype(str).str.strip() == target_cid
    ].copy()
    c_orders["_oid_key"] = c_orders[ledger.id_col].astype(str).str.strip()

    p_df = ledger.pay_df.copy()
    p_df["_oid_key"] = p_df[ledger.p_oid_col].astype(str).str.strip()
    pm = p_df.merge(c_orders[["_oid_key", "_order_day"]], on="_oid_key", how="inner")

    c_month = c_orders[
        (c_orders["_order_day"].dt.strftime("%Y-%m") == month_ym)
        & (c_orders["_order_day"] <= cutoff_day)
    ]
    c_month_ids = set(c_month["_oid_key"])
    same_day_init = pm.loc[
        pm["_oid_key"].isin(c_month_ids)
        & (pm["_pay_day"] == pm["_order_day"])
        & (pm["_pay_ts"] <= cutoff_eod),
        "_pay_val",
    ].sum()
    debt_added = int(round(c_month["_order_total"].sum() - same_day_init))

    later_repaid = pm.loc[
        (pm["_pay_ts"].dt.strftime("%Y-%m") == month_ym)
        & (pm["_pay_ts"] <= cutoff_eod)
        & (pm["_pay_day"] > pm["_order_day"]),
        "_pay_val",
    ].sum()
    return "list", [debt_added, int(round(later_repaid))], None


def resolve_debt_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Route and deterministically resolve verified debt, aging, and repayment queries."""
    if "so_no_so_no" in context.tables or "so_no_tra_gop" in context.tables:
        return "abstain", None, "Nhường xử lý cho sổ công nợ và trả góp chuyên biệt"

    q_norm = normalize_debt_text(question)

    if any(k in q_norm for k in _EXCLUDED_NON_DEBT_TERMS):
        return "abstain", None, "Không thuộc phạm vi công nợ khách hàng"

    if any(k in q_norm for k in ("tu thang", "toi thang", "den thang")):
        return "abstain", None, "Khoảng nhiều tháng công nợ ngoài phạm vi đơn tháng"

    # Must contain genuine word-bounded debt/credit signal
    has_debt_cue = bool(
        re.search(
            r"\b(?:tuoi\s+no|cong\s+no|du\s+no|con\s+no|con\s+thieu|thieu\s+tien|"
            r"mua\s+chiu|ghi\s+no|no\s+them|tra\s+(?:bot\s+)?no|thu\s+hoi|"
            r"chua\s+tra\s+het|no\s+cua\s+hang|no\s+tiem|no\s+nhieu\s+nhat|no\s+nhut|"
            r"no\s+lon\s+nhat|khach\s+no|khach\s+dang\s+no|dang\s+no|tien\s+no|"
            r"so\s+no|khoan\s+no|kieu\s+no)\b",
            q_norm,
        )
    )
    if not has_debt_cue:
        return "abstain", None, "Không khớp dấu hiệu công nợ"

    # Reject order-count queries like "có bao nhiêu đơn mua chịu" -> owned by order counter
    if (
        re.search(r"\b(?:bao nhieu|may)\s+don\b", q_norm)
        and "qua" not in q_norm
        and "hon" not in q_norm
    ):
        return "abstain", None, "Câu hỏi đếm số đơn chuyển cho bộ phân tích đơn hàng"

    # 1. Customer monthly [nợ thêm, trả được] delta
    asks_delta = bool(
        re.search(r"\b(?:no\s+them|ghi\s+no\s+them|mua\s+chiu\s+them)\b", q_norm)
        and re.search(r"\b(?:tra\s+bot\s+no|tra\s+no|tra\s+duoc|tra\s+dc)\b", q_norm)
    )
    if asks_delta:
        month_ym = _extract_month_ym(q_norm, as_of_date)
        if not month_ym:
            return (
                "abstain",
                None,
                "Không xác định được tháng cho biến động công nợ khách hàng",
            )
        ledger = extract_debt_ledger(context)
        if ledger is None:
            return "abstain", None, "Không xác định được sổ công nợ"
        res_t, res_v, res_r = _calculate_customer_monthly_delta(
            ledger, q_norm, month_ym, as_of_date
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.customer_monthly_delta"
        return res_t, res_v, res_r

    # 2. Storewide monthly credit repayments
    asks_repay = bool(
        re.search(
            r"\b(?:tra\s+bot\s+no|thu\s+hoi\b.*\bno\b|khach\s+tra\s+no\b.*\bmua\s+chiu|"
            r"thu\s+(?:duoc|dc)\b.*\bkhach\s+tra\s+no)\b",
            q_norm,
        )
    )
    if asks_repay:
        month_ym = _extract_month_ym(q_norm, as_of_date)
        if not month_ym:
            return "abstain", None, "Không xác định được tháng thu hồi công nợ"
        ledger = extract_debt_ledger(context)
        if ledger is None:
            return "abstain", None, "Không xác định được sổ công nợ"
        res_t, res_v, res_r = _calculate_monthly_credit_repayment(
            ledger, month_ym, as_of_date
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.monthly_repayment"
        return res_t, res_v, res_r

    # Remaining intents require an explicit snapshot cutoff date
    cutoff_str = _extract_cutoff_date_str(question, q_norm, as_of_date)
    if not cutoff_str:
        return "abstain", None, "Thiếu ngày chốt công nợ"

    # 3. Debt aging 3 buckets [0-7, 8-15, >15]
    if (
        "tuoi no" in q_norm or ("0" in q_norm and "8" in q_norm and "15" in q_norm)
    ) and ("0" in q_norm and "7" in q_norm and "8" in q_norm and "15" in q_norm):
        res_t, res_v, res_r = calculate_debt_snapshot_metric(
            context, cutoff_str, metric_type="aging_buckets"
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.aging_buckets"
        return res_t, res_v, res_r

    # 4. Debt aging > K days (excluding rolling window 'gần nhất' / 'vừa qua')
    if not any(k in q_norm for k in ("gan nhat", "vua qua", "tuan roi")):
        m_gt = re.search(
            r"\b(?:qua|hon|tren|cach\s+do\s+hon)\s*(\d+)\s*ngay\b",
            q_norm,
        )
        if m_gt:
            days = int(m_gt.group(1))
            res_t, res_v, res_r = calculate_debt_snapshot_metric(
                context,
                cutoff_str,
                metric_type="aging_gt_days",
                days_threshold=days,
            )
            if res_t != "abstain":
                context.last_code_path = "debt_analytics.aging_gt_days"
            return res_t, res_v, res_r

    # 5. Snapshot debt (top-K table, customer count, or total remaining balance)
    m_top = re.search(r"\btop\s*(\d+)\b", q_norm) or re.search(
        r"\b(\d+)\s*khach\b", q_norm
    )
    if (
        any(
            k in q_norm
            for k in (
                "nhung ai",
                "ai dang no",
                "moi nguoi",
                "so no cua tung nguoi",
                "tung nguoi",
                "tug ngui",
                "nhieu nhat",
                "nhut",
                "lon nhat",
                "liet ke",
                "danh sach",
                "bang",
                "top",
            )
        )
        and m_top
    ):
        k_val = int(m_top.group(1))
        res_t, res_v, res_r = calculate_debt_snapshot_metric(
            context, cutoff_str, metric_type="top_k_table", top_k=k_val
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.snapshot_top_k"
        return res_t, res_v, res_r

    has_amount_cue = any(
        k in q_norm for k in ("bao nhieu tien", "tong tien", "so tien", "nhieu tien")
    )
    if (
        re.search(r"\b(?:bao nhieu|may|so)\s+(?:khach|nguoi)\b", q_norm)
        or re.search(r"\bdem\b.*\b(?:khach|nguoi)\b", q_norm)
        or ("chua tra het no" in q_norm and not has_amount_cue)
    ):
        res_t, res_v, res_r = calculate_debt_snapshot_metric(
            context, cutoff_str, metric_type="customer_count"
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.snapshot_customer_count"
        return res_t, res_v, res_r

    if any(
        k in q_norm
        for k in (
            "tong",
            "bao nhieu",
            "nhieu tien",
            "con no",
            "con thieu",
            "thieu tien",
            "cong no",
            "kieu no",
        )
    ):
        res_t, res_v, res_r = calculate_debt_snapshot_metric(
            context, cutoff_str, metric_type="total_amount"
        )
        if res_t != "abstain":
            context.last_code_path = "debt_analytics.snapshot_total"
        return res_t, res_v, res_r

    return "abstain", None, "Không khớp mẫu công nợ"
