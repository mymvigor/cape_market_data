import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DAILY_CSV = ROOT / "data" / "cape_daily.csv"
BALTIC_FEED_ID = "FDSZ5H4HS31QCF5TN6OLWZJMBBC1QPIU"
BALTIC_FEED_URL = f"https://api.balticexchange.com/api/v1.3/feed/{BALTIC_FEED_ID}/data"
REQUEST_TIMEOUT_SECONDS = 30


def _existing_latest_date() -> pd.Timestamp | None:
    if not DAILY_CSV.exists() or DAILY_CSV.stat().st_size == 0:
        return None

    try:
        daily = pd.read_csv(DAILY_CSV, usecols=["date"])
    except (ValueError, pd.errors.EmptyDataError):
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


def _payload_to_wide_frame(payload: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    wide: pd.DataFrame | None = None
    short_codes: list[str] = []

    for item in payload:
        short_code = item.get("shortCode")
        data = item.get("data")
        if not short_code:
            continue
        short_code = str(short_code)
        short_codes.append(short_code)

        if not isinstance(data, list):
            print(f"Warning: shortCode {short_code} has no list data")
            continue

        frame = pd.DataFrame(data)
        missing = {"date", "value"} - set(frame.columns)
        if missing:
            print(f"Warning: shortCode {short_code} data missing fields {sorted(missing)}")
            continue

        frame = frame[["date", "value"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame[short_code] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.drop(columns=["value"]).dropna(subset=["date"])
        frame = frame.drop_duplicates(subset=["date"], keep="last").set_index("date")

        if wide is None:
            wide = frame
        else:
            wide = wide.join(frame, how="outer")

    if wide is None or wide.empty:
        raise RuntimeError("Baltic API returned no parseable data rows")

    return wide.sort_index().reset_index(), short_codes


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
    frame, short_codes = _payload_to_wide_frame(payload)

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
    return frame, metadata
