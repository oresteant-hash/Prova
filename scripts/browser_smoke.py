"""Run the live application's main UI flows; start scripts/start.sh first.

Usage: .venv/bin/python scripts/browser_smoke.py
Requires Playwright (uv dev group) and Chromium; BROWSER_EXECUTABLE can select it.
"""
import json
import os
from pathlib import Path
import zipfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
ARTIFACTS.mkdir(exist_ok=True)
base_url = os.environ.get('MOMENTUM_BASE_URL', 'http://127.0.0.1:8000')
with sync_playwright() as browser_tool:
    browser = browser_tool.chromium.launch(executable_path=os.environ.get('BROWSER_EXECUTABLE', '/usr/bin/chromium'), headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1000}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base_url, wait_until='networkidle')
    page.locator('#metrics-grid .metric-card').first.wait_for()
    assert 'DEMO' in page.locator('#source-badge').inner_text()
    assert page.locator('#metrics-grid .metric-card').count() == 4
    assert page.locator('#equity-chart svg path').count() >= 3
    page.screenshot(path=str(ARTIFACTS / 'dashboard-desktop.png'), full_page=True)

    # Recalculate actual configuration, not just the labels.
    page.locator('#config-button').click()
    page.locator('[name=top_n]').select_option('4')
    page.locator('[name=formula]').select_option('reactive')
    page.locator('[name=weighting]').select_option('inverse_vol')
    page.locator('[name=ma_filter]').check()
    page.locator('[name=commission_bps]').fill('5')
    with page.expect_response(lambda r: r.url.endswith('/api/backtest') and r.request.method == 'POST') as recalc:
        page.locator('#config-form button[type=submit]').click()
    response = recalc.value
    assert response.status == 200, response.text()
    analysis = response.json()
    assert analysis['config']['top_n'] == 4
    assert analysis['config']['weighting'] == 'inverse_vol'
    assert analysis['accounting']['score']['totals']['costs'] > 0
    page.locator('#loading').wait_for(state='hidden')
    assert 'Top 4' in page.locator('#strategy-chips').inner_text()
    with page.expect_download() as download:
        page.locator('#export-button').click()
    archive_path = ARTIFACTS / 'browser-analysis.zip'
    download.value.save_as(str(archive_path))
    with zipfile.ZipFile(archive_path) as archive:
        assert json.loads(archive.read('configuration.json'))['top_n'] == 4
        assert 'accounting/strategy_score/trades.csv' in archive.namelist()

    page.locator('.nav-item[data-view=allocation]').click()
    assert page.locator('#signal-rows tr').count() == 12
    page.locator('#signal-select').select_option('provisional')
    assert 'PROVVISORIO' in page.locator('#signal-context').inner_text()
    page.locator('.nav-item[data-view=accounting]').click()
    assert page.locator('#comparison-rows tr').count() == 5
    assert page.locator('#ledger-rows tr').count() > 20

    page.locator('.nav-item[data-view=account]').click()
    page.locator('#account-input').fill('date,value,external_flow\n2023-01-01,100,0\n2024-01-01,0,-110')
    page.locator('#account-form button[type=submit]').click()
    page.locator('#account-result .account-result-metrics').wait_for()
    assert '+10,00%' in page.locator('#account-result').inner_text()

    page.locator('.nav-item[data-view=proxies]').click()
    page.locator('#proxy-real').fill('date,price\n2022-01-31,200\n2022-02-28,220')
    page.locator('#proxy-series').fill('date,price\n2021-12-31,90\n2022-01-31,100\n2022-02-28,105')
    for name,value in {'source':'fixture','exposure':'global equity','hedging':'none','duration':'not applicable','distributions':'total return'}.items():
        page.locator(f'#proxy-form [name={name}]').fill(value)
    page.locator('#proxy-form button[type=submit]').click()
    page.locator('#proxy-result').wait_for(state='visible')
    assert page.locator('#proxy-result tbody tr').count() == 3

    # A failed live analysis must retain the explicit demo source of displayed results.
    page.locator('.nav-item[data-view=overview]').click()
    with page.expect_response(lambda r: r.url.endswith('/api/backtest') and r.request.method == 'POST') as unavailable:
        page.locator('#source-select').select_option('yahoo')
    # The complete live universe may become available as the provider gains history.
    if unavailable.value.status != 200:
        page.locator('#error-banner').wait_for(state='visible')
        assert 'DEMO' in page.locator('#source-badge').inner_text()
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(300)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
    page.screenshot(path=str(ARTIFACTS / 'dashboard-mobile.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('Browser smoke: config, chart, signals, accounting, account, proxies, ZIP and mobile passed.')
