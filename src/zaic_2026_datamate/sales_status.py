"""Finite Train24 status vocabulary under rulebook sections1 and8."""

import unicodedata

STATUS_ALIASES = {
    **dict.fromkeys(
        (
            "da hoan thanh",
            "hoan thanh",
            "da xong",
            "xong",
            "thanh cong",
            "da lay",
            "completed",
        ),
        "completed",
    ),
    **dict.fromkeys(
        (
            "dang giao dich",
            "dang giao hang",
            "dang giao",
            "dang gui",
            "dang van chuyen",
            "shipping",
        ),
        "shipping",
    ),
    **dict.fromkeys(
        ("da huy", "da huy don", "huy", "huy don", "khach huy", "cancel", "cancelled"),
        "cancelled",
    ),
    **dict.fromkeys(
        (
            "da hoan hang",
            "da tra hang",
            "hoan tra",
            "tra hang",
            "tra lai",
            "tra lai hang",
            "doi tra",
            "hang tra ve",
            "tra ve",
            "khach tra",
            "khach tra lai",
            "khach tra hang",
            "khach mang tra",
            "mang tra",
            "da mang tra",
            "hoan don",
            "hang hoan",
            "returned",
        ),
        "returned",
    ),
}


def classify_sales_status(value):
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    text = " ".join("".join(c for c in text if not unicodedata.combining(c)).split())
    return STATUS_ALIASES.get(text)


def canonical_status_clause(status_column, order_column, table, raw_statuses):
    """Identifiers are already quoted; raw values are escaped as SQL literals."""
    excluded = []
    for value in raw_statuses:
        state = classify_sales_status(value)
        if state is None:
            raise ValueError("Unsupported sales status")
        if state in ("cancelled", "returned"):
            excluded.append("'" + value.replace("'", "''") + "'")
    if not excluded:
        return ""
    values = ",".join(excluded)
    return (
        f"AND {order_column} NOT IN (SELECT {order_column} FROM {table} "
        f"WHERE CAST({status_column} AS VARCHAR) IN ({values}))"
    )
