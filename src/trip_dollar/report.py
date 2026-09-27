from __future__ import annotations

from decimal import Decimal

from .models import LedgerResult


def _money(value: Decimal | None, precision: int) -> str:
    if value is None:
        return "—"
    quantum = Decimal(1).scaleb(-precision)
    return f"{value.quantize(quantum):,.{precision}f}"


def text_report(result: LedgerResult, names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = ["Expenses need review" if result.errors else "Trip expenses",
             f"{len(result.transactions)} records · {result.base_currency}", ""]
    width = max(11, *(len(names.get(p, p)) for p in result.paid))
    lines.append(f"{'Participant':<{width}}  {'Paid':>14}  {'Share':>14}  {'Net':>14}")
    for participant in result.paid:
        net = result.net[participant]
        lines.append(
            f"{names.get(participant, participant):<{width}}  {_money(result.paid[participant], result.display_precision):>14}  "
            f"{_money(result.share[participant], result.display_precision):>14}  "
            f"{_money(net, result.display_precision):>14}"
        )
    lines.extend(["", "Positive net: receives money. Negative net: pays money.", "", "Payments"])
    if result.settlements:
        lines.extend(
            f"{names.get(transfer.debtor_id, transfer.debtor_id)} -> {names.get(transfer.creditor_id, transfer.creditor_id)}: "
            f"{_money(transfer.amount, result.display_precision)} {result.base_currency}"
            for transfer in result.settlements
        )
    else:
        lines.append("Blocked: fix the errors below." if result.errors else "No payments needed.")
    if result.errors:
        lines.extend(["", "Errors", *(f"- {error}" for error in result.errors)])
    lines.extend(["", "Currency segments", *currency_segments(result)])
    if not result.errors:
        lines.extend(["", f"Total expense: {_money(result.total_expense, result.display_precision)} {result.base_currency}",
                      "Accounting checks passed: paid equals shares; net balances sum to zero within tolerance.",
                      "Amounts are rounded for display; exact values are available in JSON."])
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
