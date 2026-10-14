"""Local research API: explicit data sources, immutable analyses, downloadable audits."""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import threading
import uuid
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .accounting import simulate_accounting
from .analytics import account_performance, metrics
from .data import instrument_catalog, load_cached, load_demo, refresh_yahoo
from .engine import monthly_prices, run_backtest
from .models import AccountRequest, BacktestRequest, ProxyRequest, StrategyConfig
from .proxies import extend_with_proxies

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'
app = FastAPI(title='Momentum Atelier', version='1.0.0', description='Ricerca momentum in EUR, senza esecuzione di ordini reali.')
app.mount('/static', StaticFiles(directory=STATIC), name='static')
_ANALYSES: OrderedDict[str, tuple[dict, object]] = OrderedDict()
_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE_LOCK = threading.Lock()
_REFRESH_JOBS: OrderedDict[str, dict] = OrderedDict()
_CALC_LOCK = threading.Semaphore(2)
log = logging.getLogger(__name__)


def clean(value):
    """Strict JSON: missing financial numbers remain null, never NaN or Infinity."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(x) for x in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def defaults():
    return StrategyConfig.model_validate_json((ROOT / 'config' / 'default.json').read_text()).model_dump()


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '1.0.0', 'timezone': 'Europe/Rome'}


@app.get('/api/catalog')
def catalog():
    return {'instruments': instrument_catalog(), 'defaults': defaults(), 'providers': [
        {'id': 'demo', 'label': 'Dati sintetici dimostrativi', 'verified': False},
        {'id': 'yahoo', 'label': 'Yahoo Finance / yfinance', 'verified': 'per-download'},
    ]}


@app.get('/api/data/status')
def data_status():
    try:
        cached = load_cached()
    except (ValueError, OSError, KeyError) as exc:
        return {'cached': False, 'source': None, 'error': str(exc), 'demo_available': True}
    if cached is None:
        return {'cached': False, 'source': None, 'updated_at': None, 'demo_available': True,
                'message': 'Nessuna quotazione Yahoo verificata in cache. La demo è sintetica.'}
    return clean({'cached': True, 'source': cached.source, 'updated_at': cached.updated_at,
                  'metadata': cached.metadata, 'warnings': cached.warnings, 'demo_available': True})


def _run_refresh(identifier: str):
    try:
        data = refresh_yahoo()
        result = clean({'source': data.source, 'updated_at': data.updated_at,
                        'metadata': data.metadata, 'warnings': data.warnings,
                        'observations': len(data.adjusted), 'tickers': list(data.adjusted.columns)})
        with _REFRESH_STATE_LOCK:
            _REFRESH_JOBS[identifier].update(status='updated', result=result,
                finished_at=datetime.now(ZoneInfo('Europe/Rome')).isoformat())
    except Exception as exc:
        log.warning('Yahoo refresh failed: %s', type(exc).__name__)
        with _REFRESH_STATE_LOCK:
            _REFRESH_JOBS[identifier].update(status='failed',
                error=f'Aggiornamento Yahoo non riuscito: {exc}. Nessun dato demo è stato sostituito ai dati reali.',
                finished_at=datetime.now(ZoneInfo('Europe/Rome')).isoformat())
    finally:
        _REFRESH_LOCK.release()


@app.post('/api/data/refresh', status_code=202)
def refresh():
    """Start a background download; web proxies need not hold a long request."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Un aggiornamento Yahoo è già in corso.')
    identifier = uuid.uuid4().hex
    job = {'job_id': identifier, 'status': 'running',
           'started_at': datetime.now(ZoneInfo('Europe/Rome')).isoformat(),
           'finished_at': None, 'result': None, 'error': None}
    with _REFRESH_STATE_LOCK:
        _REFRESH_JOBS[identifier] = job
        while len(_REFRESH_JOBS) > 8:
            _REFRESH_JOBS.popitem(last=False)
    try:
        threading.Thread(target=_run_refresh, args=(identifier,), daemon=True,
                         name='yahoo-refresh').start()
    except RuntimeError as exc:
        _REFRESH_LOCK.release()
        with _REFRESH_STATE_LOCK:
            _REFRESH_JOBS[identifier].update(status='failed', error='Impossibile avviare lo scaricamento.')
        raise HTTPException(503, 'Impossibile avviare lo scaricamento. Riprova.') from exc
    return {**job}


@app.get('/api/data/refresh/status')
def refresh_status(job_id: str | None = None):
    with _REFRESH_STATE_LOCK:
        if not _REFRESH_JOBS and job_id is None:
            return {'status': 'idle', 'job_id': None}
        identifier = job_id or next(reversed(_REFRESH_JOBS))
        job = _REFRESH_JOBS.get(identifier)
        if job is None:
            raise HTTPException(404, 'Aggiornamento non presente in memoria: verifica la cache o avviane uno nuovo.')
        return {**job}


def _accounting_targets(result):
    entries = {pd.Timestamp(row['signal_date']): row['weights'] for row in result['months']}
    if not entries:
        raise ValueError('Nessun mese completo simulabile nel periodo richiesto.')
    targets = pd.DataFrame.from_dict(entries, orient='index').fillna(0).sort_index()
    return targets


def _benchmark_targets(targets, balanced=False):
    first = targets.index[0]
    dates = [first]
    if balanced:
        # January returns rebalance on the session after the preceding December signal.
        dates += [date for date in targets.index[1:] if date.month == 12]
        return pd.DataFrame({'SWDA.MI': .6, 'AGGH.MI': .4}, index=dates)
    return pd.DataFrame({'SWDA.MI': 1.}, index=dates)


def _comparison(key, name, acc, capital):
    m = metrics([r['return'] for r in acc['monthly_returns']], capital)
    totals = acc['totals']
    m.update({'key': key, 'name': name, 'before_liquidation': acc['before_liquidation'],
              'after_liquidation': acc['after_liquidation'], 'total_costs': totals['costs'],
              'total_taxes': totals['taxes'], 'costs': totals['costs'], 'taxes': totals['taxes'],
              'turnover': totals['turnover'], 'convention': 'operational_daily',
              'measurement_frequency': 'monthly',
              'liquidation_costs': acc.get('final_liquidation_totals', {}).get('costs', 0),
              'liquidation_taxes': acc.get('final_liquidation_totals', {}).get('taxes', 0)})
    return m


@app.post('/api/backtest')
def backtest(request: BacktestRequest):
    if not _CALC_LOCK.acquire(blocking=False):
        raise HTTPException(429, 'Due analisi sono già in corso. Attendi il completamento e riprova.')
    try:
        data = load_demo() if request.source == 'demo' else load_cached()
        if data is None:
            raise HTTPException(409, 'Scarica e verifica i dati Yahoo prima di eseguire un backtest reale.')
        if request.source == 'yahoo' and data.source != 'yahoo':
            raise HTTPException(409, 'La cache disponibile non contiene dati Yahoo verificati.')
        asof = pd.Timestamp(datetime.now(ZoneInfo('Europe/Rome')).date())
        result = run_backtest(data, request.config, asof=asof)
        targets = _accounting_targets(result)
        end = result['period']['end']
        standard = simulate_accounting(data, targets, request.config, tax_mode='exit_only', end_date=end)
        score = simulate_accounting(data, targets, request.config, tax_mode='all_sales', end_date=end)
        world = simulate_accounting(data, _benchmark_targets(targets), request.config, tax_mode='all_sales', end_date=end)
        balanced = simulate_accounting(data, _benchmark_targets(targets, True), request.config, tax_mode='all_sales', end_date=end)
        gross_metric = dict(result['metrics']['strategy'])
        last_equity = result['months'][-1]['equity']
        gross_metric.update({'key': 'gross', 'name': 'Momentum lordo · mensile teorico',
                             'before_liquidation': last_equity, 'after_liquidation': last_equity,
                             'costs': 0, 'taxes': 0, 'total_costs': 0, 'total_taxes': 0,
                             'convention': 'theoretical_month_end', 'measurement_frequency': 'monthly'})
        comparison = [gross_metric,
            _comparison('standard', 'Momentum 1.0 standard · imposte solo uscite complete', standard, request.config.initial_capital),
            _comparison('score', 'Momentum Score · imposte anche vendite parziali', score, request.config.initial_capital),
            _comparison('world', 'MSCI World · operativo con imposte', world, request.config.initial_capital),
            _comparison('balanced', '60/40 · operativo con imposte', balanced, request.config.initial_capital)]
        identifier = uuid.uuid4().hex
        result.update({'analysis_id': identifier, 'source': data.source, 'updated_at': data.updated_at,
                       'metadata': data.metadata, 'config': request.config.model_dump(),
                       'accounting': {'standard': standard, 'score': score},
                       'accounting_benchmarks': {'world': world, 'balanced': balanced},
                       'comparison': comparison, 'catalog': instrument_catalog(),
                       'created_at': datetime.now(ZoneInfo('Europe/Rome')).isoformat()})
        result['warnings'] = list(dict.fromkeys([*data.warnings, *result.get('warnings', []),
            'Backtest mensile ideale: target eseguiti alla chiusura del mese del segnale, senza ritardo.',
            'Contabilità operativa: chiusura della prima seduta successiva al segnale; quantità frazionarie. Le curve hanno prezzi e tempi di esecuzione diversi.',
            'Momentum 1.0 standard ribilancia anche parzialmente, ma omette le imposte sulle vendite parziali: convenzione fiscale semplificata.',
            'Modello fiscale convenzionale: aliquota unica, nessuna compensazione minusvalenze; non ricostruisce la fiscalità italiana per singolo ETF/ETC.',
            'Il confronto operativo usa lo stesso periodo e le stesse regole di costi, distribuzioni e liquidazione finale per strategia e benchmark.',
            'Volatilità e drawdown delle metriche principali sono mensili. I risultati demo non descrivono il rendimento reale degli strumenti.',
            *standard.get('warnings', []), *score.get('warnings', [])]))
        result = clean(result)
        with _LOCK:
            _ANALYSES[identifier] = (result, data)
            while len(_ANALYSES) > 8:
                _ANALYSES.popitem(last=False)
        return result
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        _CALC_LOCK.release()


def _write_csv(rows):
    if not rows:
        return ''
    fields = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        values = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(clean(value), ensure_ascii=False)
            elif isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
                value = "'" + value  # keep external text inert when opened in a spreadsheet
            values[key] = value
        writer.writerow(values)
    return stream.getvalue()


@app.get('/api/export/{analysis_id}')
def export_analysis(analysis_id: str):
    with _LOCK:
        stored = _ANALYSES.get(analysis_id)
    if stored is None:
        raise HTTPException(404, 'Analisi non presente in memoria. Esegui nuovamente il backtest e scarica il nuovo archivio.')
    result, data = stored
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        def js(name, value):
            archive.writestr(name, json.dumps(clean(value), indent=2, ensure_ascii=False))
        def rows(name, values):
            archive.writestr(name, _write_csv(values))
        js('configuration.json', result['config'])
        js('catalog.json', result['catalog'])
        js('provenance.json', {key: result[key] for key in ['analysis_id', 'created_at', 'source', 'updated_at', 'metadata', 'period', 'warnings']})
        js('results.json', result)
        for field in ('adjusted', 'close', 'dividends', 'splits'):
            frame = getattr(data, field, None)
            if frame is not None:
                archive.writestr(f'data/{field}_daily.csv', frame.to_csv(index_label='date'))
        asof = pd.Timestamp(result['created_at']).tz_localize(None)
        archive.writestr('data/adjusted_complete_months.csv', monthly_prices(data.adjusted, asof=asof).to_csv(index_label='date'))
        rows('monthly_returns.csv', result['months'])
        rows('comparison.csv', result['comparison'])
        flattened = []
        weights = []
        for signal in result['signals']:
            flattened += [{'signal_date': signal['signal_date'], **row} for row in signal['rows']]
            weights += [{'signal_date': signal['signal_date'], 'ticker': ticker, 'weight': weight} for ticker, weight in signal['weights'].items()]
        rows('scores_rankings.csv', flattened)
        rows('target_weights.csv', weights)
        js('current_allocation.json', result['current_allocation'])
        js('provisional_signal.json', result['provisional_signal'])
        for group, values in [('strategy', result['accounting']), ('benchmarks', result['accounting_benchmarks'])]:
            for name, acc in values.items():
                prefix = f'accounting/{group}_{name}/'
                for table in ('equity', 'monthly_returns', 'trades', 'distributions', 'positions_before', 'final_liquidation', 'split_events'):
                    rows(prefix + table + '.csv', acc.get(table, []))
                js(prefix + 'summary.json', {k: acc[k] for k in ['totals', 'before_liquidation', 'after_liquidation', 'warnings'] if k in acc})
        archive.writestr('LEGGIMI.txt',
            'Momentum Atelier — archivio verificabile\nValuta EUR. Source=demo significa prezzi sintetici, NON dati di mercato.\n'
            'Le colonne close sono prezzi negoziabili; adjusted è una serie total return, non un prezzo di acquisto.\n'
            'Le operazioni contabili usano la prima chiusura successiva al segnale. Il modello mensile lordo usa la chiusura teorica del segnale.\n'
            'Posizioni precedenti, costi, tasse e liquidazione finale sono esportati separatamente.\n'
            'Imposte sulle distribuzioni in entrambi i modelli, plusvalenze parziali omesse solo in standard.\n'
            'Mesi incompleti esclusi da rendimenti mensili e CAGR. Nessun proxy introdotto automaticamente.\n')
    return Response(stream.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="momentum-{analysis_id[:8]}.zip"',
        'Cache-Control': 'no-store'})


@app.post('/api/account-performance')
def actual_account(request: AccountRequest):
    try:
        return clean(account_performance([v.model_dump() for v in request.valuations]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/proxy-analysis')
def proxy_analysis(request: ProxyRequest):
    try:
        def series(points):
            dates = pd.to_datetime([point.date for point in points], errors='raise')
            if dates.has_duplicates or not dates.is_monotonic_increasing:
                raise ValueError('Date dei prezzi uniche e in ordine crescente obbligatorie.')
            return pd.Series([point.price for point in points], index=dates, dtype=float)
        prices, metadata = extend_with_proxies(series(request.real), series(request.proxy), request.metadata)
        return clean({'series': [{'date': date.strftime('%Y-%m-%d'), 'price': value} for date, value in prices.items()],
                      'metadata': metadata, 'analysis_type': 'separate_proxy_extension',
                      'warning': 'Analisi proxy separata: non modifica dati Yahoo, strategia base o anagrafica.'})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
