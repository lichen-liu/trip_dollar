from __future__ import annotations

from decimal import Decimal

from .models import LedgerResult


def _money(value: Decimal | None, precision: int) -> str:
    if value is None:
        return "—"
    quantum = Decimal(1).scaleb(-precision)
    return f"{value.quantize(quantum):,.{precision}f}"


def text_report(result: LedgerResult) -> str:
    lines = [f"Status: {result.status.value}", f"Base currency: {result.base_currency}", "", "Balances"]
    lines.append("Participant | Paid | Share | Net | Action")
    lines.append("--- | ---: | ---: | ---: | ---")
    for participant in result.paid:
        net = result.net[participant]
        action = "receive" if net > 0 else "pay" if net < 0 else "settled"
        lines.append(
            f"{participant} | {_money(result.paid[participant], result.display_precision)} | "
            f"{_money(result.share[participant], result.display_precision)} | "
            f"{_money(net, result.display_precision)} | {action}"
        )
    lines.extend(["", "Settlements"])
    if result.settlements:
        lines.extend(
            f"{transfer.debtor_id} -> {transfer.creditor_id}: "
            f"{_money(transfer.amount, result.display_precision)} {result.base_currency}"
            for transfer in result.settlements
        )
    else:
        lines.append("None (or settlement blocked pending audit).")
    if result.errors:
        lines.extend(["", "Errors", *(f"- {error}" for error in result.errors)])
    lines.extend(["", "Currency segments", *currency_segments(result)])
    return "\n".join(lines)


def currency_segments(result: LedgerResult) -> list[str]:
    if not result.transactions:
        return ["None"]
    segments: list[str] = []
    start = 1
    currency = result.transactions[0].currency or "UNKNOWN"
    for index, tx in enumerate(result.transactions[1:], start=2):
        next_currency = tx.currency or "UNKNOWN"
        if next_currency != currency:
            segments.append(_segment(start, index - 1, currency))
            start, currency = index, next_currency
    segments.append(_segment(start, len(result.transactions), currency))
    return segments


def _segment(start: int, end: int, currency: str) -> str:
    label = f"#{start}" if start == end else f"#{start}-#{end}"
    return f"{label}: {currency}"
