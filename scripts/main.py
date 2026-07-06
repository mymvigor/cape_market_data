import json
import sys

from fetch_baltic import fetch_baltic_data
from save_data import save_data
from transform_data import transform_data
from validate_data import validate_data


def main() -> int:
    try:
        raw = fetch_baltic_data()
        transformed = transform_data(raw)
        validate_data(transformed)
        save_data(transformed)
    except Exception as exc:
        print(f"Baltic data pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(transformed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
