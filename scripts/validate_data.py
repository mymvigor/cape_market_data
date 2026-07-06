from typing import Any


def validate_data(record: dict[str, Any]) -> None:
    if not record.get("date"):
        raise RuntimeError("Validation failed: date is missing")

    for field in ("C5TC", "C3", "C5"):
        if field not in record:
            raise RuntimeError(f"Validation failed: {field} is missing")
        if record[field] is None:
            raise RuntimeError(f"Validation failed: {field} is null")
        if float(record[field]) <= 0:
            raise RuntimeError(f"Validation failed: {field} must be positive")

    if float(record["C5"]) == 0:
        raise RuntimeError("Validation failed: C5 must not be zero")
