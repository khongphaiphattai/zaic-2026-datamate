"""
repair_shop.py: Specialized Schema-Driven Execution Engine for Repair Shops (`so_sua_xe`).

Handles:
- Commission calculation dynamically from technician roster/rate tables (e.g. `danh_sach_tho`)
- Ticket counting (total tickets, warranty tickets, ticket AOV)
- Technician analytics (revenue per technician, ticket count per technician, top rankings)
- Vehicle model analytics (distinct vehicle count by model, top revenue model)
- Repair task counts (electrical repair, gear oil replacement, tire patching)
- Returning customer counts (vehicles visited >= 2 times identified by license plate)
- Day of week averages
- Peak sales day lookup in month
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions

logger = logging.getLogger(__name__)


def strip_accents(s: str) -> str:
    """Normalize Vietnamese text to ASCII lowercase."""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )


def extract_plate(x: Any) -> str:
    """Extract canonical uppercase license plate from vehicle string."""
    m = re.search(
        r"([0-9]{2}[A-Z0-9]{1,2}[-_ ][0-9]{3,5}(?:\.[0-9]{2})?)",
        str(x),
        re.IGNORECASE,
    )
    return m.group(1).upper() if m else str(x).lower().replace(" ", "")


def _parse_commission_rate(val: Any) -> float | None:
    """Parse commission rate from decimal (`0.32`) or percent (`32`, `'32%'`)."""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    has_pct = "%" in s
    s_clean = s.replace("%", "").replace(",", ".").strip()
    try:
        num = float(s_clean)
    except ValueError:
        return None
    if num < 0:
        return None
    if has_pct or num > 1.0:
        num = num / 100.0
    return num if 0.0 <= num <= 1.0 else None


def _load_tho_registry(
    context: BusinessContext, df: pd.DataFrame
) -> tuple[list[str], dict[str, float]]:
    """Dynamically build canonical technician names and commission rates from schema tables."""
    rates: dict[str, float] = {}
    canon_names: list[str] = []
    seen_norm: set[str] = set()

    for tname, tdf in context.tables.items():
        if tname in ("so_sua_xe", "calendar") or tname.endswith("_sheet1") or tdf.empty:
            continue
        cols = list(tdf.columns)
        name_col = next(
            (
                c
                for c in ("ho_ten", "ten_tho", "tho", "ten", "nhan_vien", "ten_nv")
                if c in cols
            ),
            None,
        )
        rate_col = next(
            (
                c
                for c in cols
                if any(
                    k in c
                    for k in (
                        "hoa_hong",
                        "hue_hong",
                        "ty_le",
                        "ti_le",
                        "muc_huong",
                        "phan_tram",
                        "rate",
                    )
                )
            ),
            None,
        )
        if not (name_col and (rate_col or "tho" in tname)):
            continue
        for _, row in tdf.iterrows():
            raw_name = row.get(name_col)
            if pd.isna(raw_name):
                continue
            cname = re.sub(r"\s+", " ", str(raw_name)).strip()
            if not cname:
                continue
            cnorm = strip_accents(cname)
            if cnorm not in seen_norm:
                seen_norm.add(cnorm)
                canon_names.append(cname)
            if rate_col:
                r = _parse_commission_rate(row.get(rate_col))
                if r is not None:
                    rates[cname] = r

    # Fallback: add multi-word canonical names observed in `so_sua_xe.tho` only if no roster table existed
    if not canon_names and "tho" in df.columns:
        raw_vals = sorted(
            {
                re.sub(r"\s+", " ", str(v)).strip()
                for v in df["tho"].dropna()
                if str(v).strip()
            },
            key=len,
            reverse=True,
        )
        for val in raw_vals:
            vnorm = strip_accents(val)
            if " " in vnorm and vnorm not in seen_norm:
                seen_norm.add(vnorm)
                canon_names.append(val)

    return canon_names, rates


def _last_token_unique_map(canonical_names: list[str]) -> dict[str, str]:
    """Map unambiguous last word tokens (e.g. 'dat' -> 'Hà Loan Đạt') across roster names."""
    buckets: dict[str, list[str]] = {}
    for cname in canonical_names:
        toks = re.findall(r"[a-z0-9]+", strip_accents(cname))
        if toks:
            buckets.setdefault(toks[-1], []).append(cname)
    return {tok: names[0] for tok, names in buckets.items() if len(names) == 1}


def canonicalize_tho_name(raw: Any, canonical_names: list[str]) -> str:
    """Map raw technician label to its canonical full name when unambiguously identified."""
    raw_clean = re.sub(r"\s+", " ", str(raw)).strip()
    if not raw_clean:
        return ""
    r_norm = strip_accents(raw_clean)
    exact_map = {strip_accents(c): c for c in canonical_names}
    if r_norm in exact_map:
        return exact_map[r_norm]

    r_tokens = re.findall(r"[a-z0-9]+", r_norm)
    last_map = _last_token_unique_map(canonical_names)
    for tok in reversed(r_tokens):
        if tok in last_map:
            return last_map[tok]
    return raw_clean


def extract_specific_tho(t_norm: str, canonical_names: list[str]) -> str | None:
    """Extract specific technician name mentioned in query with strict boundary matching."""
    for cname in sorted(
        canonical_names, key=lambda s: len(strip_accents(s)), reverse=True
    ):
        c_norm = strip_accents(cname)
        if re.search(rf"(?<![a-z0-9]){re.escape(c_norm)}(?![a-z0-9])", t_norm):
            return cname

    for last_tok, cname in _last_token_unique_map(canonical_names).items():
        if re.search(rf"\b(?:tho|a|anh|chu|chi|em)\s+{re.escape(last_tok)}\b", t_norm):
            return cname
    return None


def filter_df_by_period(
    df: pd.DataFrame,
    p: Any,
    t_norm: str,
    as_of_date: str | None = None,
) -> pd.DataFrame | None:
    """Strictly filter dataframe by temporal expressions; returns None if undefined."""
    m_span = re.search(
        r"\b(?:th(?:ang)?\s*|t)?(\d{1,2})/(\d{4})\s*(?:den|toi|->|-)(?:\s*(?:het|cuoi))?\s*(?:th(?:ang)?\s*|t)?(\d{1,2})/(\d{4})\b",
        t_norm,
    )
    if m_span:
        m1, y1, m2, y2 = (int(g) for g in m_span.groups())
        if 1 <= m1 <= 12 and 1 <= m2 <= 12 and (y1, m1) <= (y2, m2):
            span_months: list[str] = []
            yr, mo = y1, m1
            while (yr, mo) <= (y2, m2) and len(span_months) < 24:
                span_months.append(f"{yr:04d}-{mo:02d}")
                mo += 1
                if mo > 12:
                    mo = 1
                    yr += 1
            return df[df["month"].isin(span_months)]
    if p.month_str:
        return df[df["month"] == p.month_str]
    if p.quarter_str:
        m_q = re.match(r"^(\d{4})-Q([1-4])$", p.quarter_str)
        if m_q:
            yr, q_num = int(m_q.group(1)), int(m_q.group(2))
            start_m = (q_num - 1) * 3 + 1
            q_months = [f"{yr:04d}-{mm:02d}" for mm in range(start_m, start_m + 3)]
            return df[df["month"].isin(q_months)]
    if p.exact_date:
        return df[df["date_str"] == p.exact_date]
    if p.start_date and p.end_date:
        return df[(df["date_str"] >= p.start_date) & (df["date_str"] <= p.end_date)]
    if ("thang ni" in t_norm or "thang nay" in t_norm) and as_of_date:
        return df[df["month"] == as_of_date[:7]]
    return None


def resolve_repair_shop_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve queries for repair shop businesses (`so_sua_xe`) using schema-driven rules."""
    if "so_sua_xe" not in context.tables:
        return "abstain", None, "Không tìm thấy bảng so_sua_xe"

    df = context.tables["so_sua_xe"].copy()
    if df.empty:
        return "abstain", None, "Bảng so_sua_xe rỗng"

    canon_names, commission_rates = _load_tho_registry(context, df)
    df["tho_canonical"] = df["tho"].apply(
        lambda x: canonicalize_tho_name(x, canon_names)
    )
    df["plate"] = df["xe"].apply(extract_plate)
    df["month"] = pd.to_datetime(df["ngay"]).dt.strftime("%Y-%m")
    df["date_str"] = pd.to_datetime(df["ngay"]).dt.strftime("%Y-%m-%d")

    t_norm = strip_accents(question)
    p = parse_time_expressions(question, as_of_date)

    # 0. Subjective sentiment queries -> unanswerable
    if any(
        k in t_norm
        for k in [
            "nhiet tinh nhat",
            "than thien nhat",
            "gioi nhat",
            "uy tin nhat",
            "yeu thich nhat",
        ]
    ):
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có dữ liệu đánh giá cảm tính theo Rulebook Mục 7",
        )

    # 0.1 Thợ chăm chỉ nhất per Rulebook Section 3: người có nhiều ngày làm việc nhất trong kỳ
    if "cham chi" in t_norm:
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is not None and not sub.empty:
            sub = sub.copy()
            sub["day"] = pd.to_datetime(sub["ngay"]).dt.date
            sub["c_tho"] = sub["tho_canonical"]
            top_tho = sub.groupby("c_tho")["day"].nunique().idxmax()
            return "string", str(top_tho), None

    # 1. Commission table: 'bang hoa hong', 'bang tien hoa hong', 'hoa hong tung tho ... bang'
    if any(
        k in t_norm
        for k in [
            "bang hoa hong",
            "bang tien hoa hong",
            "hoa hong tung tho",
            "tien hoa hong kua moi tho",
            "tien hoa hong cua moi tho",
        ]
    ) and any(
        k in t_norm for k in ["bang", "xep", "giam dan", "cao xuong thap", "cho a xin"]
    ):
        if not commission_rates:
            return "abstain", None, "Không tìm thấy bảng tỷ lệ hoa hồng của thợ"
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        res = []
        for tho, r in commission_rates.items():
            s_rev = sub[sub["tho_canonical"] == tho]["tien"].sum()
            res.append([tho, int(round(s_rev * r))])
        res = sorted(res, key=lambda x: x[1], reverse=True)
        return "table", {"columns": ["tho", "hoa_hong"], "rows": res}, None

    # 2. Commission single tho: 'hoa hong', 'hue hong'
    if any(k in t_norm for k in ["hoa hong", "hue hong"]):
        if not commission_rates:
            return "abstain", None, "Không tìm thấy bảng tỷ lệ hoa hồng của thợ"
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        matched_tho = extract_specific_tho(t_norm, canon_names)
        if matched_tho and matched_tho in commission_rates:
            s_rev = sub[sub["tho_canonical"] == matched_tho]["tien"].sum()
            return "number", int(round(s_rev * commission_rates[matched_tho])), None
        # Top tho commission: 'tho mo hoa hong cao nhat', 'tho nao hoa hong cao nhat'
        if any(k in t_norm for k in ["cao nhat", "nhieu nhat"]):
            hh_scores = {
                tho: sub[sub["tho_canonical"] == tho]["tien"].sum() * r
                for tho, r in commission_rates.items()
            }
            if not hh_scores:
                return "abstain", None, "Không tính được hoa hồng của thợ"
            top_tho = max(hh_scores.items(), key=lambda x: x[1])[0]
            return "string", top_tho, None

    # 3. Technician sales / money: 'tho X ... bao nhieu tien', 'ban tong cong bnhiu tien', 'tien hang cac phieu tho X'
    spec_tho = extract_specific_tho(t_norm, canon_names)
    if spec_tho and any(
        k in t_norm
        for k in [
            "bao nhieu tien",
            "bnhiu tien",
            "tien hang",
            "lam duoc tong bao nhieu",
            "lam ra dthu",
            "ban tong cong",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        s_rev = sub[sub["tho_canonical"] == spec_tho]["tien"].sum()
        return "number", int(round(s_rev)), None

    # 4. Technician ticket count: 'tho X ... bao nhieu luot xe', 'mi phieu sua xe', 'may luot xe'
    if spec_tho and any(
        k in t_norm
        for k in [
            "luot xe",
            "phieu sua xe",
            "mi phieu",
            "may phieu",
            "sua duoc nhieu luot",
            "man duoc bao nhieu",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        cnt = len(sub[sub["tho_canonical"] == spec_tho])
        return "number", cnt, None

    # 5. Top technician ranking: 'tho mo lam nhieu luot xe nhat', 'tho nhan nhieu phieu sua nhat', 'tho nao lam ra dthu cao nhat', 'tho mo lam ra nhieu tien nhat'
    if any(k in t_norm for k in ["tho mo", "tho nao", "ai la tho", "tho nhan"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        if any(
            k in t_norm
            for k in [
                "nhieu luot xe nhat",
                "nhieu phieu sua nhat",
                "nhieu phieu nhat",
                "nhieu luot xe",
            ]
        ):
            top_tho = sub["tho_canonical"].value_counts().idxmax()
            return "string", top_tho, None
        if any(
            k in t_norm
            for k in [
                "dthu cao nhat",
                "nhieu tien nhat",
                "ra nhieu tien",
                "ra dthu cao nhat",
            ]
        ):
            top_tho = sub.groupby("tho_canonical")["tien"].sum().idxmax()
            return "string", top_tho, None

    # 6. AOV: 'binh quan 1 phieu', 'trung binh 1 phieu'
    if any(
        k in t_norm
        for k in [
            "binh quan 1 phieu",
            "trung binh 1 phieu",
            "binh quan mot phieu",
            "trung binh mot phieu",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        sub_paid = sub[sub["tien"] > 0]
        if sub_paid.empty:
            return "abstain", None, "Không có phiếu thu tiền trong kỳ"
        return "number", int(round(sub_paid["tien"].mean())), None

    # 7. Warranty tickets count: 'phieu bao hanh', 'quay lai bao hanh'
    if any(k in t_norm for k in ["bao hanh"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        # If asks for ratio %
        if any(k in t_norm for k in ["%", "phan tram", "chiem"]):
            bh = len(sub[sub["tien"] == 0])
            tt = len(sub[sub["tien"] > 0])
            if tt == 0:
                return "abstain", None, "Không có phiếu thu tiền để tính tỷ lệ"
            return "percent", round((bh / tt) * 100, 2), None

        # Count tickets
        return "number", len(sub[sub["tien"] == 0]), None

    # 8. Ticket count of store: 'dem phieu sua xe', 'len bnhiu phieu sua xe'
    if any(
        k in t_norm
        for k in [
            "dem phieu",
            "len bnhiu phieu",
            "may phieu sua xe",
            "mấy phiếu sửa xe",
            "bao nhieu phieu sua",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        return "number", len(sub), None

    # 9. Store monthly / quarterly revenue: 'tong dthu thang', 'dthu cua tiem', 'tiem ban tong cong duoc bnhiu tien'
    if any(
        k in t_norm
        for k in [
            "tong dthu",
            "dthu cua tiem",
            "dthu thang",
            "tiem ban tong cong",
            "dthu chia tiem",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        return "number", int(round(sub["tien"].sum())), None

    # 10. Distinct vehicle count of a model: 'bn chiec sh mode khac nhau', 'bao nhieu xe air blade khac nhau', 'bao nhieu chiec xe wave alpha'
    if any(
        k in t_norm
        for k in [
            "khac nhau",
            "moi xe tinh mot lan",
            "xe wave alpha",
            "xe air blade",
            "sh mode",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        for model_kw in [
            "sh mode",
            "shmode",
            "air blade",
            "airblade",
            "wave alpha",
            "wave rsx",
            "vision",
            "lead",
        ]:
            if model_kw in t_norm:
                kw_clean = model_kw.replace(" ", "")
                matched = sub[
                    sub["xe"].apply(
                        lambda x, m=model_kw, k=kw_clean: (
                            m in strip_accents(x) or k in strip_accents(x)
                        )
                    )
                ]
                return "number", matched["plate"].nunique(), None

    # 11. Top revenue vehicle model: 'dong xe mo dem lai nhieu tien sua nhat'
    if any(k in t_norm for k in ["dong xe mo", "dong xe nao"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"

        def extract_model(xe_str: str) -> str:
            s = strip_accents(xe_str)
            for m_kw in [
                "wave alpha",
                "wave rsx",
                "wave",
                "sh mode",
                "shmode",
                "sh",
                "air blade",
                "airblade",
                "vario",
                "vision",
                "future",
                "sirius",
                "exciter",
                "ex",
                "lead",
                "winner",
                "blade",
                "jupiter",
                "nvx",
                "grande",
                "janus",
            ]:
                if m_kw in s:
                    if m_kw in ["sh mode", "shmode"]:
                        return "SH Mode"
                    if m_kw in ["wave alpha"]:
                        return "Wave Alpha"
                    if m_kw in ["wave rsx"]:
                        return "Wave RSX"
                    if m_kw in ["wave"]:
                        return "Wave"
                    if m_kw in ["air blade", "airblade"]:
                        return "Air Blade"
                    if m_kw in ["ex", "exciter"]:
                        return "Exciter"
                    return m_kw.title()
            return xe_str.split()[0].title()

        models = sub["xe"].apply(extract_model)
        top_model = sub.groupby(models)["tien"].sum().idxmax()
        return "string", top_model, None

    # 12. Returning customer (>= 2 visits): 'xe vo sua tu 2 lan tro len', 'xe khach quen'
    if any(k in t_norm for k in ["2 lan tro len", "khach quen"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        plate_series = (
            sub["plate"] if "plate" in sub.columns else sub["xe"].map(extract_plate)
        )
        vc = plate_series.value_counts()
        return "number", int((vc >= 2).sum()), None

    # 13. Specific repair task: 'sua dien', 'dau lap', 'va ruot xe'
    if any(k in t_norm for k in ["sua dien", "dau lap", "nhot lap", "va ruot"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        matched = pd.DataFrame()
        if "sua dien" in t_norm:
            matched = sub[sub["lam_gi"].apply(lambda x: "dien" in strip_accents(x))]
        elif any(k in t_norm for k in ["dau lap", "nhot lap"]):
            matched = sub[
                sub["lam_gi"].apply(
                    lambda x: (
                        "dau lap" in strip_accents(x) or "nhot lap" in strip_accents(x)
                    )
                )
            ]
        elif "va ruot" in t_norm:
            matched = sub[sub["lam_gi"].apply(lambda x: "va ruot" in strip_accents(x))]
        return "number", len(matched), None

    # 14. Day of week average: 'trung binh moi thu tu', 'kac ngay thu 3 ... trung binh'
    if any(
        k in t_norm
        for k in ["trung binh moi thu", "trung binh 1 ngay ko bnhiu luot xe"]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        sub = sub.copy()
        sub["day_of_week"] = pd.to_datetime(sub["ngay"]).dt.day_of_week
        target_dow = 2 if ("thu tu" in t_norm or "thu 4" in t_norm) else 1
        dow_df = sub[sub["day_of_week"] == target_dow]
        dow_days = dow_df["date_str"].nunique()
        if dow_days == 0:
            return "abstain", None, "Không có ngày nào trong thứ này"
        return "number", round(len(dow_df) / dow_days, 2), None

    # 15. Max sales day in month: 'ngay nao trong thang ... ban duoc nhieu tien nhat', 'ngay ma tiem ban ra nhieu tien nhat'
    if any(k in t_norm for k in ["ngay nao", "ngay ma tiem"]) and any(
        k in t_norm for k in ["nhieu tien nhat"]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không có dữ liệu trong kỳ"
        max_d = sub.groupby("date_str")["tien"].sum().idxmax()
        return "date", max_d, None

    return "abstain", None, "Câu hỏi chưa có template khớp hoặc độ tin cậy < 95%"
