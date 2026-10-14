import math

import numpy as np
import pandas as pd
import pytest

from app.analytics import account_performance, daily_metrics, metrics, xirr


def test_monthly_metrics_include_initial_drawdown_and_integer_month_cagr():
    result = metrics([-.2, .1], 100, turnover=[.2, .3])
    assert result['cumulative_return'] == pytest.approx(-.12)
    assert result['cagr'] == pytest.approx(.88 ** 6 - 1)
    assert result['annualized_volatility'] == pytest.approx(np.std([-.2, .1], ddof=1) * math.sqrt(12))
    assert result['max_drawdown'] == pytest.approx(-.2)
    assert result['turnover'] == pytest.approx(.5)
    assert result['months'] == 2


def test_monthly_empty_and_single_observation_do_not_invent_sample_volatility():
    assert metrics([], 100)['cagr'] is None
    assert metrics([], 100)['max_drawdown'] == 0
    assert metrics([.01], 100)['annualized_volatility'] is None
    assert metrics([-1], 100)['cagr'] == -1


@pytest.mark.parametrize('returns', [[float('nan')], [float('inf')], [-1.01]])
def test_invalid_monthly_data_rejected(returns):
    with pytest.raises(ValueError):
        metrics(returns, 100)


def test_daily_accounting_uses_calendar_time_instead_of_month_counts():
    equity = pd.Series([99, 110], index=pd.to_datetime(['2023-01-02', '2024-01-01']))
    result = daily_metrics(equity, 100, start_date='2023-01-01')
    assert result['cagr'] == pytest.approx(1.1 ** (365.25 / 365) - 1)
    assert result['max_drawdown'] == pytest.approx(-.01)
    assert result['calendar_days'] == 365
    assert result['return_frequency'] == 'daily_trading_sessions'


def test_xirr_day_count_and_same_day_netting():
    assert xirr([
        {'date': '2023-01-01', 'amount': -100},
        {'date': '2024-01-01', 'amount': 120},
        {'date': '2024-01-01', 'amount': -10},
    ]) == pytest.approx(.1)
    assert xirr([
        {'date': '2023-01-01', 'amount': -100},
        {'date': '2023-07-02', 'amount': 110},
    ]) == pytest.approx(1.1 ** (365 / 182) - 1)


def test_xirr_finds_negative_and_large_positive_rates():
    assert xirr([{'date': '2023-01-01', 'amount': -100}, {'date': '2024-01-01', 'amount': 10}]) == pytest.approx(-.9)
    assert xirr([{'date': '2023-01-01', 'amount': -100}, {'date': '2024-01-01', 'amount': 100000}]) == pytest.approx(999)


def test_xirr_does_not_choose_arbitrary_root_in_ambiguous_case():
    with pytest.raises(ValueError, match='più radici'):
        xirr([
            {'date': '2023-01-01', 'amount': -100},
            {'date': '2024-01-01', 'amount': 230},
            {'date': '2024-12-31', 'amount': -132},
        ])


def test_xirr_tangent_root_and_no_root_are_distinguished():
    assert xirr([
        {'date': '2023-01-01', 'amount': -100},
        {'date': '2024-01-01', 'amount': 200},
        {'date': '2024-12-31', 'amount': -100},
    ]) == pytest.approx(0)
    with pytest.raises(ValueError, match='non esiste'):
        xirr([
            {'date': '2023-01-01', 'amount': -100},
            {'date': '2024-01-01', 'amount': 210},
            {'date': '2024-12-31', 'amount': -120},
        ])


@pytest.mark.parametrize('flows', [
    [],
    [{'date': '2023-01-01', 'amount': -100}, {'date': '2023-01-01', 'amount': 110}],
    [{'date': '2023-01-01', 'amount': -100}, {'date': '2024-01-01', 'amount': -110}],
    [{'date': 'invalid', 'amount': -100}, {'date': '2024-01-01', 'amount': 110}],
    [{'date': '2023-01-01', 'amount': -100}, {'date': '2024-01-01', 'amount': float('nan')}],
])
def test_xirr_invalid_flows_rejected(flows):
    with pytest.raises(ValueError):
        xirr(flows)


def test_account_twr_contributions_and_withdrawals_are_not_returns():
    result = account_performance([
        {'date': '2023-01-01', 'value': 100, 'external_flow': 100},
        {'date': '2024-01-01', 'value': 210, 'external_flow': 100},
        {'date': '2024-12-31', 'value': 211, 'external_flow': -20},
    ])
    assert result['twr'] == pytest.approx(.21)
    assert result['xirr'] == pytest.approx(.1)
    assert result['net_profit'] == pytest.approx(31)
    assert result['net_external_flows'] == pytest.approx(80)
    # Opening contribution is represented by initial value exactly once.
    assert result['investor_flows'][0]['amount'] == -100
    assert len([flow for flow in result['investor_flows'] if flow['date'] == '2023-01-01']) == 1


def test_full_withdrawal_has_zero_twr_and_defined_xirr():
    result = account_performance([
        {'date': '2023-01-01', 'value': 100, 'external_flow': 100},
        {'date': '2024-01-01', 'value': 0, 'external_flow': -110},
    ])
    assert result['twr'] == pytest.approx(.1)
    assert result['xirr'] == pytest.approx(.1)


def test_total_loss_is_valid_twr_with_undefined_supported_xirr():
    result = account_performance([
        {'date': '2023-01-01', 'value': 100, 'external_flow': 0},
        {'date': '2024-01-01', 'value': 0, 'external_flow': 0},
    ])
    assert result['twr'] == -1
    assert result['xirr'] is None
    assert result['xirr_error']


@pytest.mark.parametrize('rows', [
    [{'date': '2023-01-01', 'value': 100}, {'date': '2024-01-01', 'value': 110, 'external_flow': 0}],
    [{'date': '2024-01-01', 'value': 100, 'external_flow': 0}, {'date': '2023-01-01', 'value': 110, 'external_flow': 0}],
    [{'date': '2023-01-01', 'value': 100, 'external_flow': 0}, {'date': '2023-01-01', 'value': 110, 'external_flow': 0}],
    [{'date': '2023-01-01', 'value': 100, 'external_flow': 0}, {'date': '2024-01-01', 'value': 50, 'external_flow': 100}],
])
def test_actual_account_missing_or_inconsistent_values_rejected(rows):
    with pytest.raises(ValueError):
        account_performance(rows)
