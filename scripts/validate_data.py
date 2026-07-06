import pandas as pd


def validate_data(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise RuntimeError("Validation failed: no rows returned from Baltic API")

    required = {"date", "shortCode", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Validation failed: missing columns {sorted(missing)}")

    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().all():
        raise RuntimeError("Validation failed: all date values are invalid")
