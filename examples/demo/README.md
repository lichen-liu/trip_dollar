# Reproducible end-to-end demo

From the project root:

```sh
.venv/bin/python examples/demo/run_demo.py
.venv/bin/python -m pytest -q
```

The runner reads `config.json`, ordered `raw.json`, and separate `overrides.json`.
It compares the calculated ledger with `golden.json`, then writes `output/ledger.json`,
`output/audit.md`, and an intentionally blocked `output/unresolved.json`.
The golden file is a hand-calculated regression reference; the runner never rewrites it
and the engine never reads it.

## Independent arithmetic

Illustrative fixed FX assumptions: CAD = 1, USD = 1.25 CAD, EUR = 1.50 CAD.
These are demo data, not current market rates. Alice=L, Bob=B, Charlie=C.
The default split is Alice/Bob equally; A means all three equally.

| Seq | Raw record | Base calculation | Alice share | Bob share | Charlie share |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | L60 CAD | 60 × 1 = 60 | 30 | 30 | 0 |
| 2 | C90A | 90 × 1 = 90 | 30 | 30 | 30 |
| 3 | B24C USD | 24 × 1.25 = 30 | 0 | 0 | 30 |
| 4 | L48A | 48 × 1.25 = 60 | 20 | 20 | 20 |
| 5 | C20B EUR | override CAD: 20 × 1 = 20 | 0 | 20 | 0 |
| 6 | B30A | inherits corrected CAD: 30 × 1 = 30 | 10 | 10 | 10 |
| Total | | 290 | 90 | 110 | 90 |

Alice paid 60 + 60 = 120; Bob paid 30 + 30 = 60; Charlie paid 90 + 20 = 110.
Net = paid − share: Alice +30, Bob −50, Charlie +20.
Settlement: Bob pays Alice 30 CAD and Charlie 20 CAD.

Record #4 has an earlier date but inherits USD from #3 because raw order controls
currency. Record #5 retains explicit EUR in the audit even though its override wins;
#6 inherits the corrected CAD context.

Without the override, #5 becomes 30 CAD and #6 becomes 45 CAD: total 315 CAD,
shares 95/125/95, paid 120/75/120, nets +25/−50/+25.

The failure demo removes CAD from the first record. #1 and #2 become unresolved,
and settlement must be blocked. Any aggregate in that error output is partial.

Golden `rows` columns are: sequence, ID, payer ID, original amount, explicit currency,
resolved currency, currency source, allocation type, base amount, shares, status.
Decimal values are compared numerically after canonical string formatting.

This demo covers the illustrated paths; it is not proof of correctness for every
possible input. The broader test suite provides additional regression checks.
