"""Behavioral checks for signal timing, data gaps, sizing and actual drift."""
import numpy as np
import pandas as pd
import pytest

from app.data import MarketData, instrument_catalog
from app.engine import compute_signal, monthly_prices, run_backtest, targets_from_backtest
from app.models import StrategyConfig


ASSETS = [item["ticker"] for item in instrument_catalog() if item["role"] == "asset"]
TICKERS = ASSETS + ["SWDA.MI", "AGGH.MI"]


def monthly_frame(count=15):
    dates = pd.date_range("2020-01-31", periods=count, freq="ME")
    result = pd.DataFrame(100.0, index=dates, columns=TICKERS)
    result.loc[dates[12]:, ASSETS] = 90.0
    result.loc[dates[12]:, ASSETS[1]] = 120.0
    result.loc[dates[12]:, ASSETS[2]] = 110.0
    return result


def market(frame):
    return MarketData(adjusted=frame, close=frame.copy(),
                      dividends=pd.DataFrame(0.0, index=frame.index, columns=frame.columns),
                      metadata={}, source="test", updated_at="2021-04-01T00:00:00Z", warnings=[])


def config(**changes):
    return StrategyConfig(**changes)


def row(signal, ticker):
    return next(item for item in signal["rows"] if item["ticker"] == ticker)


def test_month_containing_asof_is_never_certified_complete_and_gaps_remain():
    prices = pd.DataFrame({ASSETS[0]: [100.0, 101.0, 103.0]},
                          index=pd.to_datetime(["2020-01-31", "2020-02-28", "2020-04-30"]))
    result = monthly_prices(prices, "2020-04-30")
    assert result.index[-1] == pd.Timestamp("2020-02-29")
    completed = monthly_prices(prices, "2020-05-01")
    assert pd.isna(completed.loc["2020-03-31", ASSETS[0]])
    assert completed.loc["2020-04-30", ASSETS[0]] == 103


def test_trailing_feed_month_is_not_certified_from_stale_midmonth_quotes():
    prices = pd.DataFrame({ASSETS[0]: [100.0, 101.0, 103.0]},
                          index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-14"]))
    result = monthly_prices(prices, "2024-04-01")
    assert result.index[-1] == pd.Timestamp("2024-02-29")
    assert pd.Timestamp("2024-03-31") not in result.index


def test_single_ticker_staleness_does_not_reuse_an_earlier_quote_as_month_end():
    prices = pd.DataFrame({ASSETS[0]: [100.0, np.nan], ASSETS[1]: [110.0, 120.0]},
                          index=pd.to_datetime(["2024-03-14", "2024-03-28"]))
    # Good Friday and Easter Monday are closed; March's final XMIL session is 28th.
    result = monthly_prices(prices, "2024-04-01")
    assert pd.isna(result.loc["2024-03-31", ASSETS[0]])
    assert result.loc["2024-03-31", ASSETS[1]] == 120


def test_formulas_use_cumulative_returns_without_skipping_the_last_month():
    prices = monthly_frame(13)
    prices[ASSETS[1]] = np.arange(100.0, 113.0)
    signal = compute_signal(prices, config())
    item = row(signal, ASSETS[1])
    expected = [112 / 109 - 1, 112 / 106 - 1, 112 / 100 - 1]
    assert [item["r3"], item["r6"], item["r12"]] == pytest.approx(expected)
    assert item["score"] == pytest.approx(sum(expected) / 3)
    reactive = row(compute_signal(prices, config(formula="reactive")), ASSETS[1])
    assert reactive["score"] == pytest.approx(np.dot([.5, .3, .2], expected))
    assert item["ma12"] == pytest.approx(np.mean(np.arange(101.0, 113.0)))


def test_missing_month_blocks_availability_even_with_valid_window_endpoints():
    prices = monthly_frame(13).drop(pd.Timestamp("2020-09-30"))
    signal = compute_signal(prices, config())
    assert signal["available_count"] == 0
    assert not signal["tradable"]
    assert signal["weights"] == {}


def test_future_prices_cannot_change_a_past_signal():
    prices = monthly_frame(15)
    date = prices.index[12]
    reference = compute_signal(prices.loc[:date], config(), date)
    prices.loc[prices.index > date] = 1e9
    assert compute_signal(prices, config(), date) == reference


def test_less_than_n_score_sizing_allocates_residual_to_fallback():
    prices = monthly_frame(13)
    prices.iloc[-1, :12] = 90.0
    prices.iloc[-1, 1:4] = [110.0, 106.0, 104.0]
    signal = compute_signal(prices, config())
    assert signal["selected"] == ASSETS[1:4]
    assert signal["weights"] == pytest.approx({ASSETS[1]: .25, ASSETS[2]: .15, ASSETS[3]: .10, ASSETS[0]: .50})
    assert sum(signal["weights"].values()) == pytest.approx(1)
    assert min(signal["weights"].values()) >= 0


def test_fallback_already_selected_receives_both_weights_without_duplication():
    prices = monthly_frame(13)
    prices.iloc[-1, :12] = 90.0
    prices.iloc[-1, 0] = 110.0
    signal = compute_signal(prices, config())
    assert signal["selected"] == [ASSETS[0]]
    assert signal["weights"] == {ASSETS[0]: 1.0}


@pytest.mark.parametrize("fallback", ["C3M.MI", "IBGX.MI"])
def test_nonpositive_scores_still_invest_fully_in_the_chosen_fallback(fallback):
    prices = monthly_frame(13)
    prices.iloc[-1, :12] = 100.0
    signal = compute_signal(prices, config(fallback=fallback))
    assert signal["eligible_count"] == 0
    assert signal["weights"] == {fallback: 1.0}
    prices.iloc[-1, :12] = 80.0
    assert compute_signal(prices, config(fallback=fallback))["weights"] == {fallback: 1.0}


def test_exact_score_ties_are_broken_by_immutable_asset_id():
    prices = monthly_frame(13)
    prices.iloc[-1, :12] = 110.0
    signal = compute_signal(prices, config(top_n=2, weighting="equal"))
    assert signal["selected"] == ASSETS[:2]
    assert [item["id"] for item in signal["rows"]] == list(range(12))
    assert signal["weights"] == pytest.approx({ASSETS[0]: .5, ASSETS[1]: .5})


def test_ma_filter_uses_last_12_closes_including_current_and_strict_comparison():
    prices = monthly_frame(13)
    prices[ASSETS[1]] = 100.0
    prices.loc[prices.index[0], ASSETS[1]] = 50.0
    prices.loc[prices.index[-1], ASSETS[1]] = 90.0
    assert row(compute_signal(prices, config()), ASSETS[1])["eligible"]
    assert not row(compute_signal(prices, config(ma_filter=True)), ASSETS[1])["eligible"]


def test_progressive_counts_data_availability_not_positive_scores():
    prices = monthly_frame(13)
    prices.loc[prices.index[0], ASSETS[-2:]] = np.nan
    prices.iloc[-1, :12] = 90.0
    prices.iloc[-1, 1] = 110.0
    signal = compute_signal(prices, config(universe_mode="progressive", min_assets=10))
    assert signal["available_count"] == 10
    assert signal["eligible_count"] == 1
    assert signal["tradable"]
    assert signal["weights"] == pytest.approx({ASSETS[1]: 1 / 6, ASSETS[0]: 5 / 6})
    assert not compute_signal(prices, config())["tradable"]


def test_inverse_volatility_uses_12_sample_returns_and_rejects_zero_volatility():
    prices = monthly_frame(13)
    prices[ASSETS[0]] = 100.0
    returns_a = np.array([.01, .02, -.01, .04] * 3)
    returns_b = np.array([.01, .015, .02, .03] * 3)
    prices[ASSETS[1]] = np.r_[100, 100 * np.cumprod(1 + returns_a)]
    prices[ASSETS[2]] = np.r_[100, 100 * np.cumprod(1 + returns_b)]
    settings = config(weighting="inverse_vol", universe_mode="progressive", min_assets=10, top_n=2)
    signal = compute_signal(prices, settings)
    assert not row(signal, ASSETS[0])["available"]
    assert row(signal, ASSETS[1])["volatility"] == pytest.approx(np.std(returns_a, ddof=1))
    assert row(signal, ASSETS[2])["volatility"] == pytest.approx(np.std(returns_b, ddof=1))
    inverse = 1 / np.array([np.std(returns_a, ddof=1), np.std(returns_b, ddof=1)])
    assert signal["weights"][ASSETS[1]] == pytest.approx(inverse[0] / inverse.sum())
    assert not compute_signal(prices, config(weighting="inverse_vol"))["tradable"]


def test_previous_month_signal_controls_next_return_and_turnover_uses_drift():
    prices = monthly_frame(15)
    prices.loc[prices.index[13]:, ASSETS[1]] = 144.0
    result = run_backtest(market(prices), config(top_n=2, weighting="equal"), "2021-04-01")
    first, second = result["months"]
    assert first["signal_date"] == "2021-01-31"
    assert first["date"] == "2021-02-28"
    assert first["weights"] == pytest.approx({ASSETS[1]: .5, ASSETS[2]: .5})
    assert first["return"] == pytest.approx(.1)
    assert first["turnover"] == pytest.approx(1.0)
    assert second["weights"] == first["weights"]
    assert second["drifted_weights"][ASSETS[1]] == pytest.approx(.6 / 1.1)
    assert second["turnover"] == pytest.approx(.6 / 1.1 - .5)
    assert second["turnover"] > 0
    assert targets_from_backtest(result).index.tolist() == list(pd.to_datetime(["2021-01-31", "2021-02-28"]))


def test_6040_benchmark_drifts_between_rebalances_and_resets_each_january():
    prices = monthly_frame(25)
    prices["SWDA.MI"] = 100 * 1.01 ** np.arange(len(prices))
    result = run_backtest(market(prices), config(), "2022-02-01")
    rows = result["months"]
    assert rows[0]["benchmark_6040_weights"] == {"SWDA.MI": .6, "AGGH.MI": .4}
    assert rows[1]["benchmark_6040_weights"]["SWDA.MI"] > .6
    assert rows[1]["benchmark_6040_return"] > .006
    assert rows[-2]["benchmark_6040_weights"]["SWDA.MI"] > .6
    assert rows[-1]["date"] == "2022-01-31"
    assert rows[-1]["benchmark_6040_weights"] == {"SWDA.MI": .6, "AGGH.MI": .4}


def test_custom_dates_include_only_whole_months_and_preserve_warmup_history():
    prices = monthly_frame(17)
    result = run_backtest(market(prices), config(start_date="2021-02-22", end_date="2021-04-14"), "2021-06-01")
    assert result["period"] == {"start": "2021-03-01", "end": "2021-03-31", "months": 1}
    assert result["months"][0]["signal_date"] == "2021-02-28"
    assert result["live_performance"]["estimated_equity"] is None


def test_provisional_bucket_never_adds_a_month_or_changes_complete_metrics():
    complete = monthly_frame(15)
    full = complete.copy()
    full.loc[pd.Timestamp("2021-04-10")] = complete.iloc[-1] * 1.10
    baseline = run_backtest(market(complete), config(), "2021-04-20")
    result = run_backtest(market(full), config(), "2021-04-20")
    assert result["months"] == baseline["months"]
    assert result["metrics"] == baseline["metrics"]
    assert result["period"]["months"] == 2
    assert result["current_allocation"]["signal_date"] == "2021-03-31"
    assert result["provisional_signal"]["signal_date"] == "2021-04-10"
    assert result["provisional_signal"]["month_bucket"] == "2021-04-30"
    assert result["provisional_signal"]["provisional"]
    assert result["live_performance"]["return_since_month_end"] == pytest.approx(.1)
    assert result["live_performance"]["estimated_equity"] == pytest.approx(result["months"][-1]["equity"] * 1.1)


def test_stale_last_month_remains_provisional_and_does_not_enter_cagr():
    complete = monthly_frame(15)
    full = complete.copy()
    full.loc[pd.Timestamp("2021-04-14")] = complete.iloc[-1] * 1.05
    result = run_backtest(market(full), config(), "2021-05-01")
    assert result["period"]["end"] == "2021-03-31"
    assert result["period"]["months"] == 2
    assert result["provisional_signal"]["signal_date"] == "2021-04-14"
    assert any("chiusura finale XMIL" in warning for warning in result["warnings"])


def test_missing_held_return_after_investment_is_an_error_not_a_skipped_month():
    prices = monthly_frame(15)
    prices.loc[prices.index[-1], ASSETS[1]] = np.nan
    with pytest.raises(ValueError, match="Prezzi mancanti"):
        run_backtest(market(prices), config(), "2021-04-01")


def test_missing_signal_history_after_investment_cannot_be_silently_skipped():
    prices = monthly_frame(16)
    prices.loc[prices.index[14], ASSETS[-1]] = np.nan
    with pytest.raises(ValueError, match="Segnale non negoziabile"):
        run_backtest(market(prices), config(), "2021-05-01")


def test_no_tradeable_common_period_has_a_clear_error():
    with pytest.raises(ValueError, match="Storia insufficiente"):
        run_backtest(market(monthly_frame(13)), config(), "2021-02-01")
    with pytest.raises(ValueError, match="Nessun mese completo"):
        run_backtest(market(monthly_frame(15)), config(start_date="2030-01-01"), "2021-04-01")
