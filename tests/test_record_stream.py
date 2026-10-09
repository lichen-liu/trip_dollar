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


@pytest.mark.parametrize("text", ["C500CADLC", "C500LC CAD", "B1CAD C500LC"])
def test_multi_person_suffix_splits_equally_using_stable_ids(text):
    result = engine().process(text)
    assert not result.errors
    tx = result.transactions[-1]
    assert tx.payer_id == "charlie"
    assert tx.allocation.type == "equal_split"
    assert tx.allocation.participants == ("left", "charlie")
    assert tx.shares == {"left": Decimal(250), "charlie": Decimal(250)}
    assert sum(result.paid.values()) == sum(result.share.values())
    assert sum(result.net.values()) == 0


@pytest.mark.parametrize("separator", ["", " ", ";"])
def test_multi_person_suffix_does_not_swallow_the_next_payer(separator):
    text = separator.join(["C60CADLB", "L40BC", "B20LC"])
    result = engine().process(text)
    assert not result.errors
    assert [tx.raw_text for tx in result.transactions] == ["C60CADLB", "L40BC", "B20LC"]
    assert [tx.payer_code for tx in result.transactions] == ["C", "L", "B"]
    assert [tx.shares for tx in result.transactions] == [
        {"left": Decimal(30), "bob": Decimal(30)},
        {"bob": Decimal(20), "charlie": Decimal(20)},
        {"left": Decimal(10), "charlie": Decimal(10)},
    ]
    for tx in result.transactions:
        start, end = tx.source_span
        assert text[start:end] == tx.raw_text


@pytest.mark.parametrize("token", ["LL", "LCL", "LA", "AL", "LZ"])
def test_invalid_multi_person_suffix_blocks_settlement(token):
    result = engine().process(f"C100CAD{token}")
    assert result.errors
    assert not result.settlements


def test_structured_multi_person_allocation_and_three_way_decimal_conservation():
    result = engine().process([{"payer_code": "C", "amount": "100", "currency": "CAD", "allocation": "LBC"}])
    assert not result.errors
    tx = result.transactions[0]
    assert tx.allocation.type == "equal_split"
    assert set(tx.shares) == {"left", "bob", "charlie"}
    assert all(abs(value - Decimal(100) / 3) < engine().config.tolerance for value in tx.shares.values())
    assert abs(sum(result.net.values())) < engine().config.tolerance


def test_multi_person_currency_collision_is_rejected():
    ledger = engine()
    config = {
        "base_currency": "CAD",
        "participants": [{"id": p.id, "code": p.code} for p in ledger.config.participants],
        "fx_rates": {"CAD": 1, "LC": 2},
        "initial_currency": "CAD",
        "default_allocation": {"type": "all_equal"},
    }
    result = LedgerEngine(config).process("B100LC")
    assert any("ambiguous" in error for error in result.errors)
    assert not result.settlements


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
