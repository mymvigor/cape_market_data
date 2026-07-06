from datetime import datetime, timezone
from typing import Any

import pandas as pd


def _to_float(record: dict[str, Any], field: str) -> float:
    try:
        value = float(record[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Unable to parse {field} as a number") from exc
    return value


def transform_data(record: dict[str, Any]) -> dict[str, Any]:
    date_value = pd.to_datetime(record.get("date"), errors="coerce")
    if pd.isna(date_value):
        raise RuntimeError("Unable to parse date field")

    c5tc = _to_float(record, "C5TC")
    c3 = _to_float(record, "C3")
    c5 = _to_float(record, "C5")

    return {
        "date": date_value.date().isoformat(),
        "C5TC": c5tc,
        "C3": c3,
        "C5": c5,
        "C3_C5_ratio": round(c3 / c5, 3),
        "C3_minus_C5": round(c3 - c5, 3),
        "source": "Baltic API",
        "fetch_time": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
