import json
from pathlib import Path
from typing import Any

import pandas as pd

from transform_data import output_columns


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
DAILY_CSV = DATA_DIR / "cape_daily.csv"
LATEST_JSON = DATA_DIR / "cape_latest.json"
FETCH_LOG = LOGS_DIR / "fetch_log.txt"


def _read_existing() -> pd.DataFrame:
    if not DAILY_CSV.exists() or DAILY_CSV.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(DAILY_CSV)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _json_ready(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value


def _write_latest(daily: pd.DataFrame) -> dict[str, Any]:
    latest = daily.sort_values("date").iloc[-1].to_dict()
    latest = {key: _json_ready(value) for key, value in latest.items()}
    with LATEST_JSON.open("w", encoding="utf-8") as handle:
        json.dump(latest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return latest


def save_data(frame: pd.DataFrame, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    incoming = frame.copy()
    incoming["date"] = pd.to_datetime(incoming["date"], errors="coerce").dt.date.astype(str)
    incoming = incoming.dropna(subset=["date"])
    if incoming.empty:
        raise RuntimeError("No parseable incoming rows to save")

    existing = _read_existing()
    if not existing.empty and "date" in existing.columns:
        existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.date.astype(str)
        existing = existing.dropna(subset=["date"])
        existing_latest = pd.to_datetime(existing["date"], errors="coerce").max()
    else:
        existing_latest = pd.NaT

    incoming_latest = pd.to_datetime(incoming["date"], errors="coerce").max()
    fetch_time = str(incoming["fetch_time"].dropna().iloc[-1]) if "fetch_time" in incoming.columns and not incoming["fetch_time"].dropna().empty else ""

    if not pd.isna(existing_latest) and incoming_latest <= existing_latest:
        latest = _write_latest(existing)
        with FETCH_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{fetch_time} No new data latest_api_date={incoming_latest.date()} latest_local_date={existing_latest.date()}\n")
        print("No new data")
        return {"status": "no_new_data", "latest": latest}

    combined = pd.concat([existing, incoming], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce").dt.date.astype(str)
    combined = combined.dropna(subset=["date"])
    combined = combined.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    combined = combined.reindex(columns=output_columns(combined))
    combined.to_csv(DAILY_CSV, index=False)

    latest = _write_latest(combined)
    with FETCH_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{fetch_time} success latest_date={latest['date']} rows={len(combined)}\n")
    return {"status": "updated", "latest": latest}
