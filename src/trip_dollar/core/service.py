"""Shared input preparation and calculation for the command line and server."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from typing import Callable

from .config import load_config
from .engine import LedgerEngine
from .fx import currency_code, fetch_rate, infer_date, parse_equations
from .parser import expand_records, import_document, parse_fields, resolve_allocation

RateLoader = Callable[[str, str, date], dict]


class InputError(ValueError):
    def __init__(self, message: str, *, field: str = "source", line: int | None = None):
        super().__init__(message)
        self.field = field
        self.line = line


@dataclass(frozen=True)
class LedgerOptions:
    participants: list[str]
    base: str
    split: list[str]
    fx: list[str]
    initial_currency: str | None = None
    fx_date: date | None = None


def prepare_config(
    options: LedgerOptions, source: str, *, rate_loader: RateLoader = fetch_rate,
) -> tuple[dict, list[dict], list[dict]]:
    people = []
    for value in options.participants:
        code, separator, name = value.partition("=")
        if separator and not name.strip():
            raise InputError(f"Missing name after {code}=.", field="participants")
        people.append({"id": code, "code": code, "name": name.strip() if separator else code})
    if len(set(options.split)) != len(options.split):
        raise InputError("The default split cannot contain the same participant twice.", field="participants")
    try:
        base = currency_code(options.base)
    except ValueError as exc:
        raise InputError(str(exc), field="base") from exc
    try:
        rates, provenance = parse_equations(options.fx, base)
    except ValueError as exc:
        raise InputError(str(exc), field="fx") from exc
    config = {"base_currency": base, "participants": people,
              "fx_rates": {code: str(rate) for code, rate in rates.items()},
              "default_allocation": {"type": "equal_split", "participants": options.split}}
    try:
        registry = load_config(config)
    except ValueError as exc:
        raise InputError(str(exc), field="participants") from exc
    records = list(expand_records(import_document(source), registry, discover_currencies=True))
    if not records:
        raise InputError("The input file contains no expenses.")
    try:
        current = currency_code(options.initial_currency) if options.initial_currency else None
    except ValueError as exc:
        raise InputError(str(exc), field="initial_currency") from exc
    required = set()
    for index, record in enumerate(records, 1):
        fields = parse_fields(record, registry)
        line = record.get("source_sequence", index)
        if fields.error:
            raise InputError(f"Line {line}, {record['raw_text'].strip()!r}: {fields.error}.", line=line)
        if fields.amount is None or not fields.amount.is_finite() or fields.amount < 0:
            raise InputError(f"Line {line}: the amount must be finite and nonnegative.", line=line)
        if resolve_allocation(fields.allocation_token, registry) is None:
            raise InputError(f"Line {line}: use A for everyone, or distinct registered participant letters for the allocation.", line=line)
        if fields.explicit_currency:
            current = currency_code(fields.explicit_currency)
        if current is None:
            raise InputError(f"Line {line}, {record['raw_text'].strip()!r}: currency is missing. Add a currency label or supply --initial-currency. No settlement was calculated.", line=line)
        required.add(current)
    missing = required - rates.keys()
    if options.initial_currency:
        missing |= {currency_code(options.initial_currency)} - rates.keys()
    if missing:
        try:
            requested = options.fx_date or infer_date(source)
        except ValueError as exc:
            raise InputError(str(exc), field="fx_date") from exc
        for currency in sorted(missing):
            try:
                reference = rate_loader(currency, base, requested)
            except (ValueError, OSError) as exc:
                raise InputError(str(exc), field="fx") from exc
            config["fx_rates"][currency] = reference["rate"]
            provenance.append(reference)
    if options.initial_currency:
        config["initial_currency"] = currency_code(options.initial_currency)
    return config, provenance, records


def calculate(options: LedgerOptions, source: str, **kwargs) -> dict:
    config, provenance, records = prepare_config(options, source, **kwargs)
    result = LedgerEngine(config).process(records)
    if result.errors:
        raise InputError("; ".join(result.errors))
    audit = result.to_dict()
    audit.update({"configuration": config, "fx_provenance": provenance, "source_text": source,
                  "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
    return audit
