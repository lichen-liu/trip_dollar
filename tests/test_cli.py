import json
from pathlib import Path

import pytest

from trip_dollar.cli import main
from trip_dollar.core.fx import FXError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Tests must not access live FX services")
    monkeypatch.setattr("trip_dollar.core.fx.urlopen", fail)


def arguments(tmp_path, text):
    path = tmp_path / "trip.txt"
    path.write_text(text)
    return [str(path), "--participants", "L=Li Chen", "B=Bob", "D", "M", "--base", "CAD", "--split", "L", "B"]


def audit_run(args, tmp_path):
    audit = tmp_path / "audit.json"
    assert main(args + ["--audit", str(audit)]) == 0
    return json.loads(audit.read_text())


def test_names_bidirectional_fx_and_source_preservation(tmp_path, capsys):
    args = arguments(tmp_path, "Shop: L100ISKA B10CADA")
    data = audit_run(args + ["--fx", "CAD=100ISK"], tmp_path)
    out = capsys.readouterr().out
    assert "Li Chen" in out and "Bob" in out
    assert data["total_expense"] == "11.00"
    assert data["configuration"]["fx_rates"]["ISK"] == "0.01"
    for tx in data["transactions"]:
        start, end = tx["source_span"]
        assert tx["source_text"][start:end] == tx["raw_text"]


@pytest.mark.parametrize("text,line", [
    ("2025.Aug.Trip\n0808\nCoffee: L10\nTaxi: B20ISKA", 3),
    ("L10 B20 D30CAD", 1),
])
def test_missing_initial_currency_fails_before_lookup(tmp_path, capsys, text, line):
    assert main(arguments(tmp_path, text)) == 2
    out = capsys.readouterr()
    assert not out.out
    assert f"Line {line}" in out.err and "--initial-currency" in out.err


def test_initial_currency_resolves_first_expense(tmp_path):
    args = arguments(tmp_path, "L100A B100A")
    result = audit_run(args + ["--initial-currency", "ISK", "--fx", "ISK=0.01CAD"], tmp_path)
    assert result["total_expense"] == "2.00"


def test_only_missing_rates_are_fetched(tmp_path, monkeypatch):
    calls = []
    def fetch(currency, base, requested, cache):
        calls.append((currency, base, requested.isoformat()))
        return {"currency": currency, "base_currency": base, "rate": "1.5", "source": "test", "effective_date": "2025-08-15"}
    monkeypatch.setattr("trip_dollar.cli.fetch_rate", fetch)
    args = arguments(tmp_path, "2025.Aug.Trip\n0816\nL100ISKA B10USDA D20EURA")
    result = audit_run(args + ["--fx", "CAD=100ISK", "USD=1.25CAD"], tmp_path)
    assert calls == [("EUR", "CAD", "2025-08-16")]
    assert result["total_expense"] == "43.50"


def test_failed_lookup_prints_no_payments(tmp_path, monkeypatch, capsys):
    def fetch(*args):
        raise FXError("Provider unavailable; supply --fx.")
    monkeypatch.setattr("trip_dollar.cli.fetch_rate", fetch)
    assert main(arguments(tmp_path, "L100ISKA") + ["--fx-date", "2025-08-16"]) == 2
    out = capsys.readouterr()
    assert not out.out and "--fx" in out.err


@pytest.mark.parametrize("text", ["", "L10CAD gibberish", "L-10CAD"])
def test_invalid_input_fails_cleanly(text, tmp_path, capsys):
    assert main(arguments(tmp_path, text)) == 2
    out = capsys.readouterr()
    assert "Error" in out.err or "Cannot calculate" in out.err
    assert not out.out


def test_iceland_matches_saved_reference(tmp_path):
    actual = audit_run(["examples/iceland-2026/raw.txt", "--participants", "L", "B", "D", "M",
                        "--base", "CAD", "--split", "L", "B", "--fx", "ISK=0.0113CAD"], tmp_path)
    expected = json.loads(Path("examples/iceland-2026/output/ledger.json").read_text())
    assert actual["net"] == expected["net"]
    assert actual["settlements"] == expected["settlements"]


def test_currency_is_not_misread_as_participant(tmp_path):
    path = tmp_path / "trip.txt"
    path.write_text("L10CADA C20A")
    result = audit_run([str(path), "--participants", "L", "B", "C", "--base", "CAD", "--split", "L", "B"], tmp_path)
    assert [tx["currency"] for tx in result["transactions"]] == ["CAD", "CAD"]


def test_audit_cannot_overwrite_input(tmp_path, capsys):
    args = arguments(tmp_path, "L10CAD")
    assert main(args + ["--audit", args[0]]) == 2
    assert Path(args[0]).read_text() == "L10CAD"
    assert "overwrite" in capsys.readouterr().err
