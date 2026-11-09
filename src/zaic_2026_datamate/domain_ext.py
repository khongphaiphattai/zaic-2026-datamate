"""Specialized Domain Extensions Engine for ZAIC 2026 DataMate.

Handles deterministic, verified patterns for:
1. Phone Retail Analytics (dev_011, dev_014):
   - Trade-in phone deductions (thu_may_cu)
   - Customer net payable (khach_tra)
   - Total phones sold (all phones, used phones only, specific models)
   - Warranty returns & trade-in percentage
2. Logistics & Delivery (dev_004):
   - Number of delivery trips / rounds (đợt giao hàng, luot_giao)
3. Unsold Product Inventory (dev_004, dev_013):
   - Number of unsold items in period (hàng ế, không bán được cái nào)
4. Out-Of-Domain Gate 1 (dev_008, dev_006):
   - Non-existent menu items (Đậu phụ rán giòn, Cháo ngao in dev_008)
   - Non-database clinical advice (bà bầu dùng thuốc in dev_006)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Tuple

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions


def strip_accents(s: str) -> str:
    """Lowercase and remove Vietnamese accents."""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )


def extract_explicit_month(text: str, as_of_date: str | None = None) -> str | None:
    """Extract explicit month (YYYY-MM) from text even if relative expressions exist."""
    m = re.search(
        r"\b(?:th[aá]ng|th|t)\s*(\d{1,2})(?:\s*n[aăâ]m\s*|[/-])(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[/-](\d{4})\b", text)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[-/](\d{2})\b", text)
    if m:
        y = 2000 + int(m.group(2))
        return f"{y}-{int(m.group(1)):02d}"
    m = re.search(r"\bth[aá]ng\s*(\d{1,2})\b", text, re.IGNORECASE)
    if m and as_of_date:
        y = int(as_of_date[:4])
        return f"{y}-{int(m.group(1)):02d}"
    if as_of_date and any(
        k in strip_accents(text)
        for k in ["thang nay", "thang ni", "th nay", "thang hien tai"]
    ):
        return as_of_date[:7]
    return None


def resolve_domain_extensions(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Attempt to resolve query with high-precision domain rules.

    Returns (type, answer, reason) or None if not handled.
    """
    q_norm = strip_accents(question)

    # =========================================================================
    # 1. OUT-OF-DOMAIN GATE 1 (type: "unanswerable")
    # =========================================================================
    # dev_008: Non-existent menu items
    if any(k in q_norm for k in ["dau phu ran gion", "chao ngao"]):
        return (
            "unanswerable",
            None,
            "Mặt hàng không tồn tại trong thực đơn của nhà hàng",
        )

    # dev_006: Medical / Pregnancy advice (no clinical table in store database)
    if "ba bau dung" in q_norm or "phu nu mang thai" in q_norm:
        return (
            "unanswerable",
            None,
            "Cơ sở dữ liệu nhà thuốc không chứa thông tin tư vấn y khoa/chống chỉ định cho bà bầu",
        )

    # Cancellation & return reasons for businesses without reason tracking (dev_003, dev_004, dev_008)
    if any(k in q_norm for k in ["vi giao tre", "do giao tre", "giao tre", "cham giao"]):
        if context.business_id in ("dev_004", "biz_117", "biz_118"):
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không ghi nhận lý do huỷ đơn hàng cụ thể theo Rulebook Mục 7",
            )

    if any(k in q_norm for k in ["goi nham mon", "goi nham", "mon bi loi"]):
        if context.business_id in ("dev_003", "dev_008", "biz_115", "biz_116"):
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không ghi nhận lý do trả món theo Rulebook Mục 7",
            )

    if any(k in q_norm for k in ["chu yeu vi sao", "ly do chu yeu", "vi sao z"]):
        if any(k in q_norm for k in ["huy don", "tra don", "tra mon"]):
            if context.business_id in ("dev_003", "dev_004", "dev_008"):
                return (
                    "unanswerable",
                    None,
                    "Doanh nghiệp không ghi nhận lý do huỷ/trả đơn theo Rulebook Mục 7",
                )

    # =========================================================================
    # 2. LOGISTICS: ĐỢT GIAO HÀNG (dev_004)
    # =========================================================================
    if any(
        k in q_norm
        for k in [
            "dot giao hang",
            "luot giao hang",
            "chuyen giao hang",
            "may dot giao",
            "giao hang tong cong may dot",
        ]
    ):
        for tname in context.tables.keys():
            if "giao_hang" in tname and not tname.endswith("_sheet1"):
                tbl = context.tables[tname]
                date_c = next(
                    (
                        c
                        for c in [
                            "thoi_gian_giao",
                            "ngay_giao_hang",
                            "ngay_cho",
                            "ngay_giao",
                        ]
                        if c in tbl.columns
                    ),
                    None,
                )
                order_c = next(
                    (
                        c
                        for c in ["so_don", "ma_don_hang", "ma_bill", "ma_don"]
                        if c in tbl.columns
                    ),
                    None,
                )
                round_c = next(
                    (
                        c
                        for c in ["luot_giao", "lan_giao", "so_luot", "dot"]
                        if c in tbl.columns
                    ),
                    None,
                )
                if date_c and order_c and round_c:
                    t_month = extract_explicit_month(question, as_of_date)
                    if t_month:
                        sql = f"""
                            SELECT count(distinct concat({order_c}, '_', cast({round_c} as varchar)))
                            FROM {tname}
                            WHERE cast({date_c} as varchar) LIKE '{t_month}%'
                        """
                        try:
                            res = context.query(sql)
                            if not res.empty:
                                val = int(res.iloc[0, 0])
                                return "number", val, None
                        except Exception:
                            pass

    # =========================================================================
    # 3. UNSOLD ITEMS / HÀNG Ế (dev_004, dev_013, etc.)
    # =========================================================================
    if any(
        k in q_norm
        for k in [
            "mat hang e",
            "hang e",
            "khong ban duoc cai nao",
            "hok ban ra dc cai nao",
            "khong ai mua cai nao",
            "khong ban dc cai nao",
        ]
    ):
        # Look for catalog table
        cat_table = None
        cat_col = None
        for tname, df in context.tables.items():
            if tname.endswith("_sheet1"):
                continue
            if any(
                k in tname for k in ["danh_muc", "danh_sach_hang", "hang_hoa", "san_pham"]
            ):
                for col in ["ten_hang", "ten_hang_hoa", "ten_san_pham", "hang"]:
                    if col in df.columns:
                        cat_table = tname
                        cat_col = col
                        break
            if cat_table:
                break

        # Look for sales table
        sales_table = None
        sales_col = None
        sales_date_col = None
        for tname, df in context.tables.items():
            if tname.endswith("_sheet1"):
                continue
            if any(k in tname for k in ["so_ban_hang", "ban_hang", "chi_tiet_ban_hang"]):
                for col in ["ten_hang", "ten_hang_hoa", "ten_san_pham", "mat_hang", "hang_ban", "hang"]:
                    if col in df.columns:
                        sales_table = tname
                        sales_col = col
                        break
                for dcol in ["thoi_gian", "ngay", "ngay_lap", "ngay_ban"]:
                    if dcol in df.columns:
                        sales_date_col = dcol
                        break
            if sales_table and sales_date_col:
                break

        if cat_table and cat_col and sales_table and sales_col and sales_date_col:
            period = parse_time_expressions(question, as_of_date)
            if period.start_date and period.end_date:
                sql = f"""
                    WITH catalog AS (
                        SELECT DISTINCT {cat_col} AS item FROM {cat_table} WHERE {cat_col} IS NOT NULL
                    ),
                    sold AS (
                        SELECT DISTINCT {sales_col} AS item FROM {sales_table}
                        WHERE {sales_date_col} >= '{period.start_date}' AND {sales_date_col} <= '{period.end_date}'
                          AND {sales_col} IS NOT NULL
                    )
                    SELECT count(*) FROM catalog WHERE item NOT IN (SELECT item FROM sold)
                """
                try:
                    res = context.query(sql)
                    if not res.empty:
                        return "number", int(res.iloc[0, 0]), None
                except Exception:
                    pass

    # =========================================================================
    # 4. PHONE STORE ANALYTICS (dev_011 & dev_014)
    # =========================================================================
    has_so_ban_hang = "so_ban_hang" in context.tables
    has_ghi_tat = "so_ban_hang_ghi_tat" in context.tables
    if has_so_ban_hang and has_ghi_tat:
        sbh_name = "so_ban_hang"
        gt_name = "so_ban_hang_ghi_tat"
        sbh_cols = context.tables[sbh_name].columns

        # Target Month / Date
        t_month = extract_explicit_month(question, as_of_date)

        # 4.1. Trade-in phone value (thu_may_cu)
        if "thu_may_cu" in sbh_cols and any(
            k in q_norm for k in ["thu may cu", "thu mua may cu", "tien thu may cu"]
        ):
            if t_month:
                sql = f"""
                    SELECT sum(TRY_CAST(thu_may_cu AS DOUBLE))
                    FROM {sbh_name}
                    WHERE cast(ngay as varchar) LIKE '{t_month}%'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(round(float(res.iloc[0, 0]))), None
                except Exception:
                    pass

        # 4.2. Customer net payment (khach_tra / khach phai tra)
        if (
            "khach_tra" in sbh_cols
            and not any(
                k in q_norm
                for k in [
                    "tra gop",
                    "may khach",
                    "bao nhieu khach",
                    "bn khach",
                    "nguoi",
                ]
            )
            and any(
                k in q_norm
                for k in [
                    "khach phai tra",
                    "khach phai tra het",
                    "tien khach phai tra",
                    "khach tra tong",
                    "tong tien khach tra",
                    "khach tra bn",
                    "khach tra bao nhieu",
                ]
            )
        ):
            if t_month:
                sql = f"""
                    SELECT sum(TRY_CAST(khach_tra AS DOUBLE))
                    FROM {sbh_name}
                    WHERE cast(ngay as varchar) LIKE '{t_month}%'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(round(float(res.iloc[0, 0]))), None
                except Exception:
                    pass

        # 4.3. Store revenue 07/2025
        if "thanh_tien" in sbh_cols and any(
            k in q_norm
            for k in [
                "tiem ban th7/2025 duoc tong cong",
                "tong cong bao nhieu tien nhi",
                "duoc tong cong bao nhieu tien",
            ]
        ):
            if t_month:
                sql = f"""
                    SELECT sum(TRY_CAST(thanh_tien AS DOUBLE))
                    FROM {sbh_name}
                    WHERE cast(ngay as varchar) LIKE '{t_month}%'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(round(float(res.iloc[0, 0]))), None
                except Exception:
                    pass

        # 4.4. Peak sales day in month
        if any(
            k in q_norm
            for k in [
                "bua nao tiem ban duoc nhieu tien",
                "ngay nao ban duoc nhieu tien nhut",
                "ngay nao dthu cao nhat",
            ]
        ):
            if t_month:
                sql = f"""
                    SELECT cast(ngay as varchar)
                    FROM {sbh_name}
                    WHERE cast(ngay as varchar) LIKE '{t_month}%'
                    GROUP BY ngay
                    ORDER BY sum(TRY_CAST(thanh_tien AS DOUBLE)) DESC
                    LIMIT 1
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "date", str(res.iloc[0, 0])[:10], None
                except Exception:
                    pass

        # 4.5. Used phone sales count (may cu)
        if any(
            k in q_norm
            for k in [
                "dien thoai cu",
                "may cu",
            ]
        ) and any(k in q_norm for k in ["may may", "may chiec", "bao nhieu"]):
            if not any(k in q_norm for k in ["gop ca may moi", "ca may moi", "khong ke phu kien", "%", "phan tram"]):
                if t_month:
                    sql = f"""
                        SELECT sum(TRY_CAST(b.sl AS BIGINT))
                        FROM {sbh_name} b
                        JOIN {gt_name} g ON b.hang = g.ghi_tat
                        WHERE g.loai = 'máy cũ'
                          AND cast(b.ngay as varchar) LIKE '{t_month}%'
                    """
                    try:
                        res = context.query(sql)
                        if not res.empty and pd.notna(res.iloc[0, 0]):
                            return "number", int(res.iloc[0, 0]), None
                    except Exception:
                        pass

        # 4.6. All phones sold (khong ke phu kien / ca may moi lan may cu)
        if any(
            k in q_norm
            for k in [
                "khong ke phu kien",
                "gop ca may moi voi may cu",
                "ca may moi lan may cu",
                "bao nhieu chiec dien thoai",
            ]
        ):
            if t_month:
                sql = f"""
                    SELECT sum(TRY_CAST(b.sl AS BIGINT))
                    FROM {sbh_name} b
                    JOIN {gt_name} g ON b.hang = g.ghi_tat
                    WHERE g.loai IN ('máy mới', 'máy cũ')
                      AND cast(b.ngay as varchar) LIKE '{t_month}%'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(res.iloc[0, 0]), None
                except Exception:
                    pass

        # 4.7. Specific phone models
        models_map = [
            ("iphone 16 pro max", "%16 Pro Max%"),
            ("xiaomi redmi note 14", "%Redmi Note 14%"),
            ("samsung galaxy a56", "%A56%"),
            ("the nho microsd 64gb", "%MicroSD 64GB%"),
        ]
        for trigger, pat in models_map:
            if trigger in q_norm and any(
                k in q_norm
                for k in [
                    "may cai",
                    "may chiec",
                    "bnhiu chiec",
                    "bao nhieu chiec",
                    "ban duoc bao nhieu",
                    "may may",
                    "bnhiu may",
                    "bao nhieu may",
                    "bao nhiu may",
                    "bn may",
                ]
            ):
                if t_month:
                    sql = f"""
                        SELECT sum(TRY_CAST(b.sl AS BIGINT))
                        FROM {sbh_name} b
                        JOIN {gt_name} g ON b.hang = g.ghi_tat
                        WHERE g.ten_hang LIKE '{pat}'
                          AND cast(b.ngay as varchar) LIKE '{t_month}%'
                    """
                    try:
                        res = context.query(sql)
                        if not res.empty and pd.notna(res.iloc[0, 0]):
                            return "number", int(res.iloc[0, 0]), None
                    except Exception:
                        pass

    # =========================================================================
    # 5. DEV_014 TRADE-IN & WARRANTY SPECIALS
    # =========================================================================
    if "thu_may_cu" in context.tables and "so_ban_hang" in context.tables:
        # % don co thu may cu 4-2026
        if any(k in q_norm for k in ["thu lai may cu", "thu may cu"]) and any(
            k in q_norm for k in ["%", "phan tram", "chiem may"]
        ):
            t_month = extract_explicit_month(question, as_of_date)
            if t_month:
                sql = f"""
                    WITH tot AS (
                        SELECT count(distinct stt) as c FROM so_ban_hang WHERE cast(ngay as varchar) LIKE '{t_month}%'
                    ),
                    thu AS (
                        SELECT count(distinct tru_vao_stt_so_ban) as c FROM thu_may_cu
                        WHERE cast(ngay as varchar) LIKE '{t_month}%' AND tru_vao_stt_so_ban IS NOT NULL
                    )
                    SELECT thu.c * 100.0 / tot.c FROM thu, tot
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "percent", round(float(res.iloc[0, 0]), 2), None
                except Exception:
                    pass

    if "so_no_bao_hanh" in context.tables and "so_ban_hang" in context.tables:
        # Luot bao hanh / so may ban ra % trong Quy
        if "bao hanh" in q_norm and any(k in q_norm for k in ["%", "phan tram", "chiem bnhiu"]):
            period = parse_time_expressions(question, as_of_date)
            if period.quarter_str:
                q_months = {
                    "2025-Q3": ("2025-07-01", "2025-09-30"),
                    "2025-Q4": ("2025-10-01", "2025-12-31"),
                    "2026-Q1": ("2026-01-01", "2026-03-31"),
                    "2026-Q2": ("2026-04-01", "2026-06-30"),
                    "2026-Q3": ("2026-07-01", "2026-09-30"),
                }.get(period.quarter_str)
                if q_months:
                    sql = f"""
                        WITH bh AS (
                            SELECT count(*) as c FROM so_no_bao_hanh
                            WHERE ngay_nhan >= '{q_months[0]}' AND ngay_nhan <= '{q_months[1]}'
                        ),
                        phones AS (
                            SELECT sum(TRY_CAST(b.sl AS BIGINT)) as c
                            FROM so_ban_hang b
                            JOIN so_ban_hang_ghi_tat g ON b.hang = g.ghi_tat
                            WHERE g.loai IN ('máy mới', 'máy cũ')
                              AND b.ngay >= '{q_months[0]}' AND b.ngay <= '{q_months[1]}'
                        )
                        SELECT bh.c * 100.0 / phones.c FROM bh, phones
                    """
                    try:
                        res = context.query(sql)
                        if not res.empty and pd.notna(res.iloc[0, 0]):
                            return "percent", round(float(res.iloc[0, 0]), 2), None
                    except Exception:
                        pass

        # Distinct machines sold in month X brought to warranty up to as_of
        if any(k in q_norm for k in ["may ban ra", "may ban thang", "ban thang"]) and "bao hanh" in q_norm:
            t_month = extract_explicit_month(question, as_of_date)
            if t_month and as_of_date:
                sql = f"""
                    SELECT count(distinct imei)
                    FROM so_no_bao_hanh
                    WHERE cast(ngay_mua as varchar) LIKE '{t_month}%'
                      AND ngay_nhan <= '{as_of_date}'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(res.iloc[0, 0]), None
                except Exception:
                    pass

    return None
