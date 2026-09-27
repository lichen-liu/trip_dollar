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
from .fx import FXError, currency_code, fetch_rate, infer_date, parse_equations
from .parser import expand_records, import_document, parse_fields
from .report import text_report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split trip expenses and show who owes whom.",
        epilog="Example: trip-dollar trip.txt --participants L B D M --base CAD --split L B --fx CAD=88.6ISK",
    )
    parser.add_argument("input", type=Path, help="raw expense file (or legacy configuration JSON)")
    parser.add_argument("transactions", type=Path, nargs="?", help="legacy: transaction file paired with configuration JSON")
    parser.add_argument("--participants", nargs="+", metavar="CODE[=NAME]", help="all participants, including those who paid nothing")
    parser.add_argument("--base", type=currency_code, help="currency for balances and payments, e.g. CAD")
    parser.add_argument("--split", nargs="+", metavar="CODE", help="equal split for records without an allocation suffix")
    parser.add_argument("--fx", nargs="+", action="extend", default=[], metavar="EQUATION", help="rates in either direction, e.g. CAD=88.6ISK USD=1.37CAD")
    parser.add_argument("--fx-date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="historical rate date; otherwise inferred from the trip's final date")
    parser.add_argument("--fx-cache", type=Path, default=Path(".trip-dollar/fx-cache.json"), help="saved online rates (default: .trip-dollar/fx-cache.json)")
    parser.add_argument("--offline", action="store_true", help="require explicit --fx rates; do not access the network")
    parser.add_argument("--initial-currency", type=currency_code, help="currency before the first explicit label; never guessed")
    parser.add_argument("--precision", type=int, default=2, help="decimal places for displayed money (default: 2)")
    parser.add_argument("--overrides", type=Path, help="JSON corrections keyed by transaction ID")
    parser.add_argument("--audit", type=Path, help="save exact ledger, resolved configuration, and FX provenance as JSON")
    parser.add_argument("--json", action="store_true", help="print the full audit as JSON")
    parser.add_argument("--raw-text", action="store_true", help="legacy: read transactions as text instead of JSON")
    return parser


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), parse_float=str)


def build_config(args, source: str, overrides: dict) -> tuple[dict, list[dict]]:
    if not args.participants or not args.base or not args.split:
        raise ValueError("Supply --participants, --base and --split. See --help for an example.")
    if not 0 <= args.precision <= 8:
        raise ValueError("--precision must be between 0 and 8.")
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
              "default_allocation": {"type": "equal_split", "participants": args.split},
              "display_precision": args.precision}
    # Discover explicit labels before lookup; never invent a transaction currency.
    registry = load_config(config)
    records = list(expand_records(import_document(source), registry, discover_currencies=True))
    if not records:
        raise ValueError("The input file contains no expenses.")
    ids = {str(record.get("id", f"tx_{index:03d}")) for index, record in enumerate(records, 1)}
    unknown = set(overrides) - ids
    if unknown:
        raise ValueError(f"Overrides refer to unknown transactions: {', '.join(sorted(unknown))}.")
    current = args.initial_currency
    required = set()
    for index, record in enumerate(records, 1):
        fields = parse_fields(record, registry)
        tx_id = str(record.get("id", f"tx_{index:03d}"))
        override = overrides.get(tx_id, {})
        line = record.get("source_sequence", index)
        if fields.error and not ("amount" in override and "payer_code" in override):
            raise ValueError(f"Line {line}, {record['raw_text'].strip()!r}: {fields.error}.")
        mode = override.get("currency_state_mode", "update")
        if mode not in {"update", "transaction_only"}:
            raise ValueError(f"{tx_id}: currency_state_mode must be update or transaction_only.")
        if override.get("currency") is not None:
            resolved = currency_code(override["currency"])
            if mode == "update":
                current = resolved
        elif fields.explicit_currency:
            resolved = current = currency_code(fields.explicit_currency)
        else:
            resolved = current
        if resolved is None:
            raise ValueError(f"Line {line}, {record['raw_text'].strip()!r}: currency is missing. Add a currency label or supply --initial-currency. No settlement was calculated.")
        required.add(resolved)
    missing = required - rates.keys()
    if args.initial_currency:
        missing |= {args.initial_currency} - rates.keys()
    if missing and args.offline:
        raise FXError(f"Missing rates for {', '.join(sorted(missing))}. Supply --fx equations or omit --offline.")
    if missing:
        requested = args.fx_date or infer_date(source)
        for currency in sorted(missing):
            reference = fetch_rate(currency, args.base, requested, args.fx_cache)
            config["fx_rates"][currency] = reference["rate"]
            provenance.append(reference)
    if args.initial_currency:
        config["initial_currency"] = args.initial_currency
    return config, provenance


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        overrides = read_json(args.overrides) if args.overrides else {}
        if not isinstance(overrides, dict) or any(not isinstance(v, dict) for v in overrides.values()):
            raise ValueError("Overrides must be a JSON object of transaction IDs and correction objects.")
        if args.transactions:
            if args.participants or args.base or args.split or args.fx or args.initial_currency or args.fx_date:
                raise ValueError("Use either configuration JSON plus transactions, or a raw file with CLI options.")
            config = read_json(args.input)
            source = args.transactions.read_text(encoding="utf-8")
            transactions = source if args.raw_text else json.loads(source, parse_float=str)
            provenance = [{"currency": code, "source": "configuration file", "rate": str(value)}
                          for code, value in load_config(config).fx_rates.items()]
        else:
            source = args.input.read_text(encoding="utf-8")
            config, provenance = build_config(args, source, overrides)
            transactions = list(expand_records(import_document(source), load_config(config), discover_currencies=True))
        result = LedgerEngine(config).process(transactions, overrides)
        audit = result.to_dict()
        audit.update({"configuration": config, "fx_provenance": provenance,
                      "overrides": overrides, "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
        if args.audit:
            inputs = [args.input, args.transactions, args.overrides, args.fx_cache]
            if args.audit.resolve() in {p.resolve() for p in inputs if p}:
                raise ValueError("--audit must not overwrite an input or FX cache file.")
            args.audit.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(audit, indent=2, ensure_ascii=False))
        elif result.errors:
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
        if args.json:
            print(json.dumps({"status": "AUDIT_REQUIRED", "errors": [str(exc)], "settlements": []}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
