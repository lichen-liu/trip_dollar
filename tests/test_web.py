"""The web boundary must use the same accounting and failure policy as the CLI."""
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from trip_dollar.core.fx import FXError
from trip_dollar.server.app import create_app, main


@pytest.fixture
def app(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Tests must not access live FX services")
    monkeypatch.setattr("trip_dollar.core.fx.urlopen", fail)
    return create_app({"TESTING": True, "FX_CACHE": tmp_path / "rates.json"})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def payload(client):
    return client.get("/static/example.json").get_json()


def test_page_and_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Calculate the split" in response.data
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    for asset in ["app.js", "app.css", "example.json", "favicon.svg",
                  "fonts/manrope-variable.woff2", "fonts/dm-mono-400.woff2", "fonts/dm-mono-500.woff2",
                  "fonts/OFL-Manrope.txt", "fonts/OFL-DMMono.txt"]:
        assert client.get(f"/static/{asset}").status_code == 200
    assert client.get("/health").get_json() == {"status": "ok"}


def test_boboji_brand_and_self_hosted_aerorepo_fonts(client):
    page = client.get("/").get_data(as_text=True)
    assert "Trip Split — boboji.fyi" in page
    assert 'content="Trip Split · boboji.fyi"' in page
    assert 'aria-label="Trip Split, back to top"' in page
    assert "See who owes whom." in page
    assert "Trip Dollar" not in page
    assert 'aria-label="boboji.fyi home"' in page
    assert 'href="https://boboji.fyi/"' in page
    assert ">b/</b>" in page
    assert 'id="results-link"' in page
    css = client.get("/static/app.css").get_data(as_text=True)
    assert "#d8ff36" in css and "#151816" in css
    assert 'font-family: "Manrope"' in css and 'font-family: "DM Mono"' in css
    assert "fonts.googleapis.com" not in css
    assert "Georgia" not in css


def test_demo_golden_end_to_end(client, payload):
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 200
    audit, display = response.json["audit"], response.json["display"]
    assert audit["status"] == "SETTLEMENT_READY"
    assert Decimal(audit["total_expense"]) == 290
    assert {p: Decimal(n) for p, n in audit["net"].items()} == {"L": 30, "B": -50, "C": 20}
    assert {(p["from"], p["to"], Decimal(p["amount"])) for p in audit["settlements"]} == {("B", "L", 30), ("B", "C", 20)}
    assert [tx["currency"] for tx in audit["transactions"]] == ["CAD", "CAD", "USD", "USD", "CAD", "CAD"]
    assert audit["transactions"][3]["date"] == "0814"  # Not sorted before currency resolution.
    assert audit["source_text"] == payload["source"]
    assert audit["source_sha256"] == hashlib.sha256(payload["source"].encode()).hexdigest()
    assert sum(Decimal(n) for n in audit["net"].values()) == 0
    assert display["total"] == "290.00"
    assert {p["display_amount"] for p in display["payments"]} == {"20.00", "30.00"}
    assert display["balances"][1]["direction"] == "pay"
    for tx in audit["transactions"]:
        start, end = tx["source_span"]
        assert tx["source_text"][start:end] == tx["raw_text"]


def test_iceland_web_matches_saved_reference(client):
    response = client.post("/api/calculate", json={
        "source": Path("examples/iceland-2026/raw.txt").read_text(),
        "participants": [{"code": c, "split": c in "LB"} for c in "LBDM"],
        "base": "CAD", "fx": "ISK=0.0113CAD",
    })
    assert response.status_code == 200
    audit = response.json["audit"]
    expected = json.loads(Path("examples/iceland-2026/output/ledger.json").read_text())
    assert len(audit["transactions"]) == 76
    for field in ["paid", "share", "net", "settlements", "total_expense"]:
        assert audit[field] == expected[field]


@pytest.mark.parametrize("source,initial,expected", [
    ("L100CADA B20A", "", "CAD"),
    ("L100CADAB20A", "", "CAD"),
    ("L100A B20A", "CAD", "CAD"),
    ("L100USDA B20A", "CAD", "USD"),
])
def test_compact_and_initial_currency(client, payload, source, initial, expected):
    payload.update(source=source, initial_currency=initial)
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 200
    assert [tx["currency"] for tx in response.json["audit"]["transactions"]] == [expected, expected]


def test_online_fx_uses_shared_loader(app, payload):
    calls = []
    def fetch(currency, base, requested, cache):
        calls.append((currency, base, requested))
        return {"currency": currency, "base_currency": base, "rate": "1.25",
                "source": "test", "requested_date": str(requested), "effective_date": "2026-08-14"}
    app.config["RATE_LOADER"] = fetch
    payload["fx"] = ""
    response = app.test_client().post("/api/calculate", json=payload)
    assert response.status_code == 200
    assert calls == [("USD", "CAD", date(2026, 8, 15))]
    assert response.json["audit"]["fx_provenance"][0]["effective_date"] == "2026-08-14"


def test_missing_currency_is_error_before_network(client, payload):
    payload["source"] = "2026.Aug.Trip\n0815\nCoffee: L10\nB20USDA"
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 422
    assert response.json["line"] == 3
    assert "starting currency" in response.json["error"]
    assert response.json["settlements"] == []


def test_failed_lookup_never_returns_payment_plan(app, payload):
    def fail(*args):
        raise FXError("Could not retrieve online rates. Supply --fx explicitly.")
    app.config["RATE_LOADER"] = fail
    payload["fx"] = ""
    response = app.test_client().post("/api/calculate", json=payload)
    assert response.status_code == 422
    assert "manual rate" in response.json["error"]
    assert response.json["settlements"] == []
    assert "audit" not in response.json


@pytest.mark.parametrize("change", [
    {"source": ""}, {"source": "L-10CAD"}, {"source": "Q10CAD"},
    {"source": "L10CAD nonsense"}, {"source": "L10CADZ"},
    {"participants": []}, {"participants": "L B"}, {"participants": [None]},
    {"participants": [{"code": "A", "split": True}]},
    {"participants": [{"code": "1", "split": True}]},
    {"participants": [{"code": "L=Name", "split": True}]},
    {"participants": [{"code": "L", "split": "yes"}]},
    {"participants": [{"code": "L", "split": 1}]},
    {"participants": [{"code": "L", "name": None, "split": True}]},
    {"participants": [{"code": "L"}]},
    {"participants": [{"code": "L", "split": True}, {"code": "L"}]},
    {"base": "not-currency"}, {"base": 4}, {"initial_currency": "???"},
    {"fx_date": "2026-02-30"}, {"fx_date": 1},
    {"fx": "USD=0CAD"}, {"fx": "USD=1CAD USD=2CAD"}, {"fx": ["USD=1CAD"]},
    {"source": "L10CAD\n" + "x" * 2049}, {"source": "x" * 64001},
])
def test_invalid_form_blocks_settlement(client, payload, change):
    payload.update(change)
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 422
    assert response.json["settlements"] == []
    assert "audit" not in response.json


def test_bidirectional_multiple_rates_and_case_sensitive_codes(client, payload):
    payload.update(source="l10USDA B20ISKA", fx="CAD=100ISK USD=1.25CAD",
                   participants=[{"code": "l", "split": True}, {"code": "B", "split": True}])
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 200
    assert Decimal(response.json["audit"]["total_expense"]) == Decimal("12.7")


def test_untrusted_origin_and_host_rejected(client, payload):
    assert client.post("/api/calculate", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/calculate", json=payload, headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/calculate", json=payload, headers={"Origin": "http://localhost"}).status_code == 200


@pytest.mark.parametrize("body,status", [("{", 400), ("null", 400), ("[]", 400), ("x" * 128001, 413)])
def test_bad_request_is_json(client, body, status):
    response = client.post("/api/calculate", data=body, content_type="application/json")
    assert response.status_code == status
    assert response.json["settlements"] == []


def test_non_json_rejected(client):
    assert client.post("/api/calculate", data="source=L10CAD").status_code == 415


def test_source_limit_counts_utf8_bytes(client, payload):
    payload["source"] = "é" * 33000
    response = client.post("/api/calculate", data=json.dumps(payload, ensure_ascii=False).encode(),
                           content_type="application/json")
    assert response.status_code == 422
    assert "64 KB" in response.json["error"]


def test_names_and_notes_are_not_injected_into_page(client, payload):
    payload["participants"][0]["name"] = '<img src=x onerror="alert(1)">'
    response = client.post("/api/calculate", json=payload)
    assert response.status_code == 200
    assert response.json["display"]["balances"][0]["name"] == payload["participants"][0]["name"]
    assert payload["participants"][0]["name"].encode() not in client.get("/").data
    assert b"innerHTML" not in client.get("/static/app.js").data


def test_server_stays_local_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr("waitress.serve", lambda app, **options: calls.append(options))
    main([])
    assert calls == [{"host": "127.0.0.1", "port": 8000, "threads": 4, "max_request_body_size": 128000}]
    with pytest.raises(SystemExit):
        main(["--port", "0"])


def test_busy_port_is_a_clean_startup_error(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("Address already in use")
    monkeypatch.setattr("waitress.serve", fail)
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "different --port" in capsys.readouterr().err
