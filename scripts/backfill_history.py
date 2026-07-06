import argparse
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from cape_transform import (
    expand_api_payload_to_long,
    finalize_daily_from_long,
    pivot_to_wide,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
DAILY_CSV = DATA_DIR / "cape_daily.csv"
LATEST_JSON = DATA_DIR / "cape_latest.json"
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


def log(message: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = pd.Timestamp.utcnow().replace(microsecond=0).isoformat()
    line = f"{timestamp}Z {message}"
    print(line)
    with BACKFILL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _json_ready(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value


def write_latest_from_daily(daily: pd.DataFrame) -> None:
    latest = daily.sort_values("date").iloc[-1].to_dict()
    latest = {key: _json_ready(value) for key, value in latest.items()}
    with LATEST_JSON.open("w", encoding="utf-8") as handle:
        json.dump(latest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


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


def write_outputs(raw_history: pd.DataFrame, filled_history: pd.DataFrame, write_parquet: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_history.to_csv(HISTORY_RAW_CSV, index=False)
    filled_history.to_csv(HISTORY_FILLED_CSV, index=False)
    filled_history.to_csv(DAILY_CSV, index=False)
    write_latest_from_daily(filled_history)
    log(f"Wrote {HISTORY_RAW_CSV} rows={len(raw_history)}")
    log(f"Wrote {HISTORY_FILLED_CSV} rows={len(filled_history)}")
    log(f"Initialized main daily file {DAILY_CSV} rows={len(filled_history)}")
    log(f"Wrote latest file {LATEST_JSON}")

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
            long = expand_api_payload_to_long(payload, source_label=label)
            window_frames.append(long)
            log(f"Fetched window {label} rows={len(long)}")
        except Exception as exc:
            missing_windows.append(label)
            log(f"Missing window {label}: {exc}")

    if not window_frames:
        raise RuntimeError("All backfill windows failed")

    all_long = pd.concat(window_frames, ignore_index=True)
    raw_history = pivot_to_wide(all_long, include_derived=False)
    filled_history, fill_metrics = finalize_daily_from_long(all_long, calendar_start=start)
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
