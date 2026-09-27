import json
from pathlib import Path

import pytest

from trip_dollar.cli import main
from trip_dollar.fx import FXError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Tests must not access live FX services")
    monkeypatch.setattr("trip_dollar.fx.urlopen", fail)


def arguments(tmp_path, text):
    path = tmp_path / "trip.txt"
    path.write_text(text)
    return [str(path), "--participants", "L=Li Chen", "B=Bob", "D", "M", "--base", "CAD", "--split", "L", "B"]


def test_cli_names_bidirectional_rate_and_exact_audit(tmp_path, capsys):
    args = arguments(tmp_path, "Shop: L100ISKA B10CADA")
    audit = tmp_path / "audit.json"
    assert main(args + ["--fx", "CAD=100ISK", "--offline", "--audit", str(audit)]) == 0
    out = capsys.readouterr().out
    assert "Li Chen" in out and "Bob" in out
    data = json.loads(audit.read_text())
    assert data["total_expense"] == "11.00"
    assert data["configuration"]["fx_rates"]["ISK"] == "0.01"
    for tx in data["transactions"]:
        start, end = tx["source_span"]
        assert tx["source_text"][start:end] == tx["raw_text"]


def test_unlabeled_initial_record_fails_before_lookup(tmp_path, capsys):
    args = arguments(tmp_path, "2025.Aug.Trip\n0808\nCoffee: L10\nTaxi: B20ISKA")
    assert main(args) == 2
    out = capsys.readouterr()
    assert not out.out
    assert "Line 3" in out.err and "--initial-currency" in out.err


def test_initial_currency_explicitly_resolves_first_expense(tmp_path, capsys):
    args = arguments(tmp_path, "L100A B100A")
    assert main(args + ["--initial-currency", "ISK", "--fx", "ISK=0.01CAD", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total_expense"] == "2.00"


def test_partial_manual_rates_only_fetch_missing_currency(tmp_path, monkeypatch, capsys):
    calls = []
    def fetch(currency, base, requested, cache):
        calls.append((currency, base, requested.isoformat()))
        return {"currency": currency, "base_currency": base, "rate": "1.5", "source": "test", "effective_date": "2025-08-15"}
    monkeypatch.setattr("trip_dollar.cli.fetch_rate", fetch)
    args = arguments(tmp_path, "2025.Aug.Trip\n0816\nL100ISKA B10USDA D20EURA")
    assert main(args + ["--fx", "CAD=100ISK", "USD=1.25CAD", "--json"]) == 0
    assert calls == [("EUR", "CAD", "2025-08-16")]
    assert json.loads(capsys.readouterr().out)["total_expense"] == "43.50"


def test_lookup_failure_returns_no_settlement(tmp_path, monkeypatch, capsys):
    def fetch(*args):
        raise FXError("Provider unavailable; supply --fx.")
    monkeypatch.setattr("trip_dollar.cli.fetch_rate", fetch)
    args = arguments(tmp_path, "L100ISKA")
    assert main(args + ["--fx-date", "2025-08-16", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["settlements"] == []


@pytest.mark.parametrize("text", ["", "L10CAD gibberish", "L-10CAD"])
def test_invalid_input_fails_cleanly(text, tmp_path, capsys):
    assert main(arguments(tmp_path, text)) == 2
    out = capsys.readouterr()
    assert "Error" in out.err or "Cannot calculate" in out.err
    assert not out.out


def test_original_config_cli_still_works(capsys):
    assert main(["examples/ledger.json", "examples/transactions.json", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SETTLEMENT_READY"


def test_iceland_cli_matches_saved_reference(capsys):
    assert main(["examples/iceland-2026/raw.txt", "--participants", "L", "B", "D", "M",
                 "--base", "CAD", "--split", "L", "B", "--fx", "ISK=0.0113CAD", "--json"]) == 0
    actual = json.loads(capsys.readouterr().out)
    expected = json.loads(Path("examples/iceland-2026/output/ledger.json").read_text())
    assert actual["net"] == expected["net"]
    assert actual["settlements"] == expected["settlements"]


def test_known_currency_is_not_misread_as_participant_plus_unknown_currency(tmp_path, capsys):
    path = tmp_path / "trip.txt"
    path.write_text("L10CADA C20A")
    assert main([str(path), "--participants", "L", "B", "C", "--base", "CAD",
                 "--split", "L", "B", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [tx["currency"] for tx in result["transactions"]] == ["CAD", "CAD"]


def test_multiple_unlabeled_records_do_not_inherit_from_later_label(tmp_path, capsys):
    args = arguments(tmp_path, "L10 B20 C30CAD")
    assert main(args + ["--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["settlements"] == []


def test_override_establishes_currency_before_lookup(tmp_path, capsys):
    args = arguments(tmp_path, "L10 B20")
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"tx_001": {"currency": "CAD"}}))
    assert main(args + ["--overrides", str(overrides), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [t["currency_source"] for t in result["transactions"]] == ["override", "inherited"]


def test_audit_cannot_overwrite_raw_input(tmp_path, capsys):
    args = arguments(tmp_path, "L10CAD")
    assert main(args + ["--audit", args[0]]) == 2
    assert Path(args[0]).read_text() == "L10CAD"
    assert "overwrite" in capsys.readouterr().err


def test_offline_missing_rate_is_actionable(tmp_path, capsys):
    assert main(arguments(tmp_path, "L10ISKA") + ["--offline"]) == 2
    assert "Missing rates for ISK" in capsys.readouterr().err
