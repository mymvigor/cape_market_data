import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from cape_transform import CORE_COLUMNS, finalize_daily_from_long, merge_existing_with_new


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
LOGS_DIR = ROOT / "logs"
DAILY_CSV = DATA_DIR / "cape_daily.csv"
LATEST_JSON = DATA_DIR / "cape_latest.json"
FETCH_LOG = LOGS_DIR / "fetch_log.txt"
SCHEMA_REQUIRED_COLUMNS = ["date"] + CORE_COLUMNS


def _timestamp() -> str:
    return pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")


def _append_log(message: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{pd.Timestamp.utcnow().replace(microsecond=0).isoformat()}Z {message}\n")


def _archive_legacy_csv(reason: str) -> Path | None:
    if not DAILY_CSV.exists() or DAILY_CSV.stat().st_size == 0:
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE_DIR / f"cape_daily_legacy_{_timestamp()}.csv"
    shutil.copy2(DAILY_CSV, target)
    warning = f"Warning: archived legacy cape_daily.csv to {target} ({reason})"
    print(warning)
    _append_log(warning)
    return target


def _is_legacy_schema(frame: pd.DataFrame) -> bool:
    columns = set(frame.columns)
    missing_required = [column for column in SCHEMA_REQUIRED_COLUMNS if column not in columns]
    return len(missing_required) >= max(1, len(SCHEMA_REQUIRED_COLUMNS) // 2)


def _read_existing() -> tuple[pd.DataFrame, bool]:
    if not DAILY_CSV.exists() or DAILY_CSV.stat().st_size == 0:
        return pd.DataFrame(), False
    try:
        existing = pd.read_csv(DAILY_CSV)
    except Exception as exc:
        _archive_legacy_csv(f"read_csv failed: {exc}")
        return pd.DataFrame(), True

    if existing.empty:
        return pd.DataFrame(), False

    if _is_legacy_schema(existing):
        _archive_legacy_csv(f"legacy schema columns={list(existing.columns)}")
        return pd.DataFrame(), True

    return existing, False


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
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    incoming = frame.copy()
    incoming["date"] = pd.to_datetime(incoming["date"], errors="coerce").dt.date.astype(str)
    incoming["value"] = pd.to_numeric(incoming["value"], errors="coerce")
    incoming = incoming.dropna(subset=["date", "shortCode"])
    if incoming.empty:
        raise RuntimeError("No parseable incoming rows to save")

    existing, archived_legacy = _read_existing()
    if not existing.empty and "date" in existing.columns:
        existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.date.astype(str)
        existing = existing.dropna(subset=["date"])
        existing_latest = pd.to_datetime(existing["date"], errors="coerce").max()
        calendar_start = pd.to_datetime(existing["date"], errors="coerce").min()
    else:
        existing_latest = pd.NaT
        calendar_start = pd.to_datetime(incoming["date"], errors="coerce").min()

    incoming_latest = pd.to_datetime(incoming["date"], errors="coerce").max()
    fetch_time = pd.Timestamp.utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not archived_legacy and not pd.isna(existing_latest) and incoming_latest <= existing_latest:
        latest = _write_latest(existing)
        with FETCH_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{fetch_time} No new Baltic data latest_api_date={incoming_latest.date()} latest_local_date={existing_latest.date()}\n")
        print("No new Baltic data")
        return {"status": "no_new_data", "latest": latest}

    combined_long = merge_existing_with_new(existing, incoming)
    daily, metrics = finalize_daily_from_long(combined_long, calendar_start=calendar_start, fetch_time=fetch_time)
    daily.to_csv(DAILY_CSV, index=False)

    latest = _write_latest(daily)
    with FETCH_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{fetch_time} success latest_date={latest['date']} rows={len(daily)} "
            f"missing_days_count={metrics['missing_days_count']} "
            f"forward_filled_ratio={metrics['forward_filled_ratio']:.6f}\n"
        )
    return {"status": "updated", "latest": latest}
