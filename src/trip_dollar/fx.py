"""Explicit exchange equations and dated reference rates.

All returned rates are base-currency units per one foreign-currency unit.
Only currency codes and dates are sent to the provider.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
import os
import tempfile


class FXError(ValueError):
    """A rate cannot be used safely."""


def currency_code(value: str) -> str:
    code = value.upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise FXError(f"Currency {value!r} must be a three-letter code, such as CAD.")
    return code


def positive_decimal(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FXError(f"Invalid exchange rate: {value!r}.") from exc
    if not number.is_finite() or number <= 0:
        raise FXError("Exchange rates must be finite and greater than zero.")
    return number


def parse_equations(equations: list[str], base: str) -> tuple[dict[str, Decimal], list[dict]]:
    rates = {base: Decimal(1)}
    sources = []
    for equation in equations:
        match = re.fullmatch(r"([A-Za-z]{3})=([0-9]+(?:\.[0-9]+)?)([A-Za-z]{3})", equation)
        if not match:
            raise FXError(f"Invalid FX equation {equation!r}. Use CAD=88.6ISK or ISK=0.0113CAD.")
        left, value, right = match.groups()
        left, right = left.upper(), right.upper()
        if left == right or base not in (left, right):
            raise FXError(f"{equation}: use two different currencies, including base currency {base}.")
        value = positive_decimal(value)
        foreign = right if left == base else left
        with localcontext() as context:
            context.prec = 34
            rate = Decimal(1) / value if left == base else value
        if foreign in rates and rates[foreign] != rate:
            raise FXError(f"Conflicting FX equations for {foreign}/{base}. Supply one equation for this pair.")
        rates[foreign] = rate
        sources.append({"currency": foreign, "base_currency": base, "rate": str(rate),
                        "source": "supplied", "equation": equation})
    return rates, sources


def infer_date(text: str) -> date:
    years = set(re.findall(r"(?m)^\s*(\d{4})\.[A-Za-z]+\..+$", text))
    headings = re.findall(r"(?m)^\s*(\d{4})\s*$", text)
    if len(years) != 1 or not headings:
        raise FXError("Cannot determine the trip end date. Supply --fx-date YYYY-MM-DD for online rates.")
    try:
        dates = [date(int(next(iter(years))), int(h[:2]), int(h[2:])) for h in headings]
    except ValueError as exc:
        raise FXError("Invalid date heading. Correct it or supply --fx-date YYYY-MM-DD.") from exc
    if any(a.month == 12 and b.month == 1 for a, b in zip(dates, dates[1:])):
        raise FXError("The trip may cross a year boundary. Supply --fx-date YYYY-MM-DD.")
    return max(dates)


def _download(url: str):
    try:
        request = Request(url, headers={"User-Agent": "trip-dollar/0.1.0", "Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            return json.load(response, parse_float=Decimal)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise FXError("Could not retrieve online rates. Retry or supply --fx CURRENCY=amountBASE.") from exc


def fetch_rate(currency: str, base: str, requested: date, cache_path: Path) -> dict:
    """Use the newest ECB observation on/before the date, within seven days.

    Query a bounded range explicitly: the provider cannot silently substitute
    today's rate for a historical date. The cache retains the original metadata.
    """
    if requested > date.today():
        raise FXError("Online rates are unavailable for future dates. Supply an explicit --fx rate.")
    start = requested - timedelta(days=7)
    key = f"ecb:{currency}:{base}:{requested}"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            if not isinstance(cache, dict):
                raise ValueError
        except (ValueError, OSError) as exc:
            raise FXError(f"Cannot read FX cache {cache_path}. Use a different --fx-cache path.") from exc
    if key in cache:
        entry = cache[key]
        try:
            valid = (entry["currency"] == currency and entry["base_currency"] == base
                     and entry["requested_date"] == requested.isoformat()
                     and start <= date.fromisoformat(entry["effective_date"]) <= requested
                     and entry["source"] == "Frankfurter / ECB")
            positive_decimal(entry["rate"])
            if not valid:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise FXError(f"Invalid FX cache entry in {cache_path}.") from exc
        return {**entry, "cached": True}
    query = urlencode({"base": currency, "quotes": base, "from": start.isoformat(),
                       "to": requested.isoformat(), "providers": "ecb"})
    url = f"https://api.frankfurter.dev/v2/rates?{query}"
    data = _download(url)
    try:
        if not isinstance(data, list):
            raise ValueError
        observations = []
        for row in data:
            observed = date.fromisoformat(row["date"])
            if row["base"] != currency or row["quote"] != base or observed > requested:
                raise ValueError
            if observed < start:
                continue
            observations.append((observed, positive_decimal(row["rate"])))
        effective, rate = max(observations, key=lambda item: item[0])
    except (ValueError, KeyError, TypeError) as exc:
        raise FXError(f"No usable {currency}→{base} rate on or before {requested}. Supply --fx explicitly.") from exc
    entry = {"currency": currency, "base_currency": base, "rate": str(rate),
             "requested_date": requested.isoformat(), "effective_date": effective.isoformat(),
             "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "Frankfurter / ECB", "url": url}
    cache[key] = entry
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so an interrupted write cannot truncate the cache.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=cache_path.parent, delete=False) as temporary:
            temp_name = temporary.name
            json.dump(cache, temporary, indent=2)
        os.replace(temp_name, cache_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return {**entry, "cached": False}
