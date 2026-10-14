from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import data


def fixture_history() -> pd.DataFrame:
    index = pd.date_range("2023-01-02", periods=5, freq="B", tz="Europe/Rome")
    close = [100.0, 101.0, 100.0, 101.0, 102.0]
    dividends = [0.0, 0.0, 2.0, 0.0, 0.0]
    adjusted = [100.0]
    for i in range(1, 5):
        adjusted.append(adjusted[-1] * (close[i] + dividends[i]) / close[i - 1])
    return pd.DataFrame({"Close": close, "Adj Close": adjusted, "Dividends": dividends, "Stock Splits": 0.0}, index=index)


def fixture_info(instrument: dict) -> dict:
    return {"symbol": instrument["ticker"], "currency": "EUR", "exchange": "MIL", "quoteType": "ETF", "longName": instrument["name"], "exchangeTimezoneName": "Europe/Rome"}


def fixture_dataset() -> tuple[data.MarketData, dict]:
    instruments = data.instrument_catalog()
    histories = {instrument["ticker"]: fixture_history() for instrument in instruments}
    identities = {instrument["ticker"]: data._validate_identity(instrument, fixture_info(instrument)) for instrument in instruments}
    return data._build_dataset(histories, identities, "test-version", "2023-01-09T12:00:00+00:00"), histories


def test_exact_catalog_ids_labels_and_isins():
    assets = [row for row in data.instrument_catalog() if row["role"] == "asset"]
    expected = [
        (0, "Monetario €", "FR0010754200", "C3M.MI"),
        (1, "Obbl Short Term", "IE00B1FZS681", "IBGX.MI"),
        (2, "Obbl MLT", "IE00B1FZS913", "IBGL.MI"),
        (3, "Global Aggr. Corporate", "IE00B7J7TB45", "CORP.MI"),
        (4, "Obbl Corp High Yield", "IE00B66F4759", "IHYG.MI"),
        (5, "Obbl EM", "IE00B6TLBW47", "EMCR.MI"),
        (6, "Azioni Europe", "IE00B4K48X80", "SMEA.MI"),
        (7, "Az North America", "IE00B5BMR087", "CSSPX.MI"),
        (8, "Az Tecnologico", "IE0032077012", "EQQQ.MI"),
        (9, "Az EM", "IE00BKM4GZ66", "EIMI.MI"),
        (10, "Oro", "JE00B1VS3770", "PHAU.MI"),
        (11, "Broad Commodities", "IE00BD6FTQ80", "CMOD.MI"),
    ]
    assert [(r["id"], r["label"], r["isin"], r["ticker"]) for r in assets] == expected
    benchmarks = [row for row in data.instrument_catalog() if row["role"] == "benchmark"]
    assert [(row["ticker"], row["isin"]) for row in benchmarks] == [("SWDA.MI", "IE00B4L5Y983"), ("AGGH.MI", "IE00BDBRDM35")]


@pytest.mark.parametrize("field,value,match", [("symbol", "C3M.PA", "ticker esatto"), ("currency", "USD", "richiesta EUR"), ("exchange", "PAR", "atteso MIL"), ("quoteType", "EQUITY", "ETF/ETC"), ("longName", "Amundi Euro Corporate 5-7", "non coerente"), ("isin", "FR0000000000", "ISIN provider")])
def test_provider_mismatch_fails_instead_of_conversion_or_substitution(field, value, match):
    instrument = data.instrument_catalog()[0]
    info = fixture_info(instrument)
    info[field] = value
    with pytest.raises(data.DataValidationError, match=match):
        data._validate_identity(instrument, info)


def test_declared_isin_is_never_presented_as_provider_verified():
    instrument = data.instrument_catalog()[0]
    info = fixture_info(instrument)
    metadata = data._validate_identity(instrument, info)
    assert metadata["listing_verified"] is True
    assert metadata["isin_verified"] is False
    assert metadata["provider_isin"] is None
    info["isin"] = instrument["isin"]
    assert data._validate_identity(instrument, info)["isin_verified"] is True


def test_total_return_quality_rejects_unadjusted_distributions():
    history = fixture_history()
    history["Adj Close"] = history["Close"]
    with pytest.raises(data.DataValidationError, match="non le incorpora"):
        data._quality(history, "IBGX.MI")


def test_each_distribution_must_be_incorporated_not_just_one():
    history = pd.DataFrame({"Close": [100.0, 99.5, 99.5, 99.0], "Adj Close": [99.5, 99.5, 99.5, 99.0], "Dividends": [0.0, 0.5, 0.0, 0.5], "Stock Splits": 0.0}, index=pd.date_range("2023-01-02", periods=4, freq="B"))
    with pytest.raises(data.DataValidationError, match="1 distribuzioni presenti"):
        data._quality(history, "IBGX.MI")


def test_distribution_is_in_adjusted_once_and_not_added_twice():
    history = data._normalize_history(fixture_history(), "IBGX.MI")
    quality, warnings = data._quality(history, "IBGX.MI")
    adjusted_return = history["Adj Close"].iloc[2] / history["Adj Close"].iloc[1] - 1
    expected = (history["Close"].iloc[2] + history["Dividends"].iloc[2]) / history["Close"].iloc[1] - 1
    assert adjusted_return == pytest.approx(expected)
    assert quality["adjusted_checked"] is True
    assert quality["max_daily_total_return_discrepancy"] < 1e-12
    assert warnings == []


def test_future_splits_are_undone_for_historic_prices_and_distribution_units():
    history = pd.DataFrame({"Close": [50.0, 51.0, 52.0, 53.0], "Adj Close": [49.0, 50.0, 52.0, 53.0], "Dividends": [0.5, 0.0, 0.0, 0.0], "Stock Splits": [0.0, 0.0, 2.0, 0.0]}, index=pd.date_range("2023-01-02", periods=4, freq="B"))
    tradable_close, tradable_dividends = data._tradable_prices(history)
    assert tradable_close.tolist() == [100.0, 102.0, 52.0, 53.0]
    assert tradable_dividends.tolist() == [1.0, 0.0, 0.0, 0.0]
    # Doubling the quantity leaves the portfolio value coherent with the
    # split-normalized market return at the corporate action.
    assert 2 * tradable_close.iloc[2] / tradable_close.iloc[1] == pytest.approx(history.Close.iloc[2] / history.Close.iloc[1])


def test_distinct_inceptions_gaps_and_timezone_are_preserved():
    dataset, histories = fixture_dataset()
    instruments = data.instrument_catalog()
    histories[instruments[1]["ticker"]] = fixture_history().iloc[2:]
    gap_ticker = instruments[2]["ticker"]
    histories[gap_ticker] = fixture_history().drop(fixture_history().index[3])
    identities = {row["ticker"]: data._validate_identity(row, fixture_info(row)) for row in instruments}
    dataset = data._build_dataset(histories, identities, "test", "2023-01-10T00:00:00+00:00")
    assert dataset.adjusted.index.tz is None
    assert dataset.adjusted.index.equals(dataset.close.index)
    assert dataset.adjusted.index.equals(dataset.dividends.index)
    assert dataset.close[instruments[1]["ticker"]].iloc[:2].isna().all()
    assert pd.isna(dataset.close[gap_ticker].iloc[3])
    assert any(gap_ticker in warning and "forward-fill" in warning for warning in dataset.warnings)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf")])
def test_invalid_prices_fail(bad):
    history = fixture_history()
    history.iloc[0, history.columns.get_loc("Close")] = bad
    with pytest.raises(data.DataValidationError, match="non positivi"):
        data._normalize_history(history, "C3M.MI")


def test_snapshot_roundtrip_provenance_raw_data_and_integrity(tmp_path: Path):
    dataset, histories = fixture_dataset()
    data._save_snapshot(dataset, histories, tmp_path)
    restored = data.load_cached(tmp_path)
    assert restored.source == "yahoo"
    assert restored.metadata["yfinance_version"] == "test-version"
    assert restored.metadata["fx_conversion_applied"] is False
    pd.testing.assert_frame_equal(restored.adjusted, dataset.adjusted, check_freq=False)
    pd.testing.assert_frame_equal(restored.close, dataset.close, check_freq=False)
    pointer = json.loads((tmp_path / "latest.json").read_text())
    raw = tmp_path / pointer["snapshot"] / "raw" / "C3M.MI.csv"
    assert raw.exists()
    assert "Adj Close" in pd.read_csv(raw).columns
    price_file = raw.parent.parent / "close.csv"
    price_file.write_text(price_file.read_text().replace("100", "200", 1))
    with pytest.raises(data.DataValidationError, match="checksum"):
        data.load_cached(tmp_path)


def test_absent_cache_returns_none(tmp_path):
    assert data.load_cached(tmp_path) is None


def test_failed_refresh_keeps_previous_cache_and_has_no_silent_demo(monkeypatch, tmp_path):
    dataset, histories = fixture_dataset()
    data._save_snapshot(dataset, histories, tmp_path)
    previous_pointer = (tmp_path / "latest.json").read_bytes()
    import yfinance

    class InvalidTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def get_info(self):
            row = next(row for row in data.instrument_catalog() if row["ticker"] == self.ticker)
            return {**fixture_info(row), "currency": "USD"}

    monkeypatch.setattr(yfinance, "Ticker", InvalidTicker)
    monkeypatch.setattr(yfinance, "set_tz_cache_location", lambda _: None)
    with pytest.raises(data.DataValidationError, match="richiesta EUR"):
        data.refresh_yahoo(tmp_path)
    assert (tmp_path / "latest.json").read_bytes() == previous_pointer
    assert data.load_cached(tmp_path).source == "yahoo"


def test_valid_refresh_retains_provider_version_and_exact_request_flags(monkeypatch, tmp_path):
    import yfinance
    calls = []

    class ValidTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def get_info(self):
            row = next(row for row in data.instrument_catalog() if row["ticker"] == self.ticker)
            return fixture_info(row)

        def history(self, **kwargs):
            calls.append((self.ticker, kwargs))
            return fixture_history()

    monkeypatch.setattr(yfinance, "Ticker", ValidTicker)
    monkeypatch.setattr(yfinance, "set_tz_cache_location", lambda _: None)
    dataset = data.refresh_yahoo(tmp_path)
    assert dataset.source == "yahoo"
    assert dataset.metadata["yfinance_version"] == yfinance.__version__
    assert len(calls) == 14
    assert all(kwargs == {"period": "max", "auto_adjust": False, "actions": True, "repair": False, "raise_errors": True} for _, kwargs in calls)
    assert data.load_cached(tmp_path) is not None


def test_demo_is_deterministic_synthetic_distinct_and_distribution_coherent():
    first = data.load_demo()
    second = data.load_demo()
    assert first.source == "demo"
    assert first.metadata["synthetic"] is True
    pd.testing.assert_frame_equal(first.adjusted, second.adjusted)
    pd.testing.assert_frame_equal(first.close, second.close)
    assert first.adjusted.index[-1] - first.adjusted.index[0] > pd.Timedelta(days=3650)
    assert first.close["C3M.MI"].first_valid_index() < first.close["CMOD.MI"].first_valid_index()
    assert not any(record["isin_verified"] for record in first.metadata["instruments"].values())
    for ticker in first.close:
        expected = (first.close[ticker] + first.dividends[ticker]) / first.close[ticker].shift(1) - 1
        actual = first.adjusted[ticker].pct_change(fill_method=None)
        np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), atol=1e-12, equal_nan=True)
    assert "sintetic" in first.warnings[0].lower()


def test_hosting_data_directory_can_be_configured_without_changing_checkout(tmp_path):
    import os
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, '-c', 'from app.data import CACHE_DIR; print(CACHE_DIR)'],
        env={**os.environ, 'MOMENTUM_DATA_DIR': str(tmp_path)},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == str(tmp_path / 'yahoo')
