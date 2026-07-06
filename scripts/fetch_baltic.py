import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from cape_transform import expand_api_payload_to_long


ROOT = Path(__file__).resolve().parents[1]
DAILY_CSV = ROOT / "data" / "cape_daily.csv"
BALTIC_FEED_ID = "FDSZ5H4HS31QCF5TN6OLWZJMBBC1QPIU"
BALTIC_FEED_URL = f"https://api.balticexchange.com/api/v1.3/feed/{BALTIC_FEED_ID}/data"
REQUEST_TIMEOUT_SECONDS = 30


def _existing_latest_date() -> pd.Timestamp | None:
    if not DAILY_CSV.exists() or DAILY_CSV.stat().st_size == 0:
        return None

    try:
        daily = pd.read_csv(DAILY_CSV, usecols=["date"], engine="python", on_bad_lines="skip")
    except Exception as exc:
        print(f"Warning: unable to read existing cape_daily.csv for fetch window; treating as first run: {exc}")
        return None

    if daily.empty:
        return None

    dates = pd.to_datetime(daily["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max()


def _request_feed(url: str, headers: dict[str, str], params: dict[str, str]) -> Any:
    import requests

    response = requests.get(
        url.strip(),
        headers=headers,
        params=params or {},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Baltic API request failed: HTTP {response.status_code}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Baltic API returned non-JSON response") from exc

    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"Baltic API payload must be a non-empty list, got {type(payload).__name__}")
    return payload


def _fetch_window() -> tuple[pd.Timestamp, pd.Timestamp, bool]:
    today = pd.to_datetime(date.today())
    first_run = _existing_latest_date() is None
    if first_run:
        date_from = today - pd.DateOffset(months=3)
    else:
        date_from = today - pd.Timedelta(days=14)
    return date_from, today, first_run


def fetch_baltic_data() -> tuple[pd.DataFrame, dict[str, Any]]:
    api_key = os.getenv("BALTIC_API_KEY")
    if not api_key:
        raise RuntimeError("BALTIC_API_KEY is missing")

    headers = {"x-apikey": os.getenv("BALTIC_API_KEY")}
    date_from, date_to, first_run = _fetch_window()
    params = {
        "from": date_from.strftime("%Y-%m-%d"),
        "to": date_to.strftime("%Y-%m-%d"),
    }

    payload = _request_feed(BALTIC_FEED_URL, headers, params)
    long = expand_api_payload_to_long(payload, source_label="daily fetch")
    short_codes = sorted(long["shortCode"].dropna().unique().tolist()) if not long.empty else []

    print(f"Debug: feed id={BALTIC_FEED_ID}")
    print(f"Debug: fetch window={params['from']} to {params['to']}")
    print(f"Debug: first run={first_run}")
    print(f"Debug: short codes count={len(short_codes)}")

    metadata = {
        "feed_id": BALTIC_FEED_ID,
        "first_run": first_run,
        "short_codes": short_codes,
        "fetch_from": params["from"],
        "fetch_to": params["to"],
    }
    return long, metadata
