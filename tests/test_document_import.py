from pathlib import Path
import json
import re
from collections import defaultdict
from decimal import Decimal

from trip_dollar import LedgerEngine
from trip_dollar.config import load_config
from trip_dollar.parser import expand_records


def syntax_config():
    # Artificial unit rates for parser tests only, not the user's trip FX.
    return {
        "base_currency": "CAD",
        "participants": [{"id": code, "code": code} for code in "LBDM"],
        "fx_rates": {"CAD": "1", "ISK": "1"},
        "default_allocation": {"type": "equal_split", "participants": ["L", "B"]},
    }


def test_currency_before_allocation_and_multirecord_merchants():
    text = "2026.Aug.Example\nFlight: B10467.28CADA\n0808\nShop: D10A L20L\n0807\nCafe: L30ISKA"
    result = LedgerEngine(syntax_config()).process(text)
    assert not result.errors
    assert [t.currency for t in result.transactions] == ["CAD", "CAD", "CAD", "ISK"]
    assert [t.date for t in result.transactions] == [None, "0808", "0808", "0807"]
    assert [t.description for t in result.transactions] == ["Flight", "Shop", "Shop", "Cafe"]
    assert result.transactions[2].allocation.participants == ("L",)
    assert result.transactions[0].allocation.participants == ("L", "B", "D", "M")
    for tx in result.transactions:
        assert tx.source_title == "2026.Aug.Example"
        start, end = tx.source_span
        assert tx.source_text[start:end] == tx.raw_text


def test_unknown_prose_blocks_settlement():
    result = LedgerEngine(syntax_config()).process("2026.Aug.Example\nShop: L10CADA\nmisspelled heading")
    assert result.errors
    assert not result.settlements


def test_user_document_preserves_all_records_and_repeated_expenses():
    text = (Path(__file__).parents[1] / "examples/iceland-2026/raw.txt").read_text()
    records = list(expand_records(text, load_config(syntax_config())))
    assert all("_fields" in record for record in records)
    # 11 advance bookings, then 9/10/8/8/4/10/3/6/7 expenses by day.
    assert len(records) == 76
    parking = [r for r in records if r["description"] == "Bingvellir"]
    assert len(parking) == 2
    assert [r["_fields"].amount for r in parking] == [1000, 1000]
    assert [r["_fields"].allocation_token for r in records if r["description"] == "Geysir Glyma"] == ["A", "L", "A"]


def test_corrected_trip_against_reference_arithmetic():
    root = Path(__file__).parents[1] / "examples/iceland-2026"
    raw = (root / "raw.txt").read_text()
    result = LedgerEngine(json.loads((root / "config.json").read_text())).process(raw)
    assert not result.errors
    assert len(result.transactions) == 76
    assert all(t.currency == "ISK" for t in result.transactions[11:])
    # Independent extraction from the raw merchant lines, not engine parser output.
    totals = defaultdict(Decimal)
    currency = None
    count = 0
    for line in raw.splitlines():
        if ":" not in line:
            continue
        for record in line.split(":", 1)[1].split():
            match = re.fullmatch(r"([LBDM])(\d+(?:\.\d+)?)(CAD|ISK)?([ALBDM])?", record)
            assert match is not None
            payer, amount, explicit, allocation = match.groups()
            currency = explicit or currency
            totals[payer, currency] += Decimal(amount)
            count += 1
    assert count == 76
    assert dict(totals) == {("B", "CAD"): Decimal("14602.61"), ("L", "CAD"): Decimal("1188.93"),
                            ("L", "ISK"): Decimal("572172"), ("D", "ISK"): Decimal("155408")}
    assert result.total_expense == Decimal("24013.1940")
    assert result.net == {"L": Decimal("1630.058225"), "B": Decimal("8595.935625"),
                          "D": Decimal("-4234.941725"), "M": Decimal("-5991.052125")}
    assert sum(result.paid.values()) == sum(result.share.values())
    assert sum(result.net.values()) == 0
