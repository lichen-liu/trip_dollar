from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import LedgerEngine
from .report import text_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate and settle a configured expense ledger")
    parser.add_argument("config", type=Path, help="ledger configuration JSON")
    parser.add_argument("transactions", type=Path, help="ordered transaction array JSON")
    parser.add_argument("--overrides", type=Path, help="optional transaction override JSON")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--raw-text", action="store_true", help="read existing text records instead of JSON")
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    source = args.transactions.read_text()
    transactions = source if args.raw_text else json.loads(source)
    overrides = json.loads(args.overrides.read_text()) if args.overrides else None
    result = LedgerEngine(config).process(transactions, overrides)
    print(json.dumps(result.to_dict(), indent=2) if args.json else text_report(result))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
