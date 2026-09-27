from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Allocation, LedgerConfig, Participant


class ConfigurationError(ValueError):
    pass


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(f"{label} must be a decimal number") from exc
    if not result.is_finite():
        raise ConfigurationError(f"{label} must be finite")
    return result


def load_config(data: dict[str, Any]) -> LedgerConfig:
    base = str(data.get("base_currency", "")).strip().upper()
    if not base:
        raise ConfigurationError("base_currency is required")

    for item in data.get("participants", []):
        code = item.get("code")
        if not isinstance(code, str) or len(code) != 1 or not code.isascii() or not code.isalpha():
            raise ConfigurationError("participant code must be exactly one letter (a-z or A-Z)")
        if code == "A":
            raise ConfigurationError("participant code 'A' is reserved for all participants")

    participants = tuple(
        Participant(
            id=str(item["id"]),
            code=str(item["code"]),
            name=item.get("name"),
            active=bool(item.get("active", True)),
        )
        for item in data.get("participants", [])
    )
    if not participants:
        raise ConfigurationError("at least one participant is required")
    if len({p.id for p in participants}) != len(participants):
        raise ConfigurationError("participant ids must be unique")
    if len({p.code for p in participants}) != len(participants):
        raise ConfigurationError("participant codes must be unique")
    if any(not p.id or not p.code for p in participants):
        raise ConfigurationError("participant ids and codes cannot be empty")
    if any(p.code == "A" for p in participants):
        raise ConfigurationError("participant code 'A' is reserved for all participants")

    fx_rates: dict[str, Decimal] = {}
    for currency, raw_rate in data.get("fx_rates", {}).items():
        value = raw_rate.get("base_per_unit") if isinstance(raw_rate, dict) else raw_rate
        rate = _decimal(value, f"FX rate for {currency}")
        if rate <= 0:
            raise ConfigurationError(f"FX rate for {currency} must be positive")
        fx_rates[str(currency).upper()] = rate
    fx_rates.setdefault(base, Decimal(1))
    if fx_rates[base] != 1:
        raise ConfigurationError("base currency FX rate must be 1")

    code_to_id = {p.code: p.id for p in participants}
    default = _load_allocation(data.get("default_allocation"), code_to_id, participants)
    initial = data.get("initial_currency")
    if initial is not None:
        initial = str(initial).upper()
        if initial not in fx_rates:
            raise ConfigurationError(f"initial currency {initial!r} has no FX rate")

    return LedgerConfig(
        base_currency=base,
        participants=participants,
        fx_rates=fx_rates,
        default_allocation=default,
        initial_currency=initial,
        display_precision=int(data.get("display_precision", 2)),
        tolerance=_decimal(data.get("tolerance", "0.00000001"), "tolerance"),
    )


def _load_allocation(
    raw: dict[str, Any] | None,
    code_to_id: dict[str, str],
    participants: tuple[Participant, ...],
) -> Allocation:
    if not raw:
        raise ConfigurationError("default_allocation is required")
    kind = str(raw.get("type", "")).lower()
    if kind in {"all", "all_equal"}:
        ids = tuple(p.id for p in participants if p.active)
        return Allocation("all_equal", ids, tuple(Decimal(1) for _ in ids))
    if kind in {"split", "equal_split", "weighted_split"}:
        codes = tuple(str(code) for code in raw.get("participants", []))
        try:
            ids = tuple(code_to_id[code] for code in codes)
        except KeyError as exc:
            raise ConfigurationError(f"unknown default allocation participant: {exc.args[0]}") from exc
        if not ids:
            raise ConfigurationError("default allocation participants cannot be empty")
        supplied = raw.get("weights")
        weights = tuple(_decimal(w, "allocation weight") for w in supplied) if supplied else tuple(Decimal(1) for _ in ids)
        if len(weights) != len(ids) or any(w <= 0 for w in weights):
            raise ConfigurationError("allocation weights must be positive and match participants")
        return Allocation("equal_split" if len(set(weights)) == 1 else "weighted_split", ids, weights)
    if kind == "single":
        code = str(raw.get("participant", ""))
        if code not in code_to_id:
            raise ConfigurationError(f"unknown default allocation participant: {code}")
        return Allocation("single", (code_to_id[code],), (Decimal(1),))
    raise ConfigurationError(f"unsupported default allocation type: {kind!r}")
