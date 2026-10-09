# Trip Dollar

Calculate shared trip expenses and who owes whom.

## Install

Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Run

### Web app

```bash
pip install -e '.[web]'
trip-dollar-web
```

Open <http://127.0.0.1:8000>. Paste notes or upload a UTF-8 `.txt` file, add each
participant's letter code and optional name, tick the default split, and choose
the currency to settle in. Exchange rates are automatic; open **Currency &
exchange rates** only to supply your own rates, a rate date, or a starting currency.
**Try an example** fills in a complete offline demo.

Payments come first. The tabs below show everyone's balances, each parsed
expense with its original source line and exact shares, and the exchange-rate
sources and currency carry-forward segments. Download the audit for exact
decimal values, the original notes, settings, and a source fingerprint.
Changing an input marks results out of date and disables copying/downloading
until you calculate again. Invalid records produce an error, never a payment plan.

The Python server uses the same parser, FX resolver, and accounting engine as
the CLI. Notes are held only for the request and browser session, not saved on
the server; only the exchange-rate cache is written. Uploads are limited to 64 KB.
There are no third-party scripts, analytics, or frontend build tools.

The web interface is called **Trip Split**, with **split.boboji.fyi** as its
planned deployment address. The Python package and CLI remain `trip-dollar`.
It is part of **boboji.fyi** and follows AeroRepo's visual
language: its `b/` identity, charcoal/lime palette, square controls, and locally
hosted Manrope and DM Mono fonts. The fonts are reused unchanged from AeroRepo;
their copyright notices and SIL Open Font Licenses are included in
`src/trip_dollar/frontend/static/fonts/` and shipped with the Python package.

This command runs in the foreground, binds only to loopback, and stops with
Ctrl+C. `--port 8080` changes the port. Nothing installs a macOS background
service or exposes the app to the network. Deployment on the Mac is a separate
step; remote access will need an explicit access/security setup.

### Command line

```bash
trip-dollar trip.txt --participants L B D M --base CAD --split L B
```

That's the whole command. Missing exchange rates are looked up automatically.

- `trip.txt`: your raw expense notes.
- `--participants`: everyone on the trip, including people who paid nothing.
  Use `"L=Li Chen"` to display a name. Codes must be single letters; `A` is reserved.
- `--base`: currency for the final balances and payments.
- `--split`: who shares expenses without an allocation suffix.

### Example input

```text
2026.Aug.Trip
0808
Lunch: L100CADA
Coffee: B12
Tickets: D4000ISKA L1000M
```

Here, lunch is shared by everyone. Coffee uses the L/B split and inherits CAD.
Tickets switches to ISK: D pays 4,000 for everyone; L pays 1,000 for M.
Multiple records work with spaces or directly adjacent. Input order controls
currency inheritance; date headings do not change currency.

## Only when needed

| Option | Use |
| --- | --- |
| `--fx CAD=88.6ISK USD=1.37CAD` | Supply your own rates; missing pairs are still fetched. |
| `--fx-date 2026-08-16` | Choose a historical rate date when it cannot be inferred. |
| `--initial-currency ISK` | Establish currency when the first record has no label. |
| `--audit audit.json` | Save exact transactions, balances, configuration and FX sources. |

### Rates

`CAD=88.6ISK` means **1 CAD = 88.6 ISK**. `ISK=0.0113CAD` means
**1 ISK = 0.0113 CAD**. Each equation must include the base currency. Supply one
consistent equation per pair. Rates must be positive and finite.

With all rates supplied, no internet connection is needed. Otherwise the app
uses [Frankfurter's ECB feed](https://frankfurter.dev/) and caches results in
`.trip-dollar/fx-cache.json`. Only currency codes and dates are sent to the provider.

The rate date defaults to the latest trip date from a `YYYY.Month.Title` heading
and `MMDD` date headings. Weekends and holidays use the latest published rate
within the preceding seven days. The report shows that actual date. Missing or
unsupported rates stop the calculation; you can supply `--fx` instead.

### Results you can check

Paid is what each person fronted. Share is their economic responsibility.
Net = paid − share: positive means receive; negative means pay.
The engine checks conservation before producing payments.

Money is calculated with Decimal and rounded to two places for display. Displayed
totals may differ by a cent when added; the optional audit keeps exact values.
An unlabeled initial expense is an error unless `--initial-currency` is supplied.
No currency is inferred from the amount, merchant, or destination.
Errors identify the source line and exit with code 2 without payment instructions.

## Included examples and tests

The [Iceland example](examples/iceland-2026/README.md) includes 76 expenses,
an audit, and independently checked reference arithmetic. Reproduce its saved
result with:

```bash
trip-dollar examples/iceland-2026/raw.txt --participants L B D M --base CAD --split L B --fx ISK=0.0113CAD
pytest
```

CI runs pytest on Python 3.13 for pull requests targeting `main`. Tests do not
depend on live exchange-rate services.

## Project layout

```text
src/trip_dollar/
├── frontend/        Browser templates, JavaScript, styles, logo and fonts
├── server/          Flask routes, request validation and Waitress startup
├── core/            Parser, configuration, FX, accounting and settlement
├── cli.py           Command-line interface to the same core
└── report.py        Human-readable formatting
```

The [architecture README](docs/README.md) describes the server architecture
and parser/calculation flow. The core does not import the web server or require
Flask; the frontend never calculates monetary balances or payments.

## Python API

For structured JSON input, configuration files, transaction overrides or custom
display precision, use the engine directly:

```python
import json
from trip_dollar import LedgerEngine

config = json.loads(open("examples/ledger.json").read())
records = json.loads(open("examples/transactions.json").read())
result = LedgerEngine(config).process(records, overrides={})
print(result.to_dict())
```

Configuration examples are in `examples/ledger.json` and `examples/demo/`.
Participants, currencies, rates and default allocation are data, never trip-specific
parser rules. The CLI accepts only raw text; the former two-file CLI has been removed.
