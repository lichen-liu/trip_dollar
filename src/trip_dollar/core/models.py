from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class CurrencySource(StrEnum):
    EXPLICIT = "explicit"
    INHERITED = "inherited"
    INITIAL_CONFIG = "initial_config"
    OVERRIDE = "override"
    UNKNOWN = "unknown"


class TransactionStatus(StrEnum):
    VALID = "VALID"
    UNRESOLVED_CURRENCY = "UNRESOLVED_CURRENCY"
    UNKNOWN_PARTICIPANT = "UNKNOWN_PARTICIPANT"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_ALLOCATION = "INVALID_ALLOCATION"
    OVERRIDDEN = "OVERRIDDEN"


class LedgerStatus(StrEnum):
    PARSING_INCOMPLETE = "PARSING_INCOMPLETE"
    AUDIT_REQUIRED = "AUDIT_REQUIRED"
    SETTLEMENT_READY = "SETTLEMENT_READY"


@dataclass(frozen=True)
class Participant:
    id: str
    code: str
    name: str | None = None
    active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or len(self.code) != 1 or not self.code.isascii() or not self.code.isalpha():
            raise ValueError("participant code must be exactly one letter (a-z or A-Z)")
        if self.code == "A":
            raise ValueError("participant code 'A' is reserved for all participants")


@dataclass(frozen=True)
class Allocation:
    type: str
    participants: tuple[str, ...]
    weights: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class LedgerConfig:
    base_currency: str
    participants: tuple[Participant, ...]
    fx_rates: dict[str, Decimal]
    default_allocation: Allocation
    initial_currency: str | None = None
    display_precision: int = 2
    tolerance: Decimal = Decimal("0.00000001")


@dataclass
class NormalizedTransaction:
    id: str
    sequence: int
    raw_text: str
    date: str | None = None
    description: str | None = None
    payer_id: str | None = None
    payer_code: str | None = None
    original_amount: Decimal | None = None
    explicit_currency: str | None = None
    currency: str | None = None
    currency_source: CurrencySource = CurrencySource.UNKNOWN
    allocation: Allocation | None = None
    base_currency: str | None = None
    base_amount: Decimal | None = None
    shares: dict[str, Decimal] = field(default_factory=dict)
    override_applied: bool = False
    notes: str | None = None
    status: TransactionStatus = TransactionStatus.VALID
    errors: list[str] = field(default_factory=list)
    source_text: str = ""
    source_title: str | None = None
    source_sequence: int | None = None
    source_span: tuple[int, int] | None = None


@dataclass(frozen=True)
class Transfer:
    debtor_id: str
    creditor_id: str
    amount: Decimal


@dataclass
class LedgerResult:
    status: LedgerStatus
    transactions: list[NormalizedTransaction]
    paid: dict[str, Decimal]
    share: dict[str, Decimal]
    net: dict[str, Decimal]
    settlements: list[Transfer]
    errors: list[str]
    base_currency: str
    display_precision: int

    @property
    def total_expense(self) -> Decimal:
        return sum(self.paid.values(), Decimal(0))

    def to_dict(self) -> dict[str, Any]:
        def amount(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "status": self.status.value,
            "base_currency": self.base_currency,
            "total_expense": amount(self.total_expense),
            "transactions": [
                {
                    "id": tx.id,
                    "sequence": tx.sequence,
                    "date": tx.date,
                    "description": tx.description,
                    "raw_text": tx.raw_text,
                    "source_text": tx.source_text,
                    "source_title": tx.source_title,
                    "source_sequence": tx.source_sequence,
                    "source_span": tx.source_span,
                    "payer_id": tx.payer_id,
                    "payer_code": tx.payer_code,
                    "original_amount": amount(tx.original_amount),
                    "explicit_currency": tx.explicit_currency,
                    "currency": tx.currency,
                    "currency_source": tx.currency_source.value,
                    "allocation": None if tx.allocation is None else {
                        "type": tx.allocation.type,
                        "participants": list(tx.allocation.participants),
                        "weights": [str(w) for w in tx.allocation.weights],
                    },
                    "base_amount": amount(tx.base_amount),
                    "shares": {k: amount(v) for k, v in tx.shares.items()},
                    "override_applied": tx.override_applied,
                    "status": tx.status.value,
                    "errors": tx.errors,
                }
                for tx in self.transactions
            ],
            "paid": {k: amount(v) for k, v in self.paid.items()},
            "share": {k: amount(v) for k, v in self.share.items()},
            "net": {k: amount(v) for k, v in self.net.items()},
            "settlements": [
                {"from": t.debtor_id, "to": t.creditor_id, "amount": amount(t.amount)}
                for t in self.settlements
            ],
            "errors": self.errors,
        }
