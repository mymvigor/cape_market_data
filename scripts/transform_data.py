from typing import Any

import pandas as pd

from cape_transform import CORE_COLUMNS, DERIVED_COLUMNS


def transform_data(frame: pd.DataFrame, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    transformed = frame.copy()
    if transformed.empty:
        return transformed

    required = {"date", "shortCode", "value"}
    missing = required - set(transformed.columns)
    if missing:
        raise RuntimeError(f"Transform failed: missing long-format columns {sorted(missing)}")

    transformed["date"] = pd.to_datetime(transformed["date"], errors="coerce").dt.date.astype(str)
    transformed["shortCode"] = transformed["shortCode"].astype(str)
    transformed["value"] = pd.to_numeric(transformed["value"], errors="coerce")
    return transformed.dropna(subset=["date", "shortCode"])
