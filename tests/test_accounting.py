from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.accounting import simulate_accounting


def market(rows, dates, dividends=None, splits=None):
    close = pd.DataFrame(rows, index=pd.to_datetime(dates), dtype=float)
    data = SimpleNamespace(close=close, adjusted=close * 17,
                           dividends=pd.DataFrame(0., index=close.index, columns=close.columns))
    if dividends:
        for date, ticker, amount in dividends:
            data.dividends.at[pd.Timestamp(date), ticker] = amount
    if splits:
        data.splits = pd.DataFrame(0., index=close.index, columns=close.columns)
        for date, ticker, amount in splits:
            data.splits.at[pd.Timestamp(date), ticker] = amount
    return data


def targets(rows, dates):
    return pd.DataFrame(rows, index=pd.to_datetime(dates), dtype=float).fillna(0)


def config(**overrides):
    return {"initial_capital": 100000, "tax_rate": .26, "commission_bps": 0,
            "spread_bps": 0, "rebalance_band": 0, **overrides}


def test_actual_prices_execution_and_comparable_final_tax():
    data = market({"A": [100, 110]}, ["2024-02-01", "2024-02-29"])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-01-31"]), config())
    first = result["trades"][0]
    assert first["date"] == "2024-02-01"
    assert first["price"] == 100
    assert first["quantity"] == 1000
    assert result["before_liquidation"] == pytest.approx(110000)
    assert result["after_liquidation"] == pytest.approx(107400)
    assert result["totals"]["taxes"] == 0
    assert result["final_liquidation_totals"]["taxes"] == pytest.approx(2600)
    assert result["monthly_returns"][0]["return"] == pytest.approx(.10)


def test_rebalancing_uses_position_drift_and_taxes_financed_by_capital():
    data = market({"A": [100, 200, 200], "B": [100, 100, 100]},
                  ["2024-02-01", "2024-03-01", "2024-03-29"])
    allocation = targets({"A": [.5, .5], "B": [.5, .5]}, ["2024-01-31", "2024-02-29"])
    result = simulate_accounting(data, allocation, config(), end_date="2024-03-31")
    second = [r for r in result["trades"] if r["date"] == "2024-03-01"]
    assert {t["ticker"]: t["side"] for t in second} == {"A": "SELL", "B": "BUY"}
    # Sell V_A - W/2; its average gain is 50%, hence tax=.13*(100000-W/2).
    expected_wealth = (150000 - 13000) / (1 - .065)
    assert result["before_liquidation"] == pytest.approx(expected_wealth)
    final = result["final_positions"]
    assert final["A"]["weight"] == pytest.approx(.5)
    assert final["B"]["weight"] == pytest.approx(.5)
    assert all(e["cash"] >= 0 for e in result["equity"])
    assert result["positions_before"][1]["positions"]["A"]["weight"] == pytest.approx(2 / 3)
    assert result["totals"]["taxes"] == pytest.approx(150000 - expected_wealth)


def test_exit_only_executes_partial_sale_but_defers_its_taxes():
    data = market({"A": [100, 200, 200], "B": [100, 100, 100]},
                  ["2024-02-01", "2024-03-01", "2024-03-29"])
    allocation = targets({"A": [.5, .5], "B": [.5, .5]}, ["2024-01-31", "2024-02-29"])
    result = simulate_accounting(data, allocation, config(), tax_mode="exit_only")
    sells = [t for t in result["trades"] if t["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["realized_gain"] == pytest.approx(12500)
    assert sells[0]["tax"] == 0
    assert result["before_liquidation"] == pytest.approx(150000)
    assert result["final_liquidation_totals"]["taxes"] == pytest.approx(9750)


def test_full_exit_taxes_only_gain_not_gross_and_losses_not_offset():
    data = market({"A": [100, 150, 150], "B": [100, 50, 50], "C": [100, 100, 100]},
                  ["2024-02-01", "2024-03-01", "2024-03-29"])
    allocation = targets({"A": [.5, 0], "B": [.5, 0], "C": [0, 1]},
                         ["2024-01-31", "2024-02-29"])
    result = simulate_accounting(data, allocation, config(), tax_mode="exit_only")
    sells = {t["ticker"]: t for t in result["trades"] if t["side"] == "SELL"}
    assert sells["A"]["tax"] == pytest.approx(6500)
    assert sells["B"]["tax"] == 0
    assert result["totals"]["realized_gains"] == pytest.approx(0)
    assert result["before_liquidation"] == pytest.approx(93500)
    assert result["final_positions"]["C"]["quantity"] == pytest.approx(935)


def test_costs_are_funded_and_capitalized_in_average_basis():
    data = market({"A": [100, 100]}, ["2024-02-01", "2024-02-29"])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-01-31"]),
                                 config(commission_bps=100))
    assert result["before_liquidation"] == pytest.approx(100000 / 1.01)
    assert result["final_positions"]["A"]["average_cost"] == pytest.approx(101)
    assert result["totals"]["costs"] == pytest.approx(100000 - 100000 / 1.01)
    assert result["final_liquidation_totals"]["taxes"] == 0
    assert result["after_liquidation"] == pytest.approx(100000 / 1.01 * .99)


def test_distribution_tax_and_reinvestment_on_next_rebalance_only():
    data = market({"A": [100, 99, 99, 99]},
                  ["2024-02-01", "2024-02-15", "2024-02-29", "2024-03-01"],
                  dividends=[("2024-02-15", "A", 1)])
    result = simulate_accounting(data, targets({"A": [1, 1]}, ["2024-01-31", "2024-02-29"]), config())
    assert result["distributions"][0]["gross"] == pytest.approx(1000)
    assert result["distributions"][0]["tax"] == pytest.approx(260)
    assert result["distributions"][0]["net"] == pytest.approx(740)
    assert next(e for e in result["equity"] if e["date"] == "2024-02-29")["cash"] == pytest.approx(740)
    buy = [t for t in result["trades"] if t["date"] == "2024-03-01"][0]
    assert buy["gross"] == pytest.approx(740)
    assert result["equity"][-1]["cash"] == 0
    assert result["totals"]["distribution_taxes"] == pytest.approx(260)


def test_dividend_same_day_as_entry_does_not_accrue_to_new_position():
    data = market({"A": [100, 100]}, ["2024-02-01", "2024-02-29"],
                  dividends=[("2024-02-01", "A", 10)])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-01-31"]), config())
    assert result["distributions"] == []


def test_split_preserves_value_and_basis_then_applies_distribution():
    data = market({"A": [100, 50, 50]}, ["2024-02-01", "2024-02-15", "2024-02-29"],
                  splits=[("2024-02-15", "A", 2)], dividends=[("2024-02-15", "A", 1)])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-01-31"]), config())
    assert result["split_events"][0]["quantity_after"] == 2000
    assert result["final_positions"]["A"]["average_cost"] == 50
    assert result["distributions"][0]["gross"] == 2000
    assert result["before_liquidation"] == pytest.approx(101480)


def test_band_freezes_small_drift_without_creating_unfunded_orders():
    data = market({"A": [100, 101, 101], "B": [100, 100, 100]},
                  ["2024-02-01", "2024-03-01", "2024-03-29"])
    allocation = targets({"A": [.5, .5], "B": [.5, .5]}, ["2024-01-31", "2024-02-29"])
    result = simulate_accounting(data, allocation, config(rebalance_band=.005))
    assert len(result["trades"]) == 2
    assert result["final_positions"]["A"]["quantity"] == 500


def test_band_residual_rescaling_and_nonnegative_cash_with_three_assets():
    data = market({"A": [100, 100, 100], "B": [100, 102, 102], "C": [100, 98, 98]},
                  ["2024-02-01", "2024-03-01", "2024-03-29"])
    allocation = targets({"A": [.5, .5], "B": [.25, .30], "C": [.25, .20]},
                         ["2024-01-31", "2024-02-29"])
    result = simulate_accounting(data, allocation,
                                 config(rebalance_band=.005, commission_bps=30))
    final = result["final_positions"]
    assert final["B"]["market_value"] / final["C"]["market_value"] == pytest.approx(1.5)
    assert all(e["cash"] >= 0 for e in result["equity"])


def test_incomplete_month_not_counted_even_when_requested_end_is_future():
    data = market({"A": [100, 110, 120]}, ["2024-02-01", "2024-02-29", "2024-03-14"])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-01-31"]), config(), end_date="2024-03-31")
    assert [m["date"] for m in result["monthly_returns"]] == ["2024-02-29"]
    assert result["before_liquidation"] == pytest.approx(120000)


@pytest.mark.parametrize("signal,first,last,month_end", [
    ("2024-11-30", "2024-12-02", "2024-12-30", "2024-12-31"),
    ("2024-02-29", "2024-03-01", "2024-03-28", "2024-03-31"),
])
def test_complete_month_with_milan_final_holiday_is_certified(signal, first, last, month_end):
    data = market({"A": [100, 110]}, [first, last])
    result = simulate_accounting(data, targets({"A": [1]}, [signal]), config(), end_date=month_end)
    assert [r["date"] for r in result["monthly_returns"]] == [month_end]
    assert result["monthly_returns"][0]["return"] == pytest.approx(.1)
    assert result["period"]["end"] == last
    assert result["final_liquidation"][0]["date"] == last


def test_milan_final_session_does_not_finalize_month_before_requested_calendar_end():
    data = market({"A": [100, 110]}, ["2024-12-02", "2024-12-30"])
    result = simulate_accounting(data, targets({"A": [1]}, ["2024-11-30"]), config(), end_date="2024-12-30")
    assert result["monthly_returns"] == []


def test_missing_execution_quote_is_explicit_instead_of_lookahead_delay():
    data = market({"A": [100, 100], "B": [np.nan, 100]}, ["2024-02-01", "2024-02-02"])
    with pytest.raises(ValueError, match="mancanti nella seduta"):
        simulate_accounting(data, targets({"A": [.5], "B": [.5]}, ["2024-01-31"]), config())


@pytest.mark.parametrize("mode", ["all_sales", "exit_only"])
def test_ledger_reconciles_cash_and_final_liquidation_over_many_rebalances(mode):
    random = np.random.default_rng(72)
    names = ["A", "B", "C", "D"]
    months = pd.date_range("2024-02-29", periods=10, freq="ME")
    dates, prices = [], []
    levels = np.full(4, 100.)
    for month in months:
        for day in [month.replace(day=1) + pd.offsets.BDay(0), month - pd.offsets.BDay(0)]:
            # BDay(0) rolls a weekend forward; explicitly take last business day.
            day = day if day.month == month.month else month - pd.offsets.BDay(1)
            dates.append(day)
            levels *= 1 + random.uniform(-.08, .13, size=4)
            prices.append(levels.copy())
    close = pd.DataFrame(prices, index=dates, columns=names)
    dividends = pd.DataFrame(0., index=dates, columns=names)
    dividends.iloc[1::2, 0] = .8
    data = SimpleNamespace(close=close, adjusted=close * 15, dividends=dividends)
    raw_weights = random.uniform(.1, 1, size=(10, 4))
    raw_weights[::3, 0] = 0
    raw_weights /= raw_weights.sum(axis=1, keepdims=True)
    allocation = pd.DataFrame(raw_weights, columns=names,
                              index=pd.date_range("2024-01-31", periods=10, freq="ME"))
    result = simulate_accounting(data, allocation,
                                 config(commission_bps=12, spread_bps=8, rebalance_band=.004),
                                 tax_mode=mode, end_date="2024-11-30")
    cash = 100000 + sum(d["net"] for d in result["distributions"])
    for t in result["trades"]:
        cash += (t["gross"] - t["cost"] - t["tax"]) if t["side"] == "SELL" else -t["gross"] - t["cost"]
        assert t["cash_after"] >= 0
    assert cash == pytest.approx(result["equity"][-1]["cash"], abs=1e-7)
    value = cash + sum(p["market_value"] for p in result["final_positions"].values())
    assert value == pytest.approx(result["before_liquidation"])
    liquidation = result["final_liquidation_totals"]
    assert result["after_liquidation"] == pytest.approx(value - liquidation["costs"] - liquidation["taxes"])
    assert result["totals"]["taxes"] == pytest.approx(
        sum(d["tax"] for d in result["distributions"]) + sum(t["tax"] for t in result["trades"]))
    assert all(p["quantity"] >= 0 and p["average_cost"] >= 0 for p in result["final_positions"].values())


@pytest.mark.parametrize("allocation", [
    {"A": [.9]}, {"A": [1.1], "B": [-.1]}, {"A": [np.nan]},
])
def test_invalid_weights_rejected(allocation):
    data = market({"A": [100], "B": [100]}, ["2024-02-01"])
    # Don't normalize these deliberately invalid inputs.
    frame = pd.DataFrame(allocation, index=pd.to_datetime(["2024-01-31"]))
    with pytest.raises(ValueError):
        simulate_accounting(data, frame, config())
