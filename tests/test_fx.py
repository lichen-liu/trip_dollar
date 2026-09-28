from datetime import date
from decimal import Decimal

import pytest

from trip_dollar import fx


def test_equations_both_directions_and_multiple_pairs():
    rates, sources = fx.parse_equations(["CAD=100ISK", "USD=1.25CAD", "EUR=1.5CAD"], "CAD")
    assert rates == {"CAD": 1, "ISK": Decimal(".01"), "USD": Decimal("1.25"), "EUR": Decimal("1.5")}
    assert sources[0]["equation"] == "CAD=100ISK"
    assert fx.parse_equations(["ISK=0.01CAD"], "CAD")[0]["ISK"] == rates["ISK"]


@pytest.mark.parametrize("equations", [
    ["ISK=.01"], ["CAD=0ISK"], ["CAD=-2ISK"], ["CAD=NaNISK"],
    ["CAD=InfinityISK"], ["USD=2EUR"], ["CAD=1CAD"],
    ["CAD=100ISK", "ISK=0.02CAD"],
])
def test_invalid_or_conflicting_equations(equations):
    with pytest.raises(fx.FXError):
        fx.parse_equations(equations, "CAD")


def test_date_inference_uses_latest_calendar_date_without_sorting_records():
    assert fx.infer_date("2026.Aug.Trip\n0816\n0808\n") == date(2026, 8, 16)


@pytest.mark.parametrize("text", ["0816", "2026.Aug.Trip", "2026.Feb.Trip\n0230", "2026.Dec.Trip\n1231\n0101"])
def test_date_requires_unambiguous_valid_year_and_day(text):
    with pytest.raises(fx.FXError):
        fx.infer_date(text)


def test_weekend_uses_last_prior_observation_and_reuses_cache(tmp_path, monkeypatch):
    calls = []
    def download(url):
        calls.append(url)
        return [{"date": "2025-08-08", "base": "ISK", "quote": "CAD", "rate": "0.0110"},
                {"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": "0.0113"},
                {"date": "2025-08-14", "base": "ISK", "quote": "CAD", "rate": "0.0112"}]
    monkeypatch.setattr(fx, "_download", download)
    path = tmp_path / "cache.json"
    first = fx.fetch_rate("ISK", "CAD", date(2025, 8, 17), path)
    second = fx.fetch_rate("ISK", "CAD", date(2025, 8, 17), path)
    assert first["rate"] == "0.0113"
    assert first["effective_date"] == "2025-08-15"
    assert second["cached"] and len(calls) == 1
    assert first["retrieved_at"] == second["retrieved_at"]
    assert "providers=ecb" in calls[0]


@pytest.mark.parametrize("data", [[], {},
    [{"date": "2025-08-18", "base": "ISK", "quote": "CAD", "rate": "1"}],
    [{"date": "2025-08-15", "base": "ISK", "quote": "USD", "rate": "1"}],
    [{"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": "NaN"}],
])
def test_bad_provider_data_never_becomes_a_rate(data, tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "_download", lambda url: data)
    with pytest.raises(fx.FXError):
        fx.fetch_rate("ISK", "CAD", date(2025, 8, 17), tmp_path / "cache.json")
    assert not (tmp_path / "cache.json").exists()
