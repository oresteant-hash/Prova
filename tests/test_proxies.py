import numpy as np
import pandas as pd
import pytest

from app.proxies import extend_with_proxies


def series(values, dates):
    return pd.Series(values, index=pd.to_datetime(dates), name='test')


def metadata():
    return {'source': 'explicit test source', 'exposure': 'broad equity', 'currency': 'EUR',
            'hedging': 'unhedged', 'duration': None, 'distributions': 'reinvested total return',
            'assumptions': ['Analyst-provided exposure comparison.']}


def test_proxy_linking_preserves_returns_and_every_real_price():
    real = series([100, 110, 105], ['2023-03-31', '2023-04-30', '2023-05-31'])
    proxy = series([40, 44, 50, 54, 56], ['2023-01-31', '2023-02-28', '2023-03-31', '2023-04-30', '2023-05-31'])
    extended, report = extend_with_proxies(real, proxy, metadata())
    assert extended.tolist() == pytest.approx([80, 88, 100, 110, 105])
    pd.testing.assert_series_equal(extended.loc[real.index], real, check_dtype=False)
    assert extended.loc['2023-03-31'] / extended.loc['2023-02-28'] == pytest.approx(50 / 44)
    assert report['anchor_date'] == '2023-03-31'
    assert report['extended_observations'] == 2
    assert report['overlap_monthly_return_observations'] == 2
    assert report['assumptions'] == ['Analyst-provided exposure comparison.']


def test_proxy_does_not_fill_real_gaps_or_missing_months():
    real = series([100, np.nan, 110], ['2023-03-31', '2023-04-30', '2023-05-31'])
    proxy = series([40, 50, 54, 55], ['2023-01-31', '2023-03-31', '2023-04-30', '2023-05-31'])
    extended, report = extend_with_proxies(real, proxy, metadata())
    assert '2023-02-28' not in extended.index
    assert np.isnan(extended.loc['2023-04-30'])
    assert report['missing_months'] == ['2023-02-28', '2023-04-30']
    assert report['overlap_monthly_return_observations'] == 0


def test_future_overlap_is_not_used_to_link_past_history():
    real = series([100, 110], ['2023-03-31', '2023-04-30'])
    proxy = series([40, 54], ['2023-01-31', '2023-04-30'])
    with pytest.raises(ValueError, match='anchor futuro'):
        extend_with_proxies(real, proxy, metadata())


def test_proxy_metadata_is_not_mutated_and_currency_is_explicit():
    real = series([100], ['2023-03-31'])
    proxy = series([40, 50], ['2023-01-31', '2023-03-31'])
    supplied = metadata()
    extend_with_proxies(real, proxy, supplied)
    assert 'anchor_date' not in supplied
    supplied['currency'] = 'USD'
    with pytest.raises(ValueError, match='EUR'):
        extend_with_proxies(real, proxy, supplied)


@pytest.mark.parametrize('field', ['source', 'exposure', 'currency', 'hedging', 'duration', 'distributions'])
def test_all_proxy_characteristics_must_be_documented(field):
    real = series([100], ['2023-03-31'])
    proxy = series([40, 50], ['2023-01-31', '2023-03-31'])
    supplied = metadata()
    del supplied[field]
    with pytest.raises(ValueError, match='mancanti'):
        extend_with_proxies(real, proxy, supplied)


def test_observations_must_be_complete_months_and_unique():
    real = series([100], ['2023-03-20'])
    proxy = series([40, 50], ['2023-01-31', '2023-03-31'])
    with pytest.raises(ValueError, match='fine mese'):
        extend_with_proxies(real, proxy, metadata())
