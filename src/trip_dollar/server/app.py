"""Local web interface. All money is calculated by the existing Python engine."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, DecimalException
from pathlib import Path
from string import ascii_letters

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from ..core.service import InputError, LedgerOptions, calculate
from ..report import display_amount

MAX_SOURCE = 64_000
MAX_PARTICIPANTS = len(ascii_letters.replace("A", ""))


def request_options(data: dict) -> tuple[LedgerOptions, str]:
    def text(key: str, maximum: int = 100, optional: bool = False) -> str:
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
            raise InputError(f"Enter a valid {key.replace('_', ' ')}.", field=key)
        return value.strip() if key != "source" else value

    source = text("source", MAX_SOURCE)
    if len(source.encode("utf-8")) > MAX_SOURCE:
        raise InputError("Use notes smaller than 64 KB.")
    if any(len(line) > 2048 for line in source.splitlines()) or len(source.splitlines()) > 5000:
        raise InputError("The notes are too large. Use at most 5,000 lines and 2,048 characters per line.")
    participants = data.get("participants")
    if not isinstance(participants, list) or not 1 <= len(participants) <= MAX_PARTICIPANTS:
        raise InputError(f"Add between 1 and {MAX_PARTICIPANTS} participants, each with a unique letter.", field="participants")
    values, split = [], []
    for person in participants:
        if not isinstance(person, dict):
            raise InputError("Check the participant list.", field="participants")
        code, name = person.get("code"), person.get("name", "")
        if not isinstance(code, str) or not isinstance(name, str) or len(name) > 100:
            raise InputError("Each person needs a letter code and an optional name.", field="participants")
        if len(code) != 1 or not code.isascii() or not code.isalpha() or code == "A":
            raise InputError("Codes must be single letters. A is reserved for everyone.", field="participants")
        if not isinstance(person.get("split", False), bool):
            raise InputError("Check the default split.", field="participants")
        values.append(f"{code}={name.strip()}" if name.strip() else code)
        if person.get("split", False):
            split.append(code)
    if not split:
        raise InputError("Choose at least one person for the default split.", field="participants")
    fx_date = text("fx_date", optional=True)
    try:
        parsed_date = date.fromisoformat(fx_date) if fx_date else None
    except ValueError as exc:
        raise InputError("Use a valid rate date, in YYYY-MM-DD format.", field="fx_date") from exc
    return LedgerOptions(values, text("base", 3), split,
                         text("fx", 2000, optional=True).split(),
                         text("initial_currency", 3, optional=True) or None, parsed_date), source


def presentation(audit: dict) -> dict:
    """Formatted strings only. The browser never calculates financial values."""
    balances = []
    for person in audit["configuration"]["participants"]:
        code = person["id"]
        net = Decimal(audit["net"][code])
        balances.append({**person, "paid": display_amount(audit["paid"][code]),
                         "share": display_amount(audit["share"][code]),
                         "net": display_amount(str(abs(net))),
                         "direction": "receive" if net > 0 else "pay" if net < 0 else "settled"})
    return {"total": display_amount(audit["total_expense"]), "balances": balances,
            "payments": [{**item, "display_amount": display_amount(item["amount"])} for item in audit["settlements"]],
            "expenses": [{"id": tx["id"], "amount": display_amount(tx["original_amount"]),
                          "base_amount": display_amount(tx["base_amount"])} for tx in audit["transactions"]]}


def create_app(config: dict | None = None) -> Flask:
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    app = Flask(__name__, template_folder=str(frontend / "templates"),
                static_folder=str(frontend / "static"), static_url_path="/static")
    app.config.update(MAX_CONTENT_LENGTH=128_000, FX_CACHE=Path(".trip-dollar/fx-cache.json"),
                      TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    if config:
        app.config.update(config)

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/api/calculate")
    def calculate_ledger():
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            return jsonify(error="Open this app in its own browser tab.", settlements=[]), 403
        if not request.is_json:
            return jsonify(error="Send the expense form as JSON.", settlements=[]), 415
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify(error="Check the expense form.", settlements=[]), 400
        try:
            options, source = request_options(data)
            kwargs = {"cache_path": app.config["FX_CACHE"]}
            if app.config.get("RATE_LOADER"):
                kwargs["rate_loader"] = app.config["RATE_LOADER"]
            audit = calculate(options, source, **kwargs)
            return jsonify(audit=audit, display=presentation(audit))
        except InputError as exc:
            message = str(exc).replace("--initial-currency", "a starting currency")
            message = message.replace("--fx-date YYYY-MM-DD", "the rate date").replace("--fx", "a manual rate")
            return jsonify(error=message, field=exc.field, line=exc.line, settlements=[]), 422
        except DecimalException:
            return jsonify(error="An amount is too large to calculate safely. Check the notes and rates.", settlements=[]), 422
        except ValueError as exc:
            return jsonify(error=str(exc), settlements=[]), 422
        except OSError:
            app.logger.exception("FX cache unavailable")
            return jsonify(error="Could not access the exchange-rate cache. Try a manual rate.", settlements=[]), 503

    @app.errorhandler(HTTPException)
    def http_error(exc):
        message = "The upload is too large. Use a text file smaller than 64 KB." if exc.code == 413 else exc.description
        return jsonify(error=message, settlements=[]), exc.code

    @app.errorhandler(500)
    def unexpected_error(exc):
        return jsonify(error="Something went wrong on the server. No payment plan was produced.", settlements=[]), 500

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the Trip Split web app on this computer.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from waitress import serve
    print(f"Trip Split: http://127.0.0.1:{args.port}", flush=True)
    try:
        serve(create_app(), host="127.0.0.1", port=args.port, threads=4,
              max_request_body_size=128_000)
    except OSError as exc:
        parser.exit(2, f"Could not start the local server: {exc}. Try a different --port.\n")


if __name__ == "__main__":
    main()
