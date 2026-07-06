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


def expand_api_payload_to_long(payload: list[dict[str, Any]], source_label: str = "Baltic API") -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for item in payload:
        short_code = item.get("shortCode")
        data = item.get("data")
        if not short_code:
            continue
        short_code = str(short_code)
        if not isinstance(data, list):
            print(f"Warning: {source_label} shortCode {short_code} has non-list data")
            continue

        for point in data:
            if not isinstance(point, dict):
                continue
            records.append(
                {
                    "date": point.get("date"),
                    "shortCode": short_code,
                    "value": point.get("value"),
                }
            )

    if not records:
        return pd.DataFrame(columns=["date", "shortCode", "value"])

    long = pd.DataFrame(records)
    long["date"] = pd.to_datetime(long["date"], errors="coerce").dt.date.astype(str)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    return long.dropna(subset=["date", "shortCode"])


def pivot_to_wide(long: pd.DataFrame, include_derived: bool = False) -> pd.DataFrame:
    if long.empty:
        raise RuntimeError("No Cape data rows available to pivot")

    deduped = long.drop_duplicates(subset=["date", "shortCode"], keep="last")
    wide = deduped.pivot(index="date", columns="shortCode", values="value").reset_index()
    wide.columns.name = None
    for column in CORE_COLUMNS:
        if column not in wide.columns:
            wide[column] = pd.NA
    if include_derived:
        wide = calculate_derived_metrics(wide)
        return wide.reindex(columns=order_columns(wide, include_fetch_time=False)).sort_values("date")
    return wide.reindex(columns=raw_order_columns(wide)).sort_values("date")


def calculate_derived_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in CORE_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA

    c3 = pd.to_numeric(result["C3"], errors="coerce")
    c5 = pd.to_numeric(result["C5"], errors="coerce")
    c3_tce = pd.to_numeric(result["C3-TCE"], errors="coerce")
    c5_tce = pd.to_numeric(result["C5-TCE"], errors="coerce")
    c5tc_182 = pd.to_numeric(result["C5TC (182)"], errors="coerce")

    result["C3_minus_C5"] = c3 - c5
    result["C3_div_C5"] = (c3 / c5).where(c5 != 0)
    result["C3_TCE_minus_C5_TCE"] = c3_tce - c5_tce
    result["C3_TCE_div_C5_TCE"] = (c3_tce / c5_tce).where(c5_tce != 0)
    result["C3_TCE_div_C5TC_182"] = (c3_tce / c5tc_182).where(c5tc_182 != 0)
    result["C5_TCE_div_C5TC_182"] = (c5_tce / c5tc_182).where(c5tc_182 != 0)
    return result


def raw_order_columns(frame: pd.DataFrame) -> list[str]:
    extras = sorted(column for column in frame.columns if column not in {"date"} | set(CORE_COLUMNS))
    return ["date"] + CORE_COLUMNS + extras


def order_columns(frame: pd.DataFrame, include_fetch_time: bool = True) -> list[str]:
    excluded = {"date"} | set(CORE_COLUMNS) | set(DERIVED_COLUMNS)
    if include_fetch_time:
        excluded.add("fetch_time")
    extras = sorted(column for column in frame.columns if column not in excluded)
    columns = ["date"] + CORE_COLUMNS + DERIVED_COLUMNS + extras
    if include_fetch_time:
        columns.append("fetch_time")
    return columns


def align_business_calendar_and_ffill(
    wide: pd.DataFrame,
    calendar_start: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    raw = wide.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["date"]).sort_values("date")
    if raw.empty:
        raise RuntimeError("No valid dates available for calendar alignment")

    start = pd.to_datetime(calendar_start) if calendar_start is not None else raw["date"].min()
    latest = raw["date"].max()
    calendar = pd.bdate_range(start=start.normalize(), end=latest.normalize())
    raw_indexed = raw.set_index("date").sort_index()
    raw_indexed.index = pd.to_datetime(raw_indexed.index)

    missing_days_count = int(len(calendar.difference(raw_indexed.index.normalize().unique())))
    reindexed = raw_indexed.reindex(calendar)
    pre_fill_missing = int(reindexed.isna().sum().sum())
    filled = reindexed.ffill()
    post_fill_missing = int(filled.isna().sum().sum())
    filled_cells = max(pre_fill_missing - post_fill_missing, 0)
    total_cells = int(filled.shape[0] * filled.shape[1])
    forward_filled_ratio = filled_cells / total_cells if total_cells else 0.0

    filled = filled.reset_index().rename(columns={"index": "date"})
    filled["date"] = filled["date"].dt.date.astype(str)
    metrics = {
        "missing_days_count": missing_days_count,
        "forward_filled_ratio": forward_filled_ratio,
    }
    return filled, metrics


def merge_existing_with_new(existing_daily: pd.DataFrame, new_long: pd.DataFrame) -> pd.DataFrame:
    existing = existing_daily.copy()
    if existing.empty:
        return new_long.copy()

    existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.date.astype(str)
    value_columns = [
        column
        for column in existing.columns
        if column not in {"date", "fetch_time"} | set(DERIVED_COLUMNS)
    ]
    existing_long = existing.melt(
        id_vars=["date"],
        value_vars=value_columns,
        var_name="shortCode",
        value_name="value",
    )
    existing_long = existing_long.dropna(subset=["date", "shortCode", "value"])
    existing_long["value"] = pd.to_numeric(existing_long["value"], errors="coerce")
    combined = pd.concat([existing_long, new_long], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["date", "shortCode"], keep="last")
    return combined


def finalize_daily_from_long(
    long: pd.DataFrame,
    calendar_start: pd.Timestamp | str | None = None,
    fetch_time: str | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    wide = pivot_to_wide(long, include_derived=False)
    filled, metrics = align_business_calendar_and_ffill(wide, calendar_start=calendar_start)
    final = calculate_derived_metrics(filled)
    final["fetch_time"] = fetch_time or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    final = final.reindex(columns=order_columns(final, include_fetch_time=True)).sort_values("date")
    return final, metrics
