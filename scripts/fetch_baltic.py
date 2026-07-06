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


def _list_from_payload(payload: Any, label: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        preferred_keys = ("data", "rows", "results", "feeds", "items")
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for value in payload.values():
            if isinstance(value, list):
                return value
    raise RuntimeError(f"Baltic API {label} payload does not contain a list")


def _payload_to_dataframe(payload: Any, label: str) -> pd.DataFrame:
    rows = _list_from_payload(payload, label)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"Baltic API {label} payload produced an empty DataFrame")
    return frame


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
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        data = payload["data"]
        if data and isinstance(data[0], dict) and {"date", "value"} <= set(data[0]):
            return _series_from_data(data, value_name)

    rows = _payload_to_rows(payload)
    for row in rows:
        if isinstance(row.get("data"), list):
            return _series_from_data(row["data"], value_name)
    raise RuntimeError(f"Baltic API main payload missing data array for {value_name}")


def _extract_route_dataframe(payload: Any) -> pd.DataFrame:
    route_frame = _payload_to_dataframe(payload, "route")
    print(f"Debug: route DataFrame columns={list(route_frame.columns)}")

    required = {"code", "data"}
    missing = required - set(route_frame.columns)
    if missing:
        raise RuntimeError(f"Baltic API route DataFrame missing columns: {sorted(missing)}")

    return route_frame


def _join_route_data(main: pd.DataFrame, route_frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    merged = main.copy()
    route_codes: list[str] = []

    for _, row in route_frame.iterrows():
        code = row["code"]
        if pd.isna(code):
            continue
        code = str(code)
        route_codes.append(code)

        if not isinstance(row["data"], list):
            raise RuntimeError(f"Baltic API route data for {code} is not a list")
        route_data = pd.DataFrame(row["data"])
        missing = {"date", "value"} - set(route_data.columns)
        if missing:
            raise RuntimeError(f"Baltic API route data for {code} missing fields: {sorted(missing)}")

        route_data = route_data[["date", "value"]].copy()
        route_data["date"] = pd.to_datetime(route_data["date"], errors="coerce").dt.date
        route_data[code] = pd.to_numeric(route_data["value"], errors="coerce")
        route_data = route_data.drop(columns=["value"]).dropna(subset=["date", code])
        if route_data.empty:
            continue
        route_data = route_data.drop_duplicates(subset=["date"], keep="last").set_index("date")
        merged = merged.join(route_data, how="outer")

    return merged.sort_index(), route_codes


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

    print(f"Debug: main payload type={type(c5tc_payload).__name__}")
    print(f"Debug: route payload type={type(routes_payload).__name__}")

    main = _extract_main_series(c5tc_payload, "C5TC")
    route_frame = _extract_route_dataframe(routes_payload)
    merged, route_codes = _join_route_data(main, route_frame)

    print(f"Debug: route codes list={route_codes}")
    print(f"Debug: merged DataFrame columns={list(merged.reset_index().columns)}")

    missing_output_columns = {"C5TC", "C3", "C5"} - set(merged.columns)
    if missing_output_columns:
        raise RuntimeError(
            "Baltic API merged data missing required columns "
            f"{sorted(missing_output_columns)}; actual route codes list={route_codes}"
        )

    valid = merged.dropna(subset=["C5TC", "C3", "C5"])
    if valid.empty:
        raise RuntimeError(
            "Baltic API returned no rows with C5TC, C3, and C5 all present; "
            f"actual route codes list={route_codes}"
        )

    latest_date = max(valid.index)
    print(f"Debug: latest date={latest_date.isoformat()}")
    latest = valid.loc[latest_date]
    return {
        "date": latest_date.isoformat(),
        "C5TC": latest["C5TC"],
        "C3": latest["C3"],
        "C5": latest["C5"],
    }


if __name__ == "__main__":
    print(fetch_baltic_data())
