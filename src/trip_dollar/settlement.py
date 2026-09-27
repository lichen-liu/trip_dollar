from __future__ import annotations

from decimal import Decimal

from .models import Transfer


def settle(net: dict[str, Decimal], tolerance: Decimal) -> list[Transfer]:
    """Produce deterministic greedy transfers from normalized base-currency nets."""
    debtors = [[participant, -amount] for participant, amount in sorted(net.items()) if amount < -tolerance]
    creditors = [[participant, amount] for participant, amount in sorted(net.items()) if amount > tolerance]
    transfers: list[Transfer] = []
    debtor_index = creditor_index = 0
    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor, owed = debtors[debtor_index]
        creditor, due = creditors[creditor_index]
        amount = min(owed, due)
        transfers.append(Transfer(str(debtor), str(creditor), amount))
        debtors[debtor_index][1] -= amount
        creditors[creditor_index][1] -= amount
        if debtors[debtor_index][1] <= tolerance:
            debtor_index += 1
        if creditors[creditor_index][1] <= tolerance:
            creditor_index += 1
    return transfers
