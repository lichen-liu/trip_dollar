# Trip Dollar

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
pytest suite on every push and pull request using Python 3.13 on Ubuntu.
It installs the project with `python -m pip install -e '.[dev]'`
and runs `python -m pytest -q`. A test failure fails the job.
Manual runs are also available once the workflow is on the default branch.

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
on the source entry are copied to each expense. Arbitrary prose or date headings
inside raw text are not automatically interpreted.

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
