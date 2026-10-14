"""Functional API and audit-export checks (no outbound Yahoo calls)."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(scope='module')
def analysis():
    response = client.post('/api/backtest', json={'source':'demo', 'config':{}})
    assert response.status_code == 200, response.text
    return response.json()


def test_application_serves_interface_and_health():
    assert client.get('/').status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
    catalog = client.get('/api/catalog').json()
    assert len(catalog['instruments']) == 14
    assert catalog['defaults']['top_n'] == 6
    assert catalog['defaults']['weighting'] == 'score'
    assert catalog['defaults']['ma_filter'] is False


def test_default_analysis_has_complete_months_and_accounting(analysis):
    assert analysis['source'] == 'demo'
    assert analysis['period']['months'] == len(analysis['months']) > 24
    assert len(analysis['accounting']['score']['trades']) > 0
    for month in analysis['months']:
        assert month['signal_date'] < month['date']
        assert sum(month['weights'].values()) == pytest.approx(1)
    for key in ('standard', 'score'):
        result = analysis['accounting'][key]
        assert result['before_liquidation'] >= result['after_liquidation']
        assert result['totals']['taxes'] > 0
        assert all(trade['date'] > trade['signal_date'] for trade in result['trades'])
        assert all(row['cash'] >= -1e-6 for row in result['equity'])
    assert len(analysis['comparison']) == 5
    assert analysis['warnings']


def test_audit_export_contains_actual_used_data_and_full_ledger(analysis):
    response = client.get('/api/export/' + analysis['analysis_id'])
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        for expected in ('configuration.json', 'catalog.json', 'provenance.json',
                         'data/close_daily.csv', 'data/adjusted_daily.csv', 'scores_rankings.csv',
                         'target_weights.csv', 'accounting/strategy_score/trades.csv',
                         'accounting/strategy_score/positions_before.csv', 'monthly_returns.csv'):
            assert expected in names
        assert json.loads(archive.read('configuration.json')) == analysis['config']
        assert json.loads(archive.read('provenance.json'))['source'] == 'demo'
        assert archive.read('accounting/strategy_score/trades.csv').count(b'\n') > 20


def test_invalid_and_unavailable_requests_are_explicit(monkeypatch):
    assert client.post('/api/backtest', json={'config':{'top_n':1}}).status_code == 422
    assert client.post('/api/backtest', json={'config':{'tax_rate':1.1}}).status_code == 422
    assert client.post('/api/backtest', json={'config':{'end_date':'2020-13-01'}}).status_code == 422
    monkeypatch.setattr('app.main.load_cached', lambda: None)
    response = client.post('/api/backtest', json={'source':'yahoo'})
    assert response.status_code == 409
    assert 'Yahoo' in response.json()['detail']
    assert client.get('/api/export/nonexistent').status_code == 404


def test_account_flow_conventions():
    result = client.post('/api/account-performance', json={'valuations':[
        {'date':'2024-01-01','value':10000,'external_flow':0},
        {'date':'2024-07-01','value':16000,'external_flow':5000},
        {'date':'2025-01-01','value':17600,'external_flow':0},
    ]})
    assert result.status_code == 200, result.text
    assert result.json()['twr'] == pytest.approx(.21)
    assert result.json()['xirr'] > 0


def test_proxy_analysis_is_separate_and_preserves_real_prices():
    result = client.post('/api/proxy-analysis', json={
        'real':[{'date':'2022-01-31','price':200},{'date':'2022-02-28','price':220}],
        'proxy':[{'date':'2021-12-31','price':90},{'date':'2022-01-31','price':100},{'date':'2022-02-28','price':105}],
        'metadata':{'source':'user','exposure':'global equity','currency':'EUR','hedging':'none',
                    'duration':'not applicable','distributions':'total return reinvested'},
    })
    assert result.status_code == 200, result.text
    assert result.json()['analysis_type'] == 'separate_proxy_extension'
    assert result.json()['series'][-2:] == [
        {'date':'2022-01-31','price':200}, {'date':'2022-02-28','price':220}]


def test_actual_account_can_end_with_full_withdrawal():
    result = client.post('/api/account-performance', json={'valuations':[
        {'date':'2023-01-01','value':100,'external_flow':0},
        {'date':'2024-01-01','value':0,'external_flow':-110},
    ]})
    assert result.status_code == 200, result.text
    assert result.json()['twr'] == pytest.approx(.1)
    assert result.json()['xirr'] == pytest.approx(.1)


def _wait_refresh(job_id):
    import time
    for _ in range(200):
        response = client.get('/api/data/refresh/status', params={'job_id': job_id})
        assert response.status_code == 200
        status = response.json()
        if status['status'] != 'running':
            return status
        time.sleep(.01)
    raise AssertionError('Background refresh did not finish')


def test_refresh_is_background_and_serializes_provider_downloads(monkeypatch):
    from dataclasses import replace
    import threading
    from app.data import load_demo
    release = threading.Event()
    fixture = replace(load_demo(), source='yahoo')
    def download():
        assert release.wait(timeout=3)
        return fixture
    monkeypatch.setattr('app.main.refresh_yahoo', download)
    try:
        response = client.post('/api/data/refresh')
        assert response.status_code == 202
        job = response.json()
        assert job['status'] == 'running'
        assert client.get('/api/data/refresh/status', params={'job_id':job['job_id']}).json()['status'] == 'running'
        assert client.post('/api/data/refresh').status_code == 409
    finally:
        release.set()
    result = _wait_refresh(job['job_id'])
    assert result['status'] == 'updated'
    assert result['result']['source'] == 'yahoo'
    assert len(result['result']['tickers']) == 14
    assert result['finished_at']


def test_background_refresh_failure_is_explicit_and_preserves_source(monkeypatch):
    def unavailable():
        raise ValueError('provider offline')
    monkeypatch.setattr('app.main.refresh_yahoo', unavailable)
    response = client.post('/api/data/refresh')
    assert response.status_code == 202
    result = _wait_refresh(response.json()['job_id'])
    assert result['status'] == 'failed'
    assert 'provider offline' in result['error']
    assert result['result'] is None
    assert client.get('/api/data/refresh/status', params={'job_id':'missing'}).status_code == 404
