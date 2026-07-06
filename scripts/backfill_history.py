import argparse
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
HISTORY_CSV = DATA_DIR / "cape_history.csv"
HISTORY_RAW_CSV = DATA_DIR / "cape_history_raw.csv"
HISTORY_FILLED_CSV = DATA_DIR / "cape_history_filled.csv"
HISTORY_PARQUET = DATA_DIR / "cape_history.parquet"
BACKFILL_LOG = LOGS_DIR / "backfill_history_log.txt"

FEED_ID = "FDSZ5H4HS31QCF5TN6OLWZJMBBC1QPIU"
FEED_URL = f"https://api.balticexchange.com/api/v1.3/feed/{FEED_ID}/data"
WINDOW_DAYS = 90
OVERLAP_DAYS = 15
MAX_RETRIES = 3
RETRY_SECONDS = 5

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


def log(message: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = pd.Timestamp.utcnow().replace(microsecond=0).isoformat()
    line = f"{timestamp}Z {message}"
    print(line)
    with BACKFILL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def build_windows(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    current = start
    while current <= end:
        window_end = min(current + pd.Timedelta(days=WINDOW_DAYS), end)
        windows.append((current, window_end))
        if window_end >= end:
            break
        current = window_end - pd.Timedelta(days=OVERLAP_DAYS)
    return windows


def request_window(start: pd.Timestamp, end: pd.Timestamp, headers: dict[str, str]) -> list[dict[str, Any]]:
    import requests

    params = {
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d"),
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(FEED_URL, headers=headers, params=params, timeout=45)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError(f"Expected list payload, got {type(payload).__name__}")
            return payload
        except Exception as exc:
            last_error = exc
            log(f"Warning: window {params['from']} to {params['to']} failed attempt {attempt}/{MAX_RETRIES}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SECONDS * attempt)

    raise RuntimeError(f"window {params['from']} to {params['to']} failed after retries: {last_error}")


def payload_to_long(payload: list[dict[str, Any]], window_label: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for item in payload:
        short_code = item.get("shortCode")
        data = item.get("data")
        if not short_code:
            continue
        if not isinstance(data, list):
            log(f"Warning: {window_label} shortCode {short_code} has non-list data")
            continue

        for point in data:
            if not isinstance(point, dict):
                continue
            records.append(
                {
                    "date": point.get("date"),
                    "shortCode": str(short_code),
                    "value": point.get("value"),
                }
            )

    if not records:
        return pd.DataFrame(columns=["date", "shortCode", "value"])

    long = pd.DataFrame(records)
    long["date"] = pd.to_datetime(long["date"], errors="coerce").dt.date.astype(str)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    return long.dropna(subset=["date", "shortCode"])


def calculate_derived(wide: pd.DataFrame) -> pd.DataFrame:
    result = wide.copy()
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


def order_columns(frame: pd.DataFrame) -> list[str]:
    extras = sorted(
        column
        for column in frame.columns
        if column not in {"date"} | set(CORE_COLUMNS) | set(DERIVED_COLUMNS)
    )
    return ["date"] + CORE_COLUMNS + DERIVED_COLUMNS + extras


def raw_order_columns(frame: pd.DataFrame) -> list[str]:
    extras = sorted(column for column in frame.columns if column not in {"date"} | set(CORE_COLUMNS))
    return ["date"] + CORE_COLUMNS + extras


def long_to_raw_history(long: pd.DataFrame) -> pd.DataFrame:
    if long.empty:
        raise RuntimeError("No history rows were fetched")

    deduped = long.drop_duplicates(subset=["date", "shortCode"], keep="last")
    wide = deduped.pivot(index="date", columns="shortCode", values="value").reset_index()
    wide.columns.name = None
    for column in CORE_COLUMNS:
        if column not in wide.columns:
            wide[column] = pd.NA
    return wide.reindex(columns=raw_order_columns(wide)).sort_values("date")


def fill_history(raw_history: pd.DataFrame, calendar_start: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, float]]:
    raw = raw_history.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["date"]).sort_values("date")
    if raw.empty:
        raise RuntimeError("Raw history has no valid dates")

    latest = raw["date"].max()
    calendar = pd.bdate_range(start=calendar_start.normalize(), end=latest.normalize())
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
    filled = calculate_derived(filled)
    filled = filled.reindex(columns=order_columns(filled)).sort_values("date")
    metrics = {
        "missing_days_count": missing_days_count,
        "forward_filled_ratio": forward_filled_ratio,
    }
    return filled, metrics


def write_outputs(raw_history: pd.DataFrame, filled_history: pd.DataFrame, write_parquet: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_history.to_csv(HISTORY_RAW_CSV, index=False)
    filled_history.to_csv(HISTORY_FILLED_CSV, index=False)
    filled_history.to_csv(HISTORY_CSV, index=False)
    log(f"Wrote {HISTORY_RAW_CSV} rows={len(raw_history)}")
    log(f"Wrote {HISTORY_FILLED_CSV} rows={len(filled_history)}")
    log(f"Wrote legacy alias {HISTORY_CSV} rows={len(filled_history)}")

    if not write_parquet:
        return

    try:
        filled_history.to_parquet(HISTORY_PARQUET, index=False)
        log(f"Wrote {HISTORY_PARQUET}")
    except Exception as exc:
        log(f"Warning: parquet write skipped: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Cape Baltic history from the PIU feed.")
    parser.add_argument("--start", default="2025-01-01", help="Backfill start date, e.g. 2025-01-01.")
    parser.add_argument("--end", default=date.today().isoformat(), help="Backfill end date, defaults to today.")
    parser.add_argument("--parquet", action="store_true", help="Also write data/cape_history.parquet if parquet support is installed.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("BALTIC_API_KEY")
    if not api_key:
        raise RuntimeError("BALTIC_API_KEY is missing")

    start = pd.to_datetime(args.start, errors="raise")
    end = pd.to_datetime(args.end, errors="raise")
    if start > end:
        raise RuntimeError("--start must be on or before --end")

    headers = {"x-apikey": os.getenv("BALTIC_API_KEY")}
    windows = build_windows(start, end)
    log(f"Starting backfill feed={FEED_ID} windows={len(windows)} start={args.start} end={args.end}")

    window_frames: list[pd.DataFrame] = []
    missing_windows: list[str] = []
    for window_start, window_end in windows:
        label = f"{window_start.date()} to {window_end.date()}"
        try:
            payload = request_window(window_start, window_end, headers)
            long = payload_to_long(payload, label)
            window_frames.append(long)
            log(f"Fetched window {label} rows={len(long)}")
        except Exception as exc:
            missing_windows.append(label)
            log(f"Missing window {label}: {exc}")

    if not window_frames:
        raise RuntimeError("All backfill windows failed")

    all_long = pd.concat(window_frames, ignore_index=True)
    raw_history = long_to_raw_history(all_long)
    filled_history, fill_metrics = fill_history(raw_history, start)
    log(
        "Calendar alignment "
        f"missing_days_count={fill_metrics['missing_days_count']} "
        f"forward_filled_ratio={fill_metrics['forward_filled_ratio']:.6f}"
    )
    write_outputs(raw_history, filled_history, args.parquet)

    if missing_windows:
        log(f"Backfill completed with missing windows: {missing_windows}")
    else:
        log("Backfill completed with no missing windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
