from decimal import Decimal

import pytest

from trip_dollar.config import ConfigurationError, load_config
from trip_dollar.engine import LedgerEngine
from trip_dollar.models import CurrencySource, LedgerStatus


def config(participants=None, initial_currency=None):
    participants = participants or [
        {"id": "left", "code": "L"},
        {"id": "right", "code": "B"},
        {"id": "extra", "code": "X"},
    ]
    value = {
        "base_currency": "CAD",
        "participants": participants,
        "fx_rates": {"CAD": 1, "USD": "2"},
        "default_allocation": {"type": "equal_split", "participants": [participants[0]["code"], participants[1]["code"]]},
    }
    if initial_currency:
        value["initial_currency"] = initial_currency
    return value


def test_currency_carry_forward():
    result = LedgerEngine(config()).process([
        {"raw_text": "L100 CAD"}, {"raw_text": "L200"},
        {"raw_text": "L300 USD"}, {"raw_text": "L400"},
    ])
    assert [tx.currency for tx in result.transactions] == ["CAD", "CAD", "USD", "USD"]
    assert [tx.currency_source for tx in result.transactions] == [
        CurrencySource.EXPLICIT, CurrencySource.INHERITED,
        CurrencySource.EXPLICIT, CurrencySource.INHERITED,
    ]


def test_unknown_initial_currency_blocks_settlement():
    result = LedgerEngine(config()).process([
        {"raw_text": "L100"}, {"raw_text": "L200"},
        {"raw_text": "L300 CAD"}, {"raw_text": "L400"},
    ])
    assert [tx.currency for tx in result.transactions] == [None, None, "CAD", "CAD"]
    assert result.status == LedgerStatus.AUDIT_REQUIRED
    assert result.settlements == []


def test_all_means_every_active_configured_participant():
    result = LedgerEngine(config()).process([{"raw_text": "L90A CAD"}])
    assert result.transactions[0].shares == {"left": Decimal(30), "right": Decimal(30), "extra": Decimal(30)}


def test_default_allocation_is_independent_from_payer():
    result = LedgerEngine(config()).process([{"raw_text": "X100 CAD"}])
    assert result.paid == {"left": 0, "right": 0, "extra": Decimal(100)}
    assert result.share == {"left": Decimal(50), "right": Decimal(50), "extra": 0}


def test_specific_allocation_is_independent_from_payer():
    result = LedgerEngine(config()).process([{"raw_text": "L100X CAD"}])
    assert result.paid["left"] == 100
    assert result.share["extra"] == 100


def test_raw_order_not_calendar_order_controls_currency():
    result = LedgerEngine(config()).process([
        {"date": "0902", "raw_text": "L10 CAD"},
        {"date": "0101", "raw_text": "B10"},
        {"date": "0903", "raw_text": "X10 USD"},
    ])
    assert [tx.currency for tx in result.transactions] == ["CAD", "CAD", "USD"]


def test_conservation_and_settlement():
    result = LedgerEngine(config()).process([
        {"raw_text": "L90A CAD"}, {"raw_text": "B30X CAD"},
    ])
    assert sum(result.paid.values()) == sum(result.share.values())
    assert sum(result.net.values()) == 0
    assert result.status == LedgerStatus.SETTLEMENT_READY
    assert sum(t.amount for t in result.settlements) == Decimal(60)


def test_override_currency_updates_following_state():
    result = LedgerEngine(config()).process(
        [{"id": "one", "raw_text": "L10"}, {"raw_text": "B20"}],
        {"one": {"currency": "USD"}},
    )
    assert [tx.currency for tx in result.transactions] == ["USD", "USD"]
    assert result.transactions[0].currency_source == CurrencySource.OVERRIDE


def test_override_can_repair_invalid_raw_fields():
    result = LedgerEngine(config()).process(
        [{"id": "broken", "payer_code": "NOPE", "amount": "bad", "allocation": "NOPE"}],
        {"broken": {"payer_code": "L", "amount": "25", "allocation": {"type": "single", "participant": "B"}, "currency": "CAD"}},
    )
    assert result.errors == []
    assert result.transactions[0].status.value == "OVERRIDDEN"
    assert result.paid["left"] == 25
    assert result.share["right"] == 25


def test_initial_currency_source_is_auditable():
    result = LedgerEngine(config(initial_currency="CAD")).process([{"raw_text": "L10"}, {"raw_text": "B10"}])
    assert [tx.currency_source for tx in result.transactions] == [
        CurrencySource.INITIAL_CONFIG, CurrencySource.INITIAL_CONFIG
    ]


def test_explicit_unsupported_currency_is_an_audit_error():
    result = LedgerEngine(config()).process([{"raw_text": "L10 EUR"}])
    assert result.transactions[0].currency == "EUR"
    assert "no FX rate configured" in result.errors[0]


def test_reserved_all_code_rejected():
    with pytest.raises(ConfigurationError):
        load_config(config(participants=[{"id": "one", "code": "A"}, {"id": "two", "code": "B"}]))


@pytest.mark.parametrize("code", ["A1", "B1", "12", "1", 1, "AB", "", "!", " L", "é"])
def test_participant_codes_must_be_single_letters(code):
    with pytest.raises(ConfigurationError, match="exactly one letter"):
        load_config(config(participants=[{"id": "one", "code": code}, {"id": "two", "code": "B"}]))


def test_digits_after_payer_are_entirely_the_amount():
    result = LedgerEngine(config()).process([{"raw_text": "L160 CAD"}])
    assert result.transactions[0].payer_code == "L"
    assert result.transactions[0].original_amount == Decimal("160")
    assert result.transactions[0].base_amount == Decimal("160")


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity", "bad", "-1"])
def test_invalid_override_amount_returns_audit_error(amount):
    result = LedgerEngine(config()).process(
        [{"id": "one", "raw_text": "L10 CAD"}],
        {"one": {"amount": amount}},
    )
    assert result.status == LedgerStatus.AUDIT_REQUIRED
    assert result.errors
    assert result.transactions[0].base_amount is None
    assert result.settlements == []


def test_unmatched_override_blocks_settlement():
    result = LedgerEngine(config()).process(
        [{"id": "one", "raw_text": "L10 CAD"}],
        {"typo": {"currency": "USD"}},
    )
    assert result.status == LedgerStatus.AUDIT_REQUIRED
    assert result.errors == ["override references unknown transaction id: typo"]
    assert result.settlements == []


def test_unmatched_override_on_empty_ledger_blocks_settlement():
    result = LedgerEngine(config()).process([], {"missing": {"amount": "10"}})
    assert result.status == LedgerStatus.AUDIT_REQUIRED
    assert result.errors and not result.settlements
