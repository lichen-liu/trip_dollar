from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Allocation, LedgerConfig


def split_compact(text: str, config: LedgerConfig) -> list[tuple[int, int, ParsedFields]]:
    """Find a unique complete interpretation. Never discard unrecognized text.

    Offsets refer to the original source. Two complete parses are enough to
    establish ambiguity; currency/participant collisions are not guessed.
    """
    codes = {p.code for p in config.participants}
    separator = re.compile(r"[\s;,]*")
    amount_pattern = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
    paths = {len(text): [()]}
    for start in range(len(text) - 1, -1, -1):
        skipped = separator.match(text, start).end()
        if skipped != start:
            paths[start] = paths.get(skipped, [])
            continue
        if text[start] not in codes:
            continue
        amount_start = start + 1
        while amount_start < len(text) and text[amount_start].isspace():
            amount_start += 1
        match = amount_pattern.match(text, amount_start)
        if not match:
            continue
        end = match.end()
        allocations = [(end, None)]
        if end < len(text) and text[end] in codes | {"A"}:
            allocations.append((end + 1, text[end]))
        found = []
        for allocation_end, token in allocations:
            endings = [(allocation_end, None)]
            currency_start = allocation_end
            while currency_start < len(text) and text[currency_start].isspace():
                currency_start += 1
            for currency in config.fx_rates:
                if text[currency_start:currency_start + len(currency)].upper() == currency:
                    endings.append((currency_start + len(currency), currency))
            for record_end, currency in endings:
                for tail in paths.get(record_end, []):
                    fields = ParsedFields(text[start], Decimal(match.group()), token, currency)
                    candidate = ((start, record_end, fields),) + tail
                    if candidate not in found:
                        found.append(candidate)
                    if len(found) == 2:
                        break
                if len(found) == 2:
                    break
            if len(found) == 2:
                break
        paths[start] = found
    matches = paths.get(0, [])
    if len(matches) > 1:
        raise ValueError("ambiguous compact records; use explicit per-transaction fields to disambiguate")
    if not matches or not matches[0]:
        raise ValueError("unrecognized or incomplete compact record stream")
    return list(matches[0])


def expand_records(raw_transactions, config):
    if isinstance(raw_transactions, str):
        raw_transactions = [{"raw_text": raw_transactions}]
    for source_sequence, raw in enumerate(raw_transactions, 1):
        raw = {"raw_text": raw} if isinstance(raw, str) else dict(raw)
        if "payer_code" in raw or "amount" in raw:
            yield raw
            continue
        text = str(raw.get("raw_text", ""))
        try:
            records = split_compact(text, config)
        except ValueError as exc:
            # Preserve legacy single-record diagnostics, but never guess ambiguity.
            if "ambiguous" in str(exc):
                raw["_parse_error"] = str(exc)
            yield raw
            continue
        for index, (start, end, fields) in enumerate(records, 1):
            item = dict(raw)
            item["_fields"] = fields
            item["source_text"] = text
            item["source_sequence"] = source_sequence
            item["source_span"] = (start, end)
            if len(records) > 1:
                item["raw_text"] = text[start:end]
                if "id" in raw:
                    item["id"] = f"{raw['id']}:{index}"
            yield item


@dataclass
class ParsedFields:
    payer_code: str | None = None
    amount: Decimal | None = None
    allocation_token: str | None = None
    explicit_currency: str | None = None
    error: str | None = None


def parse_fields(raw: dict[str, Any], config: LedgerConfig) -> ParsedFields:
    """Parse structured fields, or fall back to compact ``raw_text`` syntax."""
    if "_parse_error" in raw:
        return ParsedFields(error=raw["_parse_error"])
    if "_fields" in raw:
        return raw["_fields"]
    if "payer_code" in raw or "amount" in raw:
        payer_code = str(raw.get("payer_code", ""))
        allocation_token = None if raw.get("allocation") is None else str(raw["allocation"])
        currency = raw.get("currency")
        try:
            amount = Decimal(str(raw.get("amount")))
            if not amount.is_finite() or amount < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return ParsedFields(
                payer_code=payer_code,
                allocation_token=allocation_token,
                explicit_currency=None if currency is None else str(currency).upper(),
                error="invalid amount",
            )
        return ParsedFields(
            payer_code=payer_code,
            amount=amount,
            allocation_token=allocation_token,
            explicit_currency=None if currency is None else str(currency).upper(),
        )

    text = str(raw.get("raw_text", "")).strip()
    currency = None
    body = text
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", parts[1]):
        # Preserve an explicit but unsupported currency so validation can report
        # the missing FX rate instead of misclassifying the amount syntax.
        body, currency = parts[0], parts[1].upper()

    codes = {p.code for p in config.participants}
    payer = body[:1] if body[:1] in codes else None
    if payer is None:
        return ParsedFields(error="unknown payer")
    remainder = body[len(payer):]
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(.+)?", remainder)
    if not match:
        return ParsedFields(payer_code=payer, error="invalid amount or allocation suffix")
    try:
        amount = Decimal(match.group(1))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
    except InvalidOperation:
        return ParsedFields(payer_code=payer, error="invalid amount")
    return ParsedFields(payer, amount, match.group(2), currency)


def resolve_allocation(token: str | None, config: LedgerConfig) -> Allocation | None:
    if token is None or token == "":
        return config.default_allocation
    if token == "A":
        ids = tuple(p.id for p in config.participants if p.active)
        return Allocation("all_equal", ids, tuple(Decimal(1) for _ in ids))
    participant = next((p for p in config.participants if p.code == token), None)
    if participant is None:
        return None
    return Allocation("single", (participant.id,), (Decimal(1),))


def allocation_from_override(raw: dict[str, Any], config: LedgerConfig) -> Allocation | None:
    kind = str(raw.get("type", "")).lower()
    if kind in {"all", "all_equal"}:
        return resolve_allocation("A", config)
    code_map = {p.code: p.id for p in config.participants}
    id_set = {p.id for p in config.participants}
    if kind == "single":
        value = str(raw.get("participant", ""))
        participant_id = code_map.get(value, value if value in id_set else None)
        return None if participant_id is None else Allocation("single", (participant_id,), (Decimal(1),))
    if kind in {"split", "equal_split", "weighted_split"}:
        values = [str(x) for x in raw.get("participants", [])]
        ids = tuple(code_map.get(x, x if x in id_set else "") for x in values)
        if not ids or any(not x for x in ids):
            return None
        supplied = raw.get("weights")
        try:
            weights = tuple(Decimal(str(x)) for x in supplied) if supplied else tuple(Decimal(1) for _ in ids)
        except InvalidOperation:
            return None
        if len(weights) != len(ids) or any(w <= 0 for w in weights):
            return None
        return Allocation(kind, ids, weights)
    return None
