from decimal import Decimal
import json
from pathlib import Path

import pytest

from trip_dollar import LedgerEngine


def engine():
    return LedgerEngine({
        "base_currency": "CAD",
        "participants": [{"id": "left", "code": "L"}, {"id": "bob", "code": "B"}, {"id": "charlie", "code": "C"}],
        "fx_rates": {"CAD": "1", "USD": "1.25"},
        "default_allocation": {"type": "equal_split", "participants": ["L", "B"]},
    })


@pytest.mark.parametrize("text", [
    "L60 CAD C90A B24C USD",
    "L60CADC90AB24CUSD",
    "L60 CAD; C90A; B24C USD",
    "L60 CAD\nC90A\tB24C USD",
])
def test_existing_stream_variants_have_identical_accounting(text):
    result = engine().process(text)
    assert not result.errors
    assert [t.original_amount for t in result.transactions] == [60, 90, 24]
    assert [t.currency for t in result.transactions] == ["CAD", "CAD", "USD"]
    assert result.paid == {"left": 60, "bob": 30, "charlie": 90}
    assert result.share == {"left": 60, "bob": 60, "charlie": 60}
    for tx in result.transactions:
        assert tx.source_text == text
        start, end = tx.source_span
        assert text[start:end] == tx.raw_text


def test_payer_boundary_vs_single_allocation():
    result = engine().process("L10CADB20CL30")
    assert not result.errors
    assert [t.payer_code for t in result.transactions] == ["L", "B", "L"]
    assert result.transactions[1].shares == {"charlie": Decimal(20)}


def test_multi_record_ids_overrides_and_original_order():
    result = engine().process([
        {"id": "line", "date": "0902", "raw_text": "L10USD B20 C30"},
        {"date": "0101", "raw_text": "L40"},
    ], {"line:2": {"currency": "CAD"}})
    assert not result.errors
    assert [t.id for t in result.transactions[:3]] == ["line:1", "line:2", "line:3"]
    assert [t.currency for t in result.transactions] == ["USD", "CAD", "CAD", "CAD"]
    assert [t.sequence for t in result.transactions] == [1, 2, 3, 4]


@pytest.mark.parametrize("text", ["L10CAD garbage B20", "L10CAD B", "L10CAD Z20", "L-10CAD B20"])
def test_bad_stream_cannot_silently_drop_text_or_settle(text):
    result = engine().process(text)
    assert result.errors
    assert not result.settlements


def test_unknown_initial_currency_in_multi_record_line_blocks_settlement():
    result = engine().process("L10 B20 C30CAD L40")
    assert [t.currency for t in result.transactions] == [None, None, "CAD", "CAD"]
    assert result.errors and not result.settlements


def test_ambiguous_currency_and_allocation_are_rejected():
    config = {
        "base_currency": "CAD",
        "participants": [{"id": "left", "code": "L"}, {"id": "bob", "code": "B"}],
        "fx_rates": {"CAD": 1, "B": 1},
        "initial_currency": "CAD",
        "default_allocation": {"type": "all_equal"},
    }
    result = LedgerEngine(config).process("L10B")
    assert any("ambiguous" in error for error in result.errors)
    assert not result.settlements


@pytest.mark.parametrize("separator", [" ", ""])
def test_original_demo_joined_into_one_line_matches_golden(separator):
    root = Path(__file__).parents[1] / "examples/demo"
    config = json.loads((root / "config.json").read_text())
    raw = json.loads((root / "raw.json").read_text())
    golden = json.loads((root / "golden.json").read_text())
    text = separator.join(record["raw_text"] for record in raw)
    result = LedgerEngine(config).process(text, {"tx_005": {"currency": "CAD"}})
    assert not result.errors
    assert len(result.transactions) == 6
    for field in ("paid", "share", "net"):
        assert getattr(result, field) == {key: Decimal(value) for key, value in golden[field].items()}
    assert result.total_expense == Decimal(golden["total_expense"])
    assert [(t.debtor_id, t.creditor_id, t.amount) for t in result.settlements] == [
        (t["from"], t["to"], Decimal(t["amount"])) for t in golden["settlements"]
    ]
