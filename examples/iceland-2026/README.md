# User-provided raw ledger

`raw.txt` preserves the supplied input. Participants confirmed: L, B, D, M.
There are 76 expenses: 11 advance bookings and 65 dated expenses.
The parser keeps repeated amounts as separate entries and records merchant/date metadata.

Base currency: CAD. `config.json` uses the displayed historical market close of
**1 ISK = 0.0113 CAD** on **2026-08-16**, the last date of the trip.
Source: [Exchange Rates UK historical table](https://www.exchangerates.org.uk/ISK-CAD-spot-exchange-rates-history-2026.html).
The source displays only four decimal places; the configured rate retains exactly
that published precision. This is not a historical card transaction rate.

The corrected user input explicitly sets ISK at Clippers Fitjar (`L4580ISKA`).
Transactions #12–#76 therefore use ISK. No transaction override is needed.

`output/ledger.json`, `output/report.txt` and `output/audit.md` reflect the corrected
input. The older `output/literal-ledger.json` is retained only as a historical
snapshot of the superseded input (before the ISK correction); do not use it for settlement.

Participants are supplied in `config.json`; the CLI does not prompt interactively.
The configuration also supplies base currency, FX rates and the default L/B split.

Reproduce the corrected interpretation:

```sh
.venv/bin/trip-dollar examples/iceland-2026/config.json examples/iceland-2026/raw.txt --raw-text --json
.venv/bin/python examples/iceland-2026/run_ledger.py
```

## Reference arithmetic

Paid: B = 14,602.61 CAD; L = 1,188.93 CAD + 572,172 ISK × 0.0113;
D = 155,408 ISK × 0.0113; M = 0. Total = 24,013.1940 CAD.
The L-only expenses are 1,370 + 200 ISK; the L/B-only expenses are 1,820 + 945 ISK.
Subtract these from total and divide the remainder by four for the common share
of 5,991.052125 CAD. Add half of the L/B expenses to L and B, and all of the L-only
expenses to L. Tests check these reference values and independently sum the raw input.
