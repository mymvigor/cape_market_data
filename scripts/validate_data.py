import pandas as pd

from transform_data import CORE_COLUMNS


def validate_data(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise RuntimeError("Validation failed: no rows returned from Baltic API")
    if "date" not in frame.columns:
        raise RuntimeError("Validation failed: date column is missing")

    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().all():
        raise RuntimeError("Validation failed: all date values are invalid")

    missing = [column for column in CORE_COLUMNS if column not in frame.columns]
    if missing:
        print(f"Warning: core columns missing after transform: {missing}")
