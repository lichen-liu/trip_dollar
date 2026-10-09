from datetime import date, timedelta
from decimal import Decimal

import pytest

from trip_dollar.core import fx


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


def test_weekend_uses_last_prior_observation_and_fetches_each_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    def download(url):
        calls.append(url)
        return [{"date": "2025-08-08", "base": "ISK", "quote": "CAD", "rate": "0.0110"},
                {"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": "0.0113"},
                {"date": "2025-08-14", "base": "ISK", "quote": "CAD", "rate": "0.0112"}]
    monkeypatch.setattr(fx, "_download", download)
    first = fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))
    second = fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))
    assert first["rate"] == "0.0113"
    assert first["effective_date"] == "2025-08-15"
    assert second["rate"] == first["rate"] and len(calls) == 2
    assert "cached" not in first and "cached" not in second
    assert first["requested_date"] == "2025-08-17"
    assert first["retrieved_at"]
    assert first["source"] == "Frankfurter / ECB"
    assert first["url"] == calls[0]
    assert "providers=ecb" in calls[0]
    assert "from=2025-08-10" in calls[0] and "to=2025-08-17" in calls[0]
    assert list(tmp_path.iterdir()) == []


def test_old_cache_is_ignored_and_left_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_cache = tmp_path / ".trip-dollar" / "fx-cache.json"
    old_cache.parent.mkdir()
    old_cache.write_text("invalid old cache")
    monkeypatch.setattr(fx, "_download", lambda url: [
        {"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": "0.0113"},
    ])
    assert fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))["rate"] == "0.0113"
    assert old_cache.read_text() == "invalid old cache"


def test_future_date_fails_before_download(monkeypatch):
    monkeypatch.setattr(fx, "_download", lambda url: pytest.fail("No future-rate request"))
    with pytest.raises(fx.FXError, match="future"):
        fx.fetch_rate("ISK", "CAD", date.today() + timedelta(days=1))


def test_next_lookup_uses_new_response_and_never_falls_back_to_previous_rate(monkeypatch):
    responses = iter(["0.0113", "0.0114"])
    def download(url):
        return [{"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": next(responses)}]
    monkeypatch.setattr(fx, "_download", download)
    assert fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))["rate"] == "0.0113"
    assert fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))["rate"] == "0.0114"
    def fail(url):
        raise fx.FXError("Provider unavailable")
    monkeypatch.setattr(fx, "_download", fail)
    with pytest.raises(fx.FXError, match="unavailable"):
        fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))


@pytest.mark.parametrize("data", [[], {},
    [{"date": "2025-08-18", "base": "ISK", "quote": "CAD", "rate": "1"}],
    [{"date": "2025-08-15", "base": "ISK", "quote": "USD", "rate": "1"}],
    [{"date": "2025-08-15", "base": "ISK", "quote": "CAD", "rate": "NaN"}],
])
def test_bad_provider_data_never_becomes_a_rate(data, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fx, "_download", lambda url: data)
    with pytest.raises(fx.FXError):
        fx.fetch_rate("ISK", "CAD", date(2025, 8, 17))
    assert list(tmp_path.iterdir()) == []
