"""Tests for v3.6 enhancements:
1. Whitespace-normalized SQL joins (ws_norm_sql)
2. Robust order status column detection and cancellation classification
"""

from __future__ import annotations

import duckdb
import pandas as pd
from zaic_2026_datamate.cleaner import ws_norm_sql
from zaic_2026_datamate.semantic_contract import (
    is_canceled_status,
    pick_order_status_col,
)


def test_ws_norm_sql_handles_whitespace_variations():
    """Verify that ws_norm_sql successfully joins keys with irregular spaces."""
    con = duckdb.connect(":memory:")
    df_a = pd.DataFrame(
        [
            {"id": 1, "item": "  Bia   Sài Gòn  Special lon  ", "qty": 10},
            {"id": 2, "item": "Trà đào\tcam sả (M)", "qty": 5},
        ]
    )
    df_b = pd.DataFrame(
        [
            {"item_ref": "Bia Sài Gòn Special lon", "cat": "Đồ uống"},
            {"item_ref": "Trà đào cam sả (M)", "cat": "Trà"},
        ]
    )
    con.register("tbl_a", df_a)
    con.register("tbl_b", df_b)

    sql = f"""
        SELECT a.id, b.cat
        FROM tbl_a a
        JOIN tbl_b b ON {ws_norm_sql("a.item")} = {ws_norm_sql("b.item_ref")}
        ORDER BY a.id
    """
    res = con.execute(sql).fetchall()
    assert len(res) == 2
    assert res[0] == (1, "Đồ uống")
    assert res[1] == (2, "Trà")
    con.close()


def test_pick_order_status_col_fallback():
    """Verify that pick_order_status_col correctly falls back to columns containing 'trang_thai' or 'tinh_trang'."""
    df_custom = pd.DataFrame(
        {
            "ma_don": ["D01", "D02"],
            "tinh_trang_giao_hang": ["thành công", "hủy"],
            "ghi_chu": ["khách quen", None],
        }
    )
    col = pick_order_status_col(df_custom)
    assert col == "tinh_trang_giao_hang"


def test_is_canceled_status_patterns():
    """Verify cancellation detection for various standard and dialectal Vietnamese phrases."""
    assert is_canceled_status("Đã hủy")
    assert is_canceled_status("HỦY ĐƠN")
    assert is_canceled_status("bom hàng")
    assert is_canceled_status("khách từ chối nhận")
    assert is_canceled_status("thất bại")
    assert not is_canceled_status("Đã giao")
    assert not is_canceled_status("Hoàn tất")
    assert not is_canceled_status(None)
