import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
DAILY_CSV = DATA_DIR / "cape_daily.csv"
LATEST_JSON = DATA_DIR / "cape_latest.json"
FETCH_LOG = LOGS_DIR / "fetch_log.txt"
OUTPUT_COLUMNS = [
    "date",
    "C5TC",
    "C3",
    "C5",
    "C3_C5_ratio",
    "C3_minus_C5",
    "source",
    "fetch_time",
]


def save_data(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    with LATEST_JSON.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    if DAILY_CSV.exists() and DAILY_CSV.stat().st_size > 0:
        daily = pd.read_csv(DAILY_CSV)
    else:
        daily = pd.DataFrame(columns=OUTPUT_COLUMNS)

    latest = pd.DataFrame([record], columns=OUTPUT_COLUMNS)
    daily = pd.concat([daily, latest], ignore_index=True)
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.date.astype(str)
    daily = daily.dropna(subset=["date"])
    daily = daily.drop_duplicates(subset=["date"], keep="last")
    daily = daily.sort_values("date")
    daily = daily.reindex(columns=OUTPUT_COLUMNS)
    daily.to_csv(DAILY_CSV, index=False)

    with FETCH_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{record['fetch_time']} success date={record['date']} source={record['source']}\n")
