"""Specialized Deterministic Engine for Mobile & Installment Store (dev_014).

Handles:
- Trade-in phone discounts & participation rate (thu_may_cu)
- Order net payable after discount and trade-in (so_ban_hang with bot & khach_tra)
- Phone vs. accessory analytics (phone unit sales, average phone price, new vs. old split)
- Model sales counts & top model rankings (so_ban_hang with so_ban_hang_ghi_tat)
- Installment contract calculations (overdue contracts, customer overdue amounts, total unpaid balance)
- Warranty ticket counting and model warranty lookup (so_no_bao_hanh)
- Snapshot debt balances and active debtor count (so_no_so_no)
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.cleaner import clean_vietnamese_currency
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions


def strip_accents(s: str) -> str:
    """Strip accents and lowercase a string."""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )


def norm_item(s: Any) -> str:
    """Canonical key for item mapping."""
    if pd.isna(s):
        return ""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
        .replace(" ", "")
    )


def extract_dong_may(name: Any) -> str:
    """Extract model family per Rulebook Section 6:

    'dòng máy = phần tên hàng đứng trước dung lượng …GB; tên không có phần …GB thì dòng máy là cả tên'
    """
    m = re.match(r"^(.*?)\s+\d+GB\b", str(name), re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return str(name).strip()


def add_months(d: pd.Timestamp, months: int) -> tuple[int, int]:
    """Add calendar months to a timestamp."""
    y = d.year + (d.month + months - 1) // 12
    m = (d.month + months - 1) % 12 + 1
    return y, m


def get_installment_due_date(
    p_date: pd.Timestamp, due_day: int, k: int
) -> pd.Timestamp:
    """Compute the due date for the k-th installment."""
    y, m = add_months(p_date, k)
    max_day = calendar.monthrange(y, m)[1]
    return pd.Timestamp(year=y, month=m, day=min(due_day, max_day))


def parse_contracts(tg_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Parse raw so_no_tra_gop into structured contract objects with payment history."""
    contracts = []
    curr_c = None
    for _, row in tg_df.iterrows():
        if pd.notna(row.get("so_ky")):
            due_d = row.get("han_gop_ngay")
            due_day_val = int(due_d) if pd.notna(due_d) else 1
            curr_c = {
                "stt": row["stt"],
                "khach": str(row["khach"]).strip(),
                "ngay": pd.to_datetime(row["ngay"]),
                "so_ky": int(row["so_ky"]),
                "gop_thang": clean_vietnamese_currency(row["gop_thang"]) or 0.0,
                "due_day": due_day_val,
                "payments": [],
            }
            contracts.append(curr_c)
        if curr_c and pd.notna(row.get("ngay_gop")):
            curr_c["payments"].append(
                {
                    "ngay_gop": pd.to_datetime(row["ngay_gop"]),
                    "da_gop": clean_vietnamese_currency(row.get("da_gop")) or 0.0,
                }
            )
    return contracts


def _quarter_to_months(quarter_str: str | None) -> list[str]:
    """Expand 'YYYY-QN' string into its three 'YYYY-MM' month strings."""
    if not quarter_str:
        return []
    m = re.match(r"^(\d{4})-Q([1-4])$", quarter_str)
    if not m:
        return []
    year, q = int(m.group(1)), int(m.group(2))
    start_m = (q - 1) * 3 + 1
    return [f"{year:04d}-{mm:02d}" for mm in range(start_m, start_m + 3)]


def unify_debt_and_installment_ledgers(
    so_no_df: pd.DataFrame | None,
    tra_gop_df: pd.DataFrame | None,
    as_of_date: str | None = None,
) -> pd.DataFrame:
    """Combine general debt ledger (`so_no_so_no`) and installment ledger (`so_no_tra_gop`).

    Returns normalized rows up to `as_of_date` with columns:
    ['source', 'khach', 'khach_norm', 'orig_date', 'total_debt', 'paid_amount', 'remaining_debt'].
    """
    as_of = pd.to_datetime(as_of_date) if as_of_date else None
    rows: list[dict[str, Any]] = []

    if so_no_df is not None and not so_no_df.empty:
        entries: list[dict[str, Any]] = []
        curr: dict[str, Any] | None = None
        for _, r in so_no_df.iterrows():
            debt_amt = clean_vietnamese_currency(r.get("no"))
            ngay_val = pd.to_datetime(r.get("ngay"), errors="coerce")
            khach_val = str(r.get("khach") or "").strip()
            if debt_amt is not None and debt_amt > 0 and khach_val and khach_val.lower() != "nan":
                curr = {
                    "source": "so_no",
                    "khach": khach_val,
                    "khach_norm": strip_accents(khach_val),
                    "orig_date": ngay_val,
                    "total_debt": float(debt_amt),
                    "payments": [],
                }
                entries.append(curr)
            paid_amt = clean_vietnamese_currency(r.get("da_tra"))
            paid_date = pd.to_datetime(r.get("ngay_tra"), errors="coerce")
            if curr is not None and paid_amt and paid_amt > 0:
                if pd.isna(paid_date) or as_of is None or paid_date <= as_of:
                    curr["payments"].append(float(paid_amt))

        for entry in entries:
            if as_of is not None and pd.notna(entry["orig_date"]) and entry["orig_date"] > as_of:
                continue
            paid = sum(entry["payments"])
            rows.append(
                {
                    "source": "so_no",
                    "khach": entry["khach"],
                    "khach_norm": entry["khach_norm"],
                    "orig_date": entry["orig_date"],
                    "total_debt": entry["total_debt"],
                    "paid_amount": paid,
                    "remaining_debt": max(0.0, entry["total_debt"] - paid),
                }
            )

    if tra_gop_df is not None and not tra_gop_df.empty:
        for c in parse_contracts(tra_gop_df):
            if as_of is not None and pd.notna(c["ngay"]) and c["ngay"] > as_of:
                continue
            total_c = float(c["so_ky"] * c["gop_thang"])
            paid_c = sum(
                p["da_gop"]
                for p in c["payments"]
                if as_of is None or pd.isna(p["ngay_gop"]) or p["ngay_gop"] <= as_of
            )
            rows.append(
                {
                    "source": "tra_gop",
                    "khach": c["khach"],
                    "khach_norm": strip_accents(c["khach"]),
                    "orig_date": c["ngay"],
                    "total_debt": total_c,
                    "paid_amount": float(paid_c),
                    "remaining_debt": max(0.0, total_c - float(paid_c)),
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "source",
                "khach",
                "khach_norm",
                "orig_date",
                "total_debt",
                "paid_amount",
                "remaining_debt",
            ]
        )
    return pd.DataFrame(rows)


def _resolve_installment_as_of(
    as_of_date: str | None,
    context: BusinessContext,
    *date_series: pd.Series | None,
) -> pd.Timestamp:
    """Resolve as_of cutoff from explicit anchor, loaded calendar, or max ledger date."""
    if as_of_date:
        return pd.to_datetime(as_of_date)
    if context.calendar_df is not None and "date" in context.calendar_df.columns:
        cal_dates = pd.to_datetime(context.calendar_df["date"], errors="coerce").dropna()
        if not cal_dates.empty:
            return cal_dates.max()
    candidates = [
        pd.to_datetime(s, errors="coerce").dropna().max()
        for s in date_series
        if s is not None
    ]
    valid = [d for d in candidates if pd.notna(d)]
    return max(valid) if valid else pd.Timestamp.max


def resolve_unified_debt_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve general debt balances and active debtor counts using unified ledgers."""
    if "so_no_so_no" not in context.tables and "so_no_tra_gop" not in context.tables:
        return "abstain", None, "Không tìm thấy sổ công nợ hoặc sổ trả góp"

    t_norm = strip_accents(question)
    if not re.search(
        r"\b(?:con\s+no|du\s+no|cong\s+no|con\s+thieu|thieu\s+tien|no)\b", t_norm
    ):
        return "abstain", None, "Không phải câu hỏi công nợ chung"

    # Guard complex/unverified installment schedule or unsupported breakdowns
    if any(
        k in t_norm
        for k in (
            "tra gop",
            "tien gop",
            "ky gop",
            "qua han",
            "tre han",
            "cham gop",
            "cham",
            "chua toi han",
            "ke ca",
            "tuoi no",
            "nha cung cap",
            "ncc",
            "top",
            "liet ke",
            "danh sach",
            "bang",
        )
    ):
        return "abstain", None, "Dạng công nợ phức tạp cần kiểm tra lịch trả góp"

    m_dmy = re.search(
        r"\b(?:ngay|toi|den|tai|het)\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        t_norm,
    )
    m_ymd = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", question)
    if m_dmy:
        cutoff_ts = pd.Timestamp(
            year=int(m_dmy.group(3)),
            month=int(m_dmy.group(2)),
            day=int(m_dmy.group(1)),
        )
    elif m_ymd:
        cutoff_ts = pd.Timestamp(
            year=int(m_ymd.group(1)),
            month=int(m_ymd.group(2)),
            day=int(m_ymd.group(3)),
        )
    else:
        so_no_df = context.tables.get("so_no_so_no")
        tg_df = context.tables.get("so_no_tra_gop")
        cutoff_ts = _resolve_installment_as_of(
            as_of_date,
            context,
            so_no_df["ngay"] if so_no_df is not None and "ngay" in so_no_df else None,
            tg_df["ngay_gop"] if tg_df is not None and "ngay_gop" in tg_df else None,
            tg_df["ngay"] if tg_df is not None and "ngay" in tg_df else None,
        )

    if cutoff_ts == pd.Timestamp.max:
        cutoff_eod_str = None
    else:
        cutoff_eod = cutoff_ts.normalize() + pd.Timedelta(
            hours=23, minutes=59, seconds=59
        )
        cutoff_eod_str = cutoff_eod.strftime("%Y-%m-%d %H:%M:%S")

    unified = unify_debt_and_installment_ledgers(
        context.tables.get("so_no_so_no"),
        context.tables.get("so_no_tra_gop"),
        as_of_date=cutoff_eod_str,
    )
    if unified.empty:
        return "abstain", None, "Không có dữ liệu công nợ hợp nhất đến ngày xét"

    context.last_code_path = "mobile_installment.unify_debt_and_installment_ledgers"

    # 1. Active debtor count (deduplicated across ledgers)
    if re.search(
        r"\b(?:bao nhieu|bao nhiu|bnhiu|bn|may|so)\s+khach\b", t_norm
    ) or "con bao nhieu khach" in t_norm:
        active = unified[unified["remaining_debt"] > 0]
        return "number", int(active["khach_norm"].nunique()), None

    # 2. Specific customer remaining debt across unified ledgers
    all_names = sorted(
        {
            str(n).strip()
            for n in unified["khach_norm"].dropna()
            if str(n).strip() and str(n).strip() != "nan"
        }
    )
    phrase_matches = [
        nm for nm in all_names if re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", t_norm)
    ]
    maximal_matches = [
        nm
        for nm in phrase_matches
        if not any(other != nm and nm in other for other in phrase_matches)
    ]
    if len(maximal_matches) == 1:
        target_nm = maximal_matches[0]
        bal = unified.loc[unified["khach_norm"] == target_nm, "remaining_debt"].sum()
        return "number", int(round(bal)), None
    if len(maximal_matches) > 1:
        return "abstain", None, "Nhiều khách hàng trùng khớp trong sổ công nợ"

    m_cust_tok = re.search(
        r"\bkhach(?:\s+hang)?\s+(?!con\b|dang\b|mua\b|no\b|thieu\b|tra\b|hang\b|cua\b)([a-z0-9\s]{1,35}?)\s+(?:con\s+no|con\s+thieu|no|thieu|da\s+tra)\b",
        t_norm,
    )
    if m_cust_tok:
        tok = m_cust_tok.group(1).strip()
        if tok not in {"hang", "cua hang", "con", "dang", "dang con", "mua chiu"}:
            tok_matches = [
                nm
                for nm in all_names
                if re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", nm)
            ]
            if len(tok_matches) == 1:
                target_nm = tok_matches[0]
                bal = unified.loc[
                    unified["khach_norm"] == target_nm, "remaining_debt"
                ].sum()
                return "number", int(round(bal)), None
            return "abstain", None, "Tên khách hàng chưa đủ rõ để chốt công nợ"

    # 3. Storewide combined remaining debt
    if any(
        k in t_norm
        for k in ("tong", "bao nhieu", "bn", "bnhiu", "nhieu tien", "con no", "con thieu")
    ):
        tot = int(round(unified["remaining_debt"].sum()))
        return "number", tot, None

    return "abstain", None, "Không khớp mẫu công nợ hợp nhất"


def resolve_mobile_installment_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve mobile retail & installment questions using deterministic schema logic."""
    has_sales = "so_ban_hang" in context.tables
    has_ledgers = any(
        t in context.tables for t in ("so_no_tra_gop", "so_no_so_no", "so_no_bao_hanh", "thu_may_cu")
    )
    if not has_sales and not has_ledgers:
        return "abstain", None, "Không tìm thấy bảng so_ban_hang hoặc sổ công nợ"

    t_norm = strip_accents(question)
    p = parse_time_expressions(question, as_of_date=as_of_date)

    target_month = p.month_str
    if (
        not target_month
        and ("thang nay" in t_norm or "thang ni" in t_norm or "th nay" in t_norm)
        and as_of_date
    ):
        target_month = as_of_date[:7]

    # 1. Subjective question guard
    if any(k in t_norm for k in ["loi nhieu nhat", "lai nhieu nhat"]):
        return (
            "unanswerable",
            None,
            "Cửa hàng không ghi nhận giá vốn trên từng đơn bán lẻ",
        )

    # -------------------------------------------------------------------------
    # A. TRADE-IN PHONES (thu_may_cu)
    # -------------------------------------------------------------------------
    if "thu_may_cu" in context.tables and any(
        k in t_norm for k in ["thu may cu", "may cu thu lai"]
    ):
        if not any(k in t_norm for k in ["khach phai tra", "khach tra"]):
            tmc = context.tables["thu_may_cu"].copy()
            tmc["m"] = pd.to_datetime(tmc["ngay"]).dt.strftime("%Y-%m")

            # A1. Percentage of orders with trade-in: 'bnhiu % dh co thu may cu'
            if has_sales and any(k in t_norm for k in ["%", "phan tram"]) and any(
                k in t_norm for k in ["dh", "don hang", "don"]
            ):
                if target_month:
                    sbh = context.tables["so_ban_hang"]
                    is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
                    returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
                    sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)]
                    sub_sbh = sbh_clean[
                        pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m") == target_month
                    ]
                    total_orders = sub_sbh.groupby(["sheet_name", "stt"]).ngroups
                    sub_tmc = tmc[tmc["m"] == target_month]
                    orders_with_tmc = sub_tmc["tru_vao_stt_so_ban"].nunique()
                    if total_orders > 0:
                        return (
                            "percent",
                            round(orders_with_tmc / total_orders * 100, 2),
                            None,
                        )

            # A2. Total trade-in value in month: 'cong tien thu may cu', 'cac khoan tien thu may cu'
            if any(
                k in t_norm for k in ["cong", "tong", "het nhieu tien", "duoc bnhiu"]
            ):
                if target_month:
                    sub_tmc = tmc[tmc["m"] == target_month]
                    return "number", int(round(sub_tmc["gia_thu"].sum())), None

    # -------------------------------------------------------------------------
    # B. ORDER NET AMOUNT AFTER DISCOUNTS & TRADE-IN (khach_tra)
    # -------------------------------------------------------------------------
    if has_sales and any(k in t_norm for k in ["khach phai tra", "khach tra"]) and any(
        k in t_norm
        for k in [
            "tru tien bot",
            "tru bot",
            "may cu thu lai",
            "trung binh moi don",
            "binh quan mot don",
        ]
    ):
        sbh = context.tables["so_ban_hang"].copy()
        is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
        returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
        sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)].copy()
        sbh_clean["kt_val"] = sbh_clean["khach_tra"].apply(clean_vietnamese_currency)
        sbh_clean["m"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m")

        if target_month:
            sub = sbh_clean[sbh_clean["m"] == target_month]
            if not sub.empty:
                orders = sub.groupby(["sheet_name", "stt"])["kt_val"].max()
                if any(k in t_norm for k in ["trung binh", "binh quan"]):
                    return "number", int(round(orders.mean())), None
                if any(k in t_norm for k in ["tong cong", "het bao nhieu", "tong"]):
                    return "number", int(round(orders.sum())), None

    # -------------------------------------------------------------------------
    # B0. AVERAGE ORDER VALUE (AOV - Gross Revenue thanh_tien)
    # -------------------------------------------------------------------------
    if has_sales and any(k in t_norm for k in ["trung binh", "binh quan", "bq"]) and any(
        k in t_norm for k in ["moi don", "1 don", "mot don", "tren mot don", "moi dh", "1 dh", "tren 1 don"]
    ):
        sbh = context.tables["so_ban_hang"].copy()
        is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
        returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
        sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)].copy()
        sbh_clean["m"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m")
        if p.start_date and p.end_date:
            sbh_clean["d_str"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m-%d")
            sub = sbh_clean[(sbh_clean["d_str"] >= p.start_date) & (sbh_clean["d_str"] <= p.end_date)]
            order_totals = sub.groupby(["sheet_name", "stt"])["thanh_tien"].sum()
            if len(order_totals) > 0:
                aov = float(order_totals.sum() / len(order_totals))
                val = int(round(aov))
                return "number", val, None
        elif target_month:
            sub = sbh_clean[sbh_clean["m"] == target_month]
            order_totals = sub.groupby(["sheet_name", "stt"])["thanh_tien"].sum()
            if len(order_totals) > 0:
                aov = float(order_totals.sum() / len(order_totals))
                val = int(round(aov))
                return "number", val, None

    # -------------------------------------------------------------------------
    # C. INSTALLMENT CONTRACTS (so_no_tra_gop)
    # -------------------------------------------------------------------------
    if "so_no_tra_gop" in context.tables and any(
        k in t_norm
        for k in ["tra gop", "tien gop", "ky gop", "qua han gop", "cham gop"]
    ):
        tg_df = context.tables["so_no_tra_gop"]
        contracts = parse_contracts(tg_df)
        as_of = _resolve_installment_as_of(
            as_of_date,
            context,
            tg_df.get("ngay_gop"),
            tg_df.get("ngay"),
        )

        # C1. Single customer installments due count: 'khach X toi han may ky gop'
        if any(k in t_norm for k in ["toi han may ky", "den han may ky", "may ky gop"]):
            for c in contracts:
                if strip_accents(c["khach"]) in t_norm:
                    due_count = 0
                    for k in range(1, c["so_ky"] + 1):
                        d_due = get_installment_due_date(c["ngay"], c["due_day"], k)
                        if d_due <= as_of:
                            due_count += 1
                    return "number", due_count, None

        # C2. Single customer remaining balance: 'khach X con thieu bao nhieu', 'con lai bao nhieu tien chua gop'
        if any(k in t_norm for k in ["con thieu", "con lai", "chua gop"]):
            matched_customer = None
            for c in contracts:
                if strip_accents(c["khach"]) in t_norm:
                    matched_customer = c
                    break
            if matched_customer:
                total_c_val = matched_customer["so_ky"] * matched_customer["gop_thang"]
                paid_so_far = sum(
                    p["da_gop"]
                    for p in matched_customer["payments"]
                    if p["ngay_gop"] <= as_of
                )
                rem = max(0.0, total_c_val - paid_so_far)
                return "number", int(round(rem)), None

        # C3, C4, C5: Fragile storewide overdue and balance aggregates -> abstain under Silence is Free
        if any(
            k in t_norm
            for k in [
                "ke ca",
                "chua toi han",
                "tong so tien gop",
                "qua han",
                "tre han",
                "cham gop",
                "cham",
                "chua dong bao nhieu tien",
            ]
        ):
            return "abstain", None, "Complex installment aggregate abstained under Silence is Free"

    # -------------------------------------------------------------------------
    # C+. UNIFIED DEBT & INSTALLMENT LEDGER (so_no_so_no + so_no_tra_gop)
    # -------------------------------------------------------------------------
    if "so_no_so_no" in context.tables or "so_no_tra_gop" in context.tables:
        u_type, u_val, u_reason = resolve_unified_debt_query(
            question, context, as_of_date
        )
        if u_type != "abstain":
            return u_type, u_val, u_reason

    # -------------------------------------------------------------------------
    # D. WARRANTY (so_no_bao_hanh)
    # -------------------------------------------------------------------------
    if "so_no_bao_hanh" in context.tables and any(
        k in t_norm for k in ["bao hanh", "luot may", "luot khach đem may"]
    ):
        bh = context.tables["so_no_bao_hanh"].copy()
        bh["nhan_m"] = pd.to_datetime(bh["ngay_nhan"]).dt.strftime("%Y-%m")
        bh["mua_m"] = pd.to_datetime(bh["ngay_mua"]).dt.strftime("%Y-%m")
        as_of_dt = _resolve_installment_as_of(
            as_of_date,
            context,
            bh.get("ngay_nhan"),
            bh.get("ngay_mua"),
        )

        # D1. Machines sold in month X and brought to warranty: 'may ban ra thang X ... mang lai bao hanh'
        if any(k in t_norm for k in ["may ban ra", "ban thang", "ban ra thang"]):
            if target_month:
                sub = bh[
                    (bh["mua_m"] == target_month)
                    & (pd.to_datetime(bh["ngay_nhan"]) <= as_of_dt)
                ]
                return "number", int(sub["imei"].nunique()), None

        # D2. Top warrantied model in period: 'dong may nao ko nhiu luot bao hanh nhut'
        if any(k in t_norm for k in ["dong may nao", "may nao"]) and any(
            k in t_norm for k in ["nhieu luot", "nhiu luot", "cao nhat"]
        ):
            if p.quarter_str:
                q_months = _quarter_to_months(p.quarter_str)
                sub = bh[bh["nhan_m"].isin(q_months)]
                if not sub.empty:
                    return "string", sub["may"].value_counts().idxmax(), None

        # D3. Warranty ticket count in quarter / month
        if p.quarter_str:
            q_months = _quarter_to_months(p.quarter_str)
            sub = bh[bh["nhan_m"].isin(q_months)]
            if any(k in t_norm for k in ["%", "phan tram"]):
                if "so_ban_hang" in context.tables and "so_ban_hang_ghi_tat" in context.tables:
                    sbh = context.tables["so_ban_hang"].copy()
                    is_ret = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
                    ret_ords = set(zip(sbh.loc[is_ret, "sheet_name"], sbh.loc[is_ret, "stt"]))
                    sbh_cl = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(ret_ords)].copy()
                    gt_map = {}
                    for _, r in context.tables["so_ban_hang_ghi_tat"].iterrows():
                        gt_map[norm_item(r["ghi_tat"])] = r["loai"]
                        gt_map[norm_item(r["ten_hang"])] = r["loai"]
                    sbh_cl["m"] = pd.to_datetime(sbh_cl["ngay"]).dt.strftime("%Y-%m")
                    sbh_cl["loai"] = sbh_cl["hang"].apply(norm_item).map(lambda x: gt_map.get(x, "khac"))
                    sub_may = sbh_cl[sbh_cl["m"].isin(q_months) & sbh_cl["loai"].isin(["máy mới", "máy cũ"])]
                    tot_may = sub_may["sl"].sum()
                    if tot_may > 0:
                        return "percent", round(len(sub) / tot_may * 100, 2), None
                return "abstain", None, "Warranty sales ratio percentage abstained for safety"
            return "number", len(sub), None

    # -------------------------------------------------------------------------
    # E. PRODUCT & CATEGORY ANALYTICS (so_ban_hang + so_ban_hang_ghi_tat)
    # -------------------------------------------------------------------------
    if has_sales and "so_ban_hang_ghi_tat" in context.tables:
        sbh = context.tables["so_ban_hang"].copy()
        is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
        returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
        sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)].copy()

        gt = context.tables["so_ban_hang_ghi_tat"].copy()

        mapping = {}
        for _, r in gt.iterrows():
            mapping[norm_item(r["ghi_tat"])] = (r["ten_hang"], r["loai"])
            mapping[norm_item(r["ten_hang"])] = (r["ten_hang"], r["loai"])

        sbh_clean["norm_hang"] = sbh_clean["hang"].apply(norm_item)
        sbh_clean["canonical_name"] = sbh_clean["norm_hang"].map(
            lambda x: mapping.get(x, (str(x), "khac"))[0]
        )
        sbh_clean["loai"] = sbh_clean["norm_hang"].map(
            lambda x: mapping.get(x, (str(x), "khac"))[1]
        )
        sbh_clean["m"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m")

        # E1. Exclude accessories phone sales count: 'ban duoc bao nhieu may khong tinh phu kien'
        if any(k in t_norm for k in ["khong tinh phu kien", "bo phu kien"]) and any(
            k in t_norm for k in ["bao nhieu may", "may may", "so may"]
        ):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                return "number", int(sub["sl"].sum()), None

        # E2. New phone revenue %: 'dthu may moi th nay chiem bnhiu % tong dthu may'
        if "may moi" in t_norm and any(k in t_norm for k in ["%", "phan tram"]):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                new_rev = sub[sub["loai"] == "máy mới"]["thanh_tien"].sum()
                tot_rev = sub["thanh_tien"].sum()
                if tot_rev > 0:
                    return "percent", round(new_rev / tot_rev * 100, 2), None

        # E3. Average phone price: 'trung binh 1 may dt ban dc bnhiu tien', 'trung binh moi may gia bao nhieu'
        if any(k in t_norm for k in ["trung binh", "binh quan"]) and any(
            k in t_norm for k in ["1 may", "moi may", "may dt"]
        ):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                tot_sl = sub["sl"].sum()
                if tot_sl > 0:
                    return "number", int(round(sub["thanh_tien"].sum() / tot_sl)), None

        # E4. Brand market share (e.g. Samsung): 'may samsung chiem bnhiu % tong so may ban ra'
        if "samsung" in t_norm and any(k in t_norm for k in ["%", "phan tram"]):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                ss_sl = sub[sub["canonical_name"].str.contains("Samsung", case=False)][
                    "sl"
                ].sum()
                tot_sl = sub["sl"].sum()
                if tot_sl > 0:
                    return "percent", round(ss_sl / tot_sl * 100, 2), None

        # E5. Top 3 models in month or quarter: 'top 3 dong may ban nhieu may nhat'
        if "top 3" in t_norm or "3 dong may nao" in t_norm:
            sub = pd.DataFrame()
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
            elif p.quarter_str:
                q_months = _quarter_to_months(p.quarter_str)
                sub = sbh_clean[
                    (sbh_clean["m"].isin(q_months))
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
            if not sub.empty:
                sub["dong_may"] = sub["canonical_name"].apply(extract_dong_may)
                top3 = (
                    sub.groupby("dong_may")["sl"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(3)
                    .index.tolist()
                )
                return "list", top3, None

        # E6. Top new model: 'trong nhom may moi, mat hang nao ban duoc nhieu may nhat'
        if "may moi" in t_norm and any(
            k in t_norm for k in ["nhieu may nhat", "chay nhat"]
        ):
            if target_month:
                sub = sbh_clean[(sbh_clean["m"] == target_month) & (sbh_clean["loai"] == "máy mới")]
                if not sub.empty:
                    top_m = sub.groupby("canonical_name")["sl"].sum().idxmax()
                    return "string", top_m, None

        # E7. Dynamic specific item unit count from canonical product list
        if target_month and "canonical_name" in sbh_clean.columns:
            canon_names = sorted(
                sbh_clean["canonical_name"].dropna().astype(str).unique().tolist(),
                key=len,
                reverse=True,
            )
            matched_canon = next(
                (cname for cname in canon_names if len(strip_accents(cname)) >= 4 and strip_accents(cname) in t_norm),
                None,
            )
            if not matched_canon:
                # Try stripped family name prefix (before capacity/color like iPhone 13)
                families = sorted(
                    {extract_dong_may(c) for c in canon_names if len(strip_accents(extract_dong_may(c))) >= 5},
                    key=len,
                    reverse=True,
                )
                fam = next(
                    (f for f in families if re.search(rf"(?<!\w){re.escape(strip_accents(f))}(?!\w)", t_norm)),
                    None,
                )
                if fam:
                    sub = sbh_clean[
                        (sbh_clean["m"] == target_month)
                        & (sbh_clean["canonical_name"].str.contains(fam, case=False, regex=False))
                    ]
                    if not sub.empty:
                        return "number", int(sub["sl"].sum()), None
            else:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["canonical_name"] == matched_canon)
                ]
                if not sub.empty:
                    return "number", int(sub["sl"].sum()), None

    return "abstain", None, "Không tìm thấy mẫu phù hợp cho câu hỏi cửa hàng điện thoại"
