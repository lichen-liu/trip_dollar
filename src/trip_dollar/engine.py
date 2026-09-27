from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from typing import Any, Iterable

from .config import load_config
from .models import (
    CurrencySource,
    LedgerConfig,
    LedgerResult,
    LedgerStatus,
    NormalizedTransaction,
    TransactionStatus,
)
from .parser import allocation_from_override, expand_records, parse_fields, resolve_allocation
from .settlement import settle


class LedgerEngine:
    def __init__(self, config: LedgerConfig | dict[str, Any]):
        self.config = load_config(config) if isinstance(config, dict) else config
        self.by_code = {p.code: p for p in self.config.participants}

    def process(
        self,
        raw_transactions: str | Iterable[dict[str, Any] | str],
        overrides: dict[str, dict[str, Any]] | None = None,
    ) -> LedgerResult:
        source = raw_transactions if isinstance(raw_transactions, str) else list(raw_transactions)
        raw_items = list(expand_records(deepcopy(source), self.config))
        override_map = overrides or {}
        transactions: list[NormalizedTransaction] = []
        current_currency = self.config.initial_currency
        currency_came_from_initial_config = current_currency is not None
        seen_ids: set[str] = set()

        with localcontext() as context:
            context.prec = 34
            for sequence, raw in enumerate(raw_items, start=1):
                tx_id = str(raw.get("id", f"tx_{sequence:03d}"))
                tx = NormalizedTransaction(
                    id=tx_id,
                    sequence=sequence,
                    raw_text=str(raw.get("raw_text", "")),
                    date=None if raw.get("date") is None else str(raw["date"]),
                    description=raw.get("description"),
                    notes=raw.get("notes"),
                    base_currency=self.config.base_currency,
                    source_text=raw.get("source_text", raw.get("raw_text", "")),
                    source_sequence=raw.get("source_sequence", sequence),
                    source_span=raw.get("source_span"),
                )
                if tx_id in seen_ids:
                    self._fail(tx, TransactionStatus.INVALID_AMOUNT, f"duplicate transaction id: {tx_id}")
                    transactions.append(tx)
                    continue
                seen_ids.add(tx_id)

                parsed = parse_fields(raw, self.config)
                tx.payer_code = parsed.payer_code
                tx.original_amount = parsed.amount
                tx.explicit_currency = parsed.explicit_currency
                if parsed.payer_code in self.by_code:
                    tx.payer_id = self.by_code[parsed.payer_code].id
                tx.allocation = resolve_allocation(parsed.allocation_token, self.config)

                override = override_map.get(tx_id)
                if override:
                    tx.override_applied = True
                    if "payer_code" in override:
                        code = str(override["payer_code"])
                        tx.payer_code = code
                        tx.payer_id = self.by_code[code].id if code in self.by_code else None
                    if "amount" in override:
                        try:
                            tx.original_amount = Decimal(str(override["amount"]))
                            if not tx.original_amount.is_finite() or tx.original_amount < 0:
                                raise ValueError
                        except Exception:
                            self._fail(tx, TransactionStatus.INVALID_AMOUNT, "invalid override amount")
                    if "allocation" in override:
                        tx.allocation = allocation_from_override(override["allocation"], self.config)

                # Validate effective values after overrides so a correction can
                # repair malformed raw data without mutating the source record.
                if tx.payer_id is None:
                    self._fail(tx, TransactionStatus.UNKNOWN_PARTICIPANT, f"unknown payer: {tx.payer_code or ''}")
                if tx.original_amount is None:
                    self._fail(tx, TransactionStatus.INVALID_AMOUNT, parsed.error or "invalid amount")
                elif not tx.original_amount.is_finite():
                    self._fail(tx, TransactionStatus.INVALID_AMOUNT, "amount must be finite")
                elif tx.original_amount < 0:
                    self._fail(tx, TransactionStatus.INVALID_AMOUNT, "invalid negative amount")
                if tx.allocation is None:
                    self._fail(tx, TransactionStatus.INVALID_ALLOCATION, "invalid allocation")

                override_currency = None if not override else override.get("currency")
                if override_currency is not None:
                    tx.currency = str(override_currency).upper()
                    tx.currency_source = CurrencySource.OVERRIDE
                    if override.get("currency_state_mode", "update") == "update":
                        current_currency = tx.currency
                        currency_came_from_initial_config = False
                elif parsed.explicit_currency is not None:
                    tx.currency = parsed.explicit_currency
                    tx.currency_source = CurrencySource.EXPLICIT
                    current_currency = tx.currency
                    currency_came_from_initial_config = False
                elif current_currency is not None:
                    tx.currency = current_currency
                    tx.currency_source = (
                        CurrencySource.INITIAL_CONFIG
                        if currency_came_from_initial_config
                        else CurrencySource.INHERITED
                    )
                else:
                    tx.currency_source = CurrencySource.UNKNOWN

                if tx.currency is None:
                    self._fail(tx, TransactionStatus.UNRESOLVED_CURRENCY, "currency is unresolved")
                elif tx.currency not in self.config.fx_rates:
                    self._fail(tx, TransactionStatus.UNRESOLVED_CURRENCY, f"no FX rate configured for {tx.currency}")

                if not tx.errors and tx.original_amount is not None and tx.allocation is not None:
                    tx.base_amount = tx.original_amount * self.config.fx_rates[tx.currency]  # type: ignore[index]
                    total_weight = sum(tx.allocation.weights, Decimal(0))
                    tx.shares = {
                        participant: tx.base_amount * weight / total_weight
                        for participant, weight in zip(tx.allocation.participants, tx.allocation.weights)
                    }
                    tx.status = TransactionStatus.OVERRIDDEN if tx.override_applied else TransactionStatus.VALID
                transactions.append(tx)

        unmatched = [
            f"override references unknown transaction id: {tx_id}"
            for tx_id in override_map if tx_id not in seen_ids
        ]
        return self._aggregate(transactions, unmatched)

    @staticmethod
    def _fail(tx: NormalizedTransaction, status: TransactionStatus, message: str) -> None:
        tx.status = status
        tx.errors.append(message)

    def _aggregate(
        self, transactions: list[NormalizedTransaction], override_errors: list[str] | None = None
    ) -> LedgerResult:
        ids = [p.id for p in self.config.participants]
        paid = {participant: Decimal(0) for participant in ids}
        share = {participant: Decimal(0) for participant in ids}
        errors: list[str] = list(override_errors or [])
        for tx in transactions:
            if tx.errors or tx.base_amount is None or tx.payer_id is None:
                errors.extend(f"{tx.id}: {error}" for error in tx.errors)
                continue
            paid[tx.payer_id] += tx.base_amount
            for participant, amount in tx.shares.items():
                share[participant] += amount

        net = {participant: paid[participant] - share[participant] for participant in ids}
        total_paid = sum(paid.values(), Decimal(0))
        total_share = sum(share.values(), Decimal(0))
        total_net = sum(net.values(), Decimal(0))
        if abs(total_paid - total_share) > self.config.tolerance:
            errors.append("invariant failed: total paid does not equal total share")
        if abs(total_net) > self.config.tolerance:
            errors.append("invariant failed: net balances do not sum to zero")

        status = LedgerStatus.AUDIT_REQUIRED if errors else LedgerStatus.SETTLEMENT_READY
        settlements = [] if errors else settle(net, self.config.tolerance)
        return LedgerResult(
            status=status,
            transactions=transactions,
            paid=paid,
            share=share,
            net=net,
            settlements=settlements,
            errors=errors,
            base_currency=self.config.base_currency,
            display_precision=self.config.display_precision,
        )
