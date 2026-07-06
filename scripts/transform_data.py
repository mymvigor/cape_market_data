from datetime import datetime, timezone
from typing import Any

import pandas as pd


CORE_COLUMNS = [
    "C5TC (182)",
    "C5TC",
    "BCI",
    "C16_182",
    "C3",
    "C5",
    "C3-TCE",
    "C5-TCE",
]
DERIVED_COLUMNS = [
    "C3_minus_C5",
    "C3_div_C5",
    "C3_TCE_minus_C5_TCE",
    "C3_TCE_div_C5_TCE",
    "C3_TCE_div_C5TC_182",
    "C5_TCE_div_C5TC_182",
]


def _divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator
    return result.where(denominator != 0)


def _warn_missing_core_columns(frame: pd.DataFrame) -> None:
    missing = [column for column in CORE_COLUMNS if column not in frame.columns]
    if missing:
        print(f"Warning: core columns missing from Baltic API response: {missing}")


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce")


def output_columns(frame: pd.DataFrame) -> list[str]:
    rest = sorted(
        column
        for column in frame.columns
        if column not in {"date", "fetch_time"} | set(CORE_COLUMNS) | set(DERIVED_COLUMNS)
    )
    return ["date"] + CORE_COLUMNS + DERIVED_COLUMNS + rest + ["fetch_time"]


def transform_data(frame: pd.DataFrame, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    transformed = frame.copy()
    transformed["date"] = pd.to_datetime(transformed["date"], errors="coerce").dt.date.astype(str)
    transformed = transformed.dropna(subset=["date"])

    for column in transformed.columns:
        if column != "date":
            transformed[column] = pd.to_numeric(transformed[column], errors="coerce")

    _warn_missing_core_columns(transformed)

    c3 = _numeric_column(transformed, "C3")
    c5 = _numeric_column(transformed, "C5")
    c3_tce = _numeric_column(transformed, "C3-TCE")
    c5_tce = _numeric_column(transformed, "C5-TCE")
    c5tc_182 = _numeric_column(transformed, "C5TC (182)")

    transformed["C3_minus_C5"] = c3 - c5
    transformed["C3_div_C5"] = _divide(c3, c5)
    transformed["C3_TCE_minus_C5_TCE"] = c3_tce - c5_tce
    transformed["C3_TCE_div_C5_TCE"] = _divide(c3_tce, c5_tce)
    transformed["C3_TCE_div_C5TC_182"] = _divide(c3_tce, c5tc_182)
    transformed["C5_TCE_div_C5TC_182"] = _divide(c5_tce, c5tc_182)
    transformed["fetch_time"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for column in CORE_COLUMNS + DERIVED_COLUMNS:
        if column not in transformed.columns:
            transformed[column] = pd.NA

    return transformed.reindex(columns=output_columns(transformed)).sort_values("date")
