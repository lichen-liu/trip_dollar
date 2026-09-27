"""Command-line entry point; configuration, FX I/O and accounting stay separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import DecimalException
from pathlib import Path

from .config import load_config
from .engine import LedgerEngine
from .fx import currency_code, fetch_rate, infer_date, parse_equations
from .parser import expand_records, import_document, parse_fields
from .report import text_report


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
    people = []
    for value in args.participants:
        code, separator, name = value.partition("=")
        if separator and not name.strip():
            raise ValueError(f"Missing name after {code}=.")
        people.append({"id": code, "code": code, "name": name.strip() if separator else code})
    if len(set(args.split)) != len(args.split):
        raise ValueError("--split cannot contain the same participant twice.")
    rates, provenance = parse_equations(args.fx, args.base)
    config = {"base_currency": args.base, "participants": people,
              "fx_rates": {code: str(rate) for code, rate in rates.items()},
              "default_allocation": {"type": "equal_split", "participants": args.split}}
    # Discover explicit labels before lookup; never invent a transaction currency.
    registry = load_config(config)
    records = list(expand_records(import_document(source), registry, discover_currencies=True))
    if not records:
        raise ValueError("The input file contains no expenses.")
    current = args.initial_currency
    required = set()
    for index, record in enumerate(records, 1):
        fields = parse_fields(record, registry)
        line = record.get("source_sequence", index)
        if fields.error:
            raise ValueError(f"Line {line}, {record['raw_text'].strip()!r}: {fields.error}.")
        if fields.explicit_currency:
            resolved = current = currency_code(fields.explicit_currency)
        else:
            resolved = current
        if resolved is None:
            raise ValueError(f"Line {line}, {record['raw_text'].strip()!r}: currency is missing. Add a currency label or supply --initial-currency. No settlement was calculated.")
        required.add(resolved)
    missing = required - rates.keys()
    if args.initial_currency:
        missing |= {args.initial_currency} - rates.keys()
    if missing:
        requested = args.fx_date or infer_date(source)
        for currency in sorted(missing):
            reference = fetch_rate(currency, args.base, requested, Path(".trip-dollar/fx-cache.json"))
            config["fx_rates"][currency] = reference["rate"]
            provenance.append(reference)
    if args.initial_currency:
        config["initial_currency"] = args.initial_currency
    return config, provenance, records


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
