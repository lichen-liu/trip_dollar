"""Reproduce the demo and verify hand-calculated expectations without rewriting them."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from trip_dollar import LedgerEngine
from trip_dollar.report import currency_segments, text_report

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text())


def number(value):
    return format(Decimal(value).normalize(), "f")


def amounts(mapping):
    return {key: number(value) for key, value in mapping.items()}


def verify(result, golden):
    """Compare every normalized row and aggregate to the independent fixture."""
    actual = {
        "status": result.status.value,
        "base_currency": result.base_currency,
        "total_expense": number(result.total_expense),
        "rows": [
            [tx.sequence, tx.id, tx.payer_id, number(tx.original_amount),
             tx.explicit_currency, tx.currency, tx.currency_source.value,
             tx.allocation.type, number(tx.base_amount), amounts(tx.shares), tx.status.value]
            for tx in result.transactions
        ],
        "paid": amounts(result.paid),
        "share": amounts(result.share),
        "net": amounts(result.net),
        "settlements": [
            {"from": t.debtor_id, "to": t.creditor_id, "amount": number(t.amount)}
            for t in result.settlements
        ],
        "currency_segments": currency_segments(result),
    }
    assert actual == golden, f"Golden mismatch:\n{json.dumps(actual, indent=2)}"
    assert not result.errors
    assert sum(result.paid.values()) == sum(result.share.values()) == Decimal("290")
    assert sum(result.net.values()) == 0
    residual = dict(result.net)
    for transfer in result.settlements:
        residual[transfer.debtor_id] += transfer.amount
        residual[transfer.creditor_id] -= transfer.amount
    assert all(value == 0 for value in residual.values())


def run():
    raw = load("raw.json")
    preserved = deepcopy(raw)
    engine = LedgerEngine(load("config.json"))
    result = engine.process(raw, load("overrides.json"))
    verify(result, load("golden.json"))
    assert raw == preserved
    assert [t.raw_text for t in result.transactions] == [r["raw_text"] for r in raw]
    assert [t.date for t in result.transactions] == [r["date"] for r in raw]

    # Same source before correction: prove both the override and its propagation.
    uncorrected = engine.process(raw)
    assert uncorrected.total_expense == Decimal("315")
    assert uncorrected.net == {"alice": Decimal("25"), "bob": Decimal("-50"), "charlie": Decimal("25")}

    # A separate failure case demonstrates that unknown currency blocks settlement.
    unresolved_raw = deepcopy(raw)
    unresolved_raw[0]["raw_text"] = "L60"
    unresolved = engine.process(unresolved_raw, load("overrides.json"))
    assert [t.currency for t in unresolved.transactions[:2]] == [None, None]
    assert unresolved.status.value == "AUDIT_REQUIRED"
    assert not unresolved.settlements

    return result, unresolved


def audit(result):
    lines = ["# Demo audit", "", "Amounts in the Base and Shares columns are CAD.", "",
             "Seq | Date | Description | Raw | Payer | Amount | Explicit | Resolved | Source | Allocation | Base | Shares | Status",
             "--- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: | --- | ---"]
    for tx in result.transactions:
        allocation = f"{tx.allocation.type}: {', '.join(tx.allocation.participants)}"
        shares = ", ".join(f"{p}={number(v)}" for p, v in tx.shares.items())
        lines.append(" | ".join(map(str, [tx.sequence, tx.date, tx.description, tx.raw_text,
            tx.payer_id, number(tx.original_amount), tx.explicit_currency or "—", tx.currency,
            tx.currency_source.value, allocation, number(tx.base_amount), shares, tx.status.value])))
    lines += ["", "## Final output", "", text_report(result), "",
              "## Checks", "", "- Golden rows, balances, segments and transfers: PASS",
              "- Paid = Share = 290 CAD; Net = 0: PASS",
              "- All balances are zero after transfers: PASS",
              "- Raw text and input order preserved: PASS",
              "- Without correction: total 315 CAD; nets Alice +25, Bob -50, Charlie +25.",
              "- Unknown initial currency: AUDIT_REQUIRED, zero settlement transfers."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    result, unresolved = run()
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    (output / "ledger.json").write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    (output / "unresolved.json").write_text(json.dumps(unresolved.to_dict(), indent=2) + "\n")
    (output / "audit.md").write_text(audit(result))
    print(text_report(result))
    print(f"\nGolden reference: PASS\nFailure case: PASS\nArtifacts: {output}")
