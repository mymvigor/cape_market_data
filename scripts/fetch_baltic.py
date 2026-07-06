import os
from datetime import date
from typing import Any

import pandas as pd
from pandas.tseries.offsets import BDay


C5TC_URL = "https://api.balticexchange.com/api/v1.3/feed/FDS041FOL8AMWM6CHZEXDRAG9P33TT5W/data"
CAPE_ROUTES_URL = "https://api.balticexchange.com/api/v1.3/feed/FDSIR2LD7ZH28DVT07YZDO77YD4K5T3J/data"
REQUEST_TIMEOUT_SECONDS = 30


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
        raise RuntimeError(f"Baltic API request failed for {url}: HTTP {response.status_code}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Baltic API returned non-JSON response for {url}") from exc

    if payload in (None, [], {}):
        raise RuntimeError(f"Baltic API returned empty payload for {url}")
    return payload


def _payload_to_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            data = payload["data"]
            if data and isinstance(data[0], dict) and {"code", "data"} <= set(data[0]):
                return [row for row in data if isinstance(row, dict)]
            return [payload]
        for key in ("results", "feeds", "items"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    raise RuntimeError(f"Unexpected Baltic API payload type: {type(payload).__name__}")


def _series_from_data(data: Any, value_name: str) -> pd.DataFrame:
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Baltic API missing non-empty data array for {value_name}")

    frame = pd.DataFrame(data)
    required = {"date", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Baltic API data for {value_name} missing fields: {sorted(missing)}")

    frame = frame[["date", "value"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame[value_name] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.drop(columns=["value"]).dropna(subset=["date", value_name])
    if frame.empty:
        raise RuntimeError(f"Baltic API data for {value_name} contained no parseable rows")

    return frame.drop_duplicates(subset=["date"], keep="last").set_index("date")


def _extract_main_series(payload: Any, value_name: str) -> pd.DataFrame:
    rows = _payload_to_rows(payload)
    for row in rows:
        if isinstance(row.get("data"), list):
            return _series_from_data(row["data"], value_name)
    raise RuntimeError(f"Baltic API payload missing data array for {value_name}")


def _payload_to_frame_candidates(payload: Any) -> list[pd.DataFrame]:
    candidates: list[pd.DataFrame] = []

    if isinstance(payload, list):
        candidates.append(pd.DataFrame(payload))
    elif isinstance(payload, dict):
        for key in ("data", "results", "feeds", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.append(pd.DataFrame(value))
        candidates.append(pd.DataFrame([payload]))
    else:
        raise RuntimeError(f"Unexpected Baltic API payload type: {type(payload).__name__}")

    return [frame for frame in candidates if not frame.empty]


def _standardize_route_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if "Date" in frame.columns:
        frame = frame.rename(columns={"Date": "date"})
    if "date" not in frame.columns:
        raise RuntimeError("Baltic API Capesize route payload missing Date/date column")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    for column in ("C5TC", "C3", "C5"):
        if column not in frame.columns:
            raise RuntimeError(f"Baltic API Capesize route payload missing column: {column}")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame[["date", "C5TC", "C3", "C5"]].dropna(subset=["date", "C5TC", "C3", "C5"])
    if frame.empty:
        raise RuntimeError("Baltic API Capesize route payload contained no valid C5TC/C3/C5 rows")

    return frame.drop_duplicates(subset=["date"], keep="last").set_index("date").sort_index()


def _nested_routes_to_wide_frame(payload: Any) -> pd.DataFrame:
    rows = _payload_to_rows(payload)
    route_frames: list[pd.DataFrame] = []

    for row in rows:
        code = row.get("code")
        if not code or not isinstance(row.get("data"), list):
            continue
        route_frames.append(_series_from_data(row["data"], str(code)))

    if not route_frames:
        raise RuntimeError("Baltic API Capesize route payload is neither a wide table nor a nested route payload")

    wide = route_frames[0]
    for frame in route_frames[1:]:
        wide = wide.join(frame, how="outer")
    return wide.reset_index()


def _extract_cape_route_frame(payload: Any) -> pd.DataFrame:
    first_error: Exception | None = None
    for frame in _payload_to_frame_candidates(payload):
        try:
            return _standardize_route_frame(frame)
        except RuntimeError as exc:
            if first_error is None:
                first_error = exc

    try:
        return _standardize_route_frame(_nested_routes_to_wide_frame(payload))
    except RuntimeError as exc:
        if first_error is not None:
            raise RuntimeError(f"{first_error}; nested parse also failed: {exc}") from exc
        raise


def fetch_baltic_data() -> dict[str, Any]:
    api_key = os.getenv("BALTIC_API_KEY")
    if not api_key:
        raise RuntimeError("BALTIC_API_KEY is missing")

    headers = {"x-apikey": os.getenv("BALTIC_API_KEY")}
    today = pd.to_datetime(date.today())
    date_from = today - BDay(15)
    params = {
        "from": date_from.strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
    }

    c5tc_payload = _request_feed(C5TC_URL, headers, params)
    routes_payload = _request_feed(CAPE_ROUTES_URL, headers, params)

    _extract_main_series(c5tc_payload, "C5TC")
    routes = _extract_cape_route_frame(routes_payload)
    print(f"Debug: route DataFrame columns={list(routes.reset_index().columns)}")
    if routes.empty:
        raise RuntimeError("Baltic API returned no valid Capesize route rows")

    latest_date = max(routes.index)
    print(f"Debug: latest route date={latest_date.isoformat()}")
    latest = routes.loc[latest_date]
    return {
        "date": latest_date.isoformat(),
        "C5TC": latest["C5TC"],
        "C3": latest["C3"],
        "C5": latest["C5"],
    }


if __name__ == "__main__":
    print(fetch_baltic_data())
