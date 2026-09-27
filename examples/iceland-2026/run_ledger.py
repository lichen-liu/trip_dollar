"""Reproduce the corrected ledger and its audit artifacts."""
import json
from pathlib import Path

from trip_dollar import LedgerEngine
from trip_dollar.report import text_report


def main():
    root = Path(__file__).resolve().parent
    result = LedgerEngine(json.loads((root / "config.json").read_text())).process(
        (root / "raw.txt").read_text()
    )
    if result.errors:
        raise ValueError(result.errors)
    output = root / "output"
    output.mkdir(exist_ok=True)
    (output / "ledger.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n")
    report = text_report(result)
    (output / "report.txt").write_text(report + f"\n\nTotal expense: {result.total_expense} CAD\n")
    rows = ["# Corrected-input audit", "", "Clippers Fitjar explicitly establishes ISK at transaction #12. No override is needed.",
            "", "Sequence | Date | Merchant | Raw | Currency | Source | Base CAD",
            "--- | --- | --- | --- | --- | --- | ---:"]
    rows += [" | ".join(map(str, [t.sequence, t.date or "—", t.description, t.raw_text,
                                t.currency, t.currency_source.value, t.base_amount]))
             for t in result.transactions]
    rows += ["", "## Totals and settlement", "", report,
             f"\nExact total: {result.total_expense} CAD",
             "\nBalances and transfers are rounded independently for display; exact Decimal values are in ledger.json."]
    (output / "audit.md").write_text("\n".join(rows) + "\n")
    print(report)
    print(f"\nTotal expense: {result.total_expense:.2f} CAD")


if __name__ == "__main__":
    main()
