# Trip Dollar

## Run from a raw expense file

Supply the people, final currency and default split on the command line:

```bash
trip-dollar examples/iceland-2026/raw.txt --participants L B D M --base CAD --split L B
```

Missing exchange rates are fetched automatically. For this file, the trip end date
is inferred as 2026-08-16. Online rates may differ from the rate saved in the example.
To reproduce its exact result without network access:

```bash
trip-dollar examples/iceland-2026/raw.txt --participants L B D M --base CAD --split L B --fx ISK=0.0113CAD --offline
```

Use `"L=Li Chen"` instead of `L` to show a name. Codes remain single letters;
`A` is reserved for everyone. Include people who never paid. `--split L B` applies
only to expenses without an allocation suffix; it does not depend on who paid.

### Exchange rates

The left side of an equation is always one unit. Both directions work:

```bash
--fx CAD=88.6ISK
--fx ISK=0.0113CAD USD=1.37CAD EUR=1.50CAD
```

The first equation means 1 CAD buys 88.6 ISK, so ISK amounts are divided by 88.6.
The second form multiplies ISK by 0.0113. These two sample rates are not exactly
equal; use one equation per currency pair. Every pair must include `--base`.
Rates must be positive and finite; conflicting equations are rejected.
Unspecified pairs are fetched from Frankfurter's ECB reference-rate feed.
Unsupported currencies or failed lookups stop the calculation; provide `--fx` to
use another source. The request contains currency codes and dates, never expenses
or participant names.

`--fx-date YYYY-MM-DD` selects a date explicitly. Otherwise, a `YYYY.Month.Title`
heading and `MMDD` date headings must establish the trip end date unambiguously.
The latest calendar date is used without reordering expenses. A weekend or holiday
uses the most recent published observation within the preceding seven days. The
report and JSON audit show the actual observation date. Future dates are rejected
for online lookup. The provider API is documented at https://frankfurter.dev/.

Online responses are saved in `.trip-dollar/fx-cache.json` and reused for the same
pair and date. Use `--fx-cache PATH` for a separate cache. `--offline` requires all
rates explicitly; it never contacts the provider. Explicit equations always win.

### Errors and audit

If the first expense has no currency, supply `--initial-currency ISK` or correct
the raw record. Without either, the command fails with the source line and exits
with code 2. Later labels do not backfill earlier records, and online FX never
determines a transaction's currency.

Use `--audit audit.json` to save the exact ledger, resolved configuration, input
hash, overrides, and FX provenance. `--json` prints that same data to stdout.
Text errors go to stderr; no payment instructions are printed on failure.
Display amounts default to two decimal places (`--precision` changes this).
Rounding is for display only, so displayed amounts can differ by a cent when added;
the audit retains exact Decimal balances and transfers.

The older `trip-dollar config.json transactions.json` interface still works.
Use its `--raw-text` flag when the second file contains raw text.

## Real ledger example

The [August 2026 Iceland example](examples/iceland-2026/README.md) contains a
76-expense raw document, configuration for L/B/D/M, reproducible parsed output,
an audit report, and reference arithmetic checked by `tests/test_document_import.py`.
It exercises merchant labels, date headings, multiple expenses per line,
currency carry-forward, and both all-participant and individual allocations.

```bash
.venv/bin/python examples/iceland-2026/run_ledger.py
.venv/bin/python -m pytest tests/test_document_import.py -q
```

Trip Dollar is a configuration-driven Python expense ledger. It preserves the ordered raw stream, resolves stateful currency context, separates payer from economic allocation, converts to a configured base currency, verifies conservation, and only then produces settlement transfers.

Nothing in the engine knows a trip location, a person's name, a fixed participant count, or a special real-world currency.

## Install and run

Python 3.11 or newer is required.

```bash
python -m pip install -e '.[dev]'
pytest
trip-dollar examples/ledger.json examples/transactions.json
trip-dollar examples/ledger.json examples/transactions.json --json
```

You can also run without installing:

```bash
PYTHONPATH=src python -m trip_dollar.cli examples/ledger.json examples/transactions.json
```

The command exits with status `2` when an audit issue blocks settlement.

## Continuous integration

The GitHub Actions workflow in `.github/workflows/pytest.yml` runs the complete
pytest suite only for pull requests targeting `main`, using Python 3.13 on Ubuntu.
It installs the project with `python -m pip install -e '.[dev]'`
and runs `python -m pytest -q`. A test failure fails the job.
Pushes without a matching pull request and manual dispatch do not trigger CI.

## Configuration

```json
{
  "base_currency": "CAD",
  "participants": [
    {"id": "person-1", "code": "L", "name": "Optional display name"},
    {"id": "person-2", "code": "B"}
  ],
  "fx_rates": {
    "CAD": 1,
    "USD": {"base_per_unit": "1.37"}
  },
  "initial_currency": "CAD",
  "default_allocation": {
    "type": "equal_split",
    "participants": ["L", "B"]
  }
}
```

Rates mean `base amount = original amount × base_per_unit`. Decimal values should be strings when exact decimal spelling matters. Omit `initial_currency` to leave leading currency-less transactions unresolved.

Participant code `A` is forbidden because the compact language reserves it for “all active participants.” The default allocation is entirely configured; `L` and `B` have no meaning to the engine.

## Ordered transaction input

The transactions file is a JSON array. Array order is the source sequence and is never replaced by date order.

Existing multi-record lines are supported, without requiring separators:

```text
L60 CAD C90A B24C USD
L60CADC90AB24CUSD
```

Both examples contain three expenses (when L, B, C and the currencies are configured).
The parser uses registered participant letters, amounts, allocation suffixes and
currency names to recognize complete records. Currency context follows left-to-right
order, including across lines. Spaces, newlines, semicolons and commas between
records are accepted. Ambiguous or unrecognized input blocks settlement.

Pass text directly to `LedgerEngine(config).process(text)`, put a multi-record line
in a JSON entry's `raw_text`, or use the CLI with an existing text file:

```bash
trip-dollar config.json records.txt --raw-text --json
```

Each parsed transaction preserves its source text, source sequence and zero-based
character span (end exclusive), alongside the extracted raw record. A supplied
entry ID such as `line` becomes `line:1`, `line:2`, etc. when it contains multiple
expenses; use these IDs for individual overrides. Date and description metadata
on the source entry are copied to each expense.

Text documents also support an optional `YYYY.Month.Title` heading, four-digit
`MMDD` date headings, and `Merchant: records` lines. These headings supply metadata;
they never change currency. Records before the first date heading retain an unknown
date. Both `L100ISKA` (currency then allocation) and `L100A ISK` are accepted.
Original source lines and character spans are preserved. Unknown nonempty lines
remain validation errors rather than being silently skipped.

Compact input uses:

```text
<payer-code><amount><optional allocation suffix> [optional currency]
```

Examples:

- `X100A USD`: X paid 100 USD; all active participants share it equally.
- `X100Y`: X paid; configured participant Y bears the whole expense.
- `X100`: X paid; the configured default allocation applies.

Participant codes must be exactly one ASCII letter, are case-sensitive, and are matched against configuration. Digits and multi-character codes are rejected; `A` is reserved for all participants. For complex integrations, structured fields are also supported:

```json
{
  "id": "tx_42",
  "date": "0815",
  "description": "Dinner",
  "raw_text": "source material retained verbatim",
  "payer_code": "X",
  "amount": "100.25",
  "allocation": "A",
  "currency": "USD"
}
```

## Overrides

Pass an override object using `--overrides overrides.json`:

```json
{
  "tx_42": {
    "currency": "CAD",
    "allocation": {
      "type": "equal_split",
      "participants": ["L", "B"]
    }
  }
}
```

A currency override updates carry-forward state by default. Set `"currency_state_mode": "transaction_only"` when it applies only to that transaction. Overrides may also replace `payer_code` or `amount`; raw input remains unchanged.

## Accounting behavior

- Explicit currency changes the state; missing currency inherits current state.
- An override has higher authority than raw fields and normally changes subsequent state.
- Unresolved currency or invalid input blocks settlement instead of guessing.
- Amounts and FX calculations use `Decimal` with 34-digit working precision.
- Rounding happens only in the text presentation layer.
- `net = paid - share`: positive means receive, negative means pay.
- Settlement is a deterministic greedy pass over participant IDs.

The JSON output contains normalized transactions, currency sources, shares, balances, errors, and transfers. The text report also shows compact currency segments for audit.
