"""Command-line entry point; configuration, FX I/O and accounting stay separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import DecimalException
from pathlib import Path

from .core.engine import LedgerEngine
from .core.fx import currency_code, fetch_rate
from .report import text_report
from .core.service import LedgerOptions, prepare_config


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split trip expenses and show who owes whom.",
        epilog="Example: trip-dollar trip.txt --participants L B D M --base CAD --split L B --fx CAD=88.6ISK",
    )
    parser.add_argument("input", type=Path, help="raw expense file")
    parser.add_argument("--participants", nargs="+", required=True, metavar="CODE[=NAME]", help="everyone on the trip")
    parser.add_argument("--base", type=currency_code, required=True, help="currency for final payments, e.g. CAD")
    parser.add_argument("--split", nargs="+", required=True, metavar="CODE", help="who shares expenses with no allocation suffix")
    parser.add_argument("--fx", nargs="+", action="extend", default=[], metavar="EQUATION", help="rates in either direction, e.g. CAD=88.6ISK USD=1.37CAD")
    parser.add_argument("--fx-date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="historical rate date; otherwise inferred from the trip's final date")
    parser.add_argument("--initial-currency", type=currency_code, help="currency before the first explicit label; never guessed")
    parser.add_argument("--audit", type=Path, help="save exact ledger, resolved configuration, and FX provenance as JSON")
    return parser


def build_config(args, source: str) -> tuple[dict, list[dict], list[dict]]:
    return prepare_config(
        LedgerOptions(args.participants, args.base, args.split, args.fx,
                      args.initial_currency, args.fx_date),
        source, rate_loader=fetch_rate,
    )


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        source = args.input.read_text(encoding="utf-8")
        config, provenance, transactions = build_config(args, source)
        result = LedgerEngine(config).process(transactions)
        audit = result.to_dict()
        audit.update({"configuration": config, "fx_provenance": provenance,
                      "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
        if args.audit:
            inputs = [args.input, Path(".trip-dollar/fx-cache.json")]
            if args.audit.resolve() in {p.resolve() for p in inputs if p}:
                raise ValueError("--audit must not overwrite an input or FX cache file.")
            args.audit.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if result.errors:
            print("Cannot calculate settlement:", file=sys.stderr)
            for tx in result.transactions:
                for error in tx.errors:
                    print(f"  Line {tx.source_sequence}, {tx.raw_text.strip()!r}: {error}", file=sys.stderr)
            for error in result.errors:
                if not any(error.startswith(f"{tx.id}:") for tx in result.transactions):
                    print(f"  {error}", file=sys.stderr)
        else:
            names = {person["id"]: (f"{person['name']} ({person['code']})" if person.get("name") and person["name"] != person["code"]
                                    else person["code"]) for person in config["participants"]}
            print(text_report(result, names=names))
            if provenance:
                print("\nExchange rates")
                for reference in provenance:
                    if "equation" in reference:
                        left, right = reference["equation"].upper().split("=")
                        print(f"  1 {left} = {right[:-3]} {right[-3:]} (supplied)")
                        continue
                    print(f"  1 {reference['currency']} = {reference['rate']} {result.base_currency} ({reference['source']}"
                          + (f", {reference['effective_date']}" if "effective_date" in reference else "") + ")")
            if args.audit:
                print(f"\nAudit saved to {args.audit}")
        return 2 if result.errors else 0
    except (ValueError, OSError, KeyError, TypeError, DecimalException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
