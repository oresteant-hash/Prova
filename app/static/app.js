'use strict';

const $ = (id) => document.getElementById(id);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const number = (value, digits = 2) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value).toLocaleString('it-IT', {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
const percent = (value, digits = 2, signed = false) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${signed && value > 0 ? '+' : ''}${number(Number(value) * 100, digits)}%` : '—';
const money = (value, digits = 0) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${number(value, digits)} €` : '—';
const date = (value, options = {}) => {if (!value) return '—'; const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value); return Number.isNaN(parsed.getTime()) ? String(value) : new Intl.DateTimeFormat('it-IT', {day:'2-digit',month:'short',year:'numeric',timeZone:'Europe/Rome',...options}).format(parsed);};
const month = (value) => date(value, {day:undefined,month:'long',year:'numeric'});
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const palette = ['#3e7056','#77956a','#a6ba8b','#c6cf9e','#d2b282','#c99566','#93aba1','#597774','#a29278','#7e9a85','#bac39b','#d1d8c4'];
const state = {catalog:[],defaults:{},config:{},result:null,status:null,view:'overview',series:{strategy:true,world:true,balanced:true},busy:false,previousFocus:null};
const viewLabels = {overview:'Panoramica',allocation:'Segnali e allocazioni',accounting:'Contabilità e fiscalità',data:'Dati e strumenti',account:'Il mio conto',proxies:'Analisi con proxy'};
const viewTitles = {overview:['Il capitale, in prospettiva.','Una strategia sistematica. Ogni scelta, sotto la lente.'],allocation:['La logica dietro ogni scelta.','Momentum, graduatoria e allocazioni. Senza punti ciechi.'],accounting:['Quanto resta, davvero.','Rendimenti, operazioni, costi e ipotesi fiscali a confronto.'],data:['La qualità parte dai dati.','Strumenti definiti, fonti dichiarate, risultati riproducibili.'],account:['Il tuo conto, misurato bene.','Separa la performance della gestione da quella del capitale.'],proxies:['Più storia. Ipotesi esplicite.','Un’analisi separata per estendere le serie con proxy documentati.']};

async function request(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'},...options});
  let payload;
  try {payload = await response.json();} catch {throw new Error(`Il server ha restituito una risposta non valida (${response.status}).`);}
  if (!response.ok) {
    const detail = payload.detail ?? payload.error ?? `Errore HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join('.') || 'Dati'}: ${item.msg || JSON.stringify(item)}`).join(' · ') : JSON.stringify(detail));
  }
  return payload;
}

function navigate(view, updateHash = true) {
  if (!viewLabels[view]) view = 'overview';
  state.view = view;
  $$('.view').forEach((element) => element.classList.toggle('active', element.id === `view-${view}`));
  $$('.nav-item').forEach((element) => {element.classList.toggle('active', element.dataset.view === view); if(element.dataset.view === view) element.setAttribute('aria-current','page'); else element.removeAttribute('aria-current');});
  $('breadcrumb-current').textContent = viewLabels[view];
  $('page-title').textContent = viewTitles[view][0];
  $('page-subtitle').textContent = viewTitles[view][1];
  $('sidebar').classList.remove('open');
  $('menu-button').setAttribute('aria-expanded','false');
  if (updateHash && location.hash !== `#${view}`) history.replaceState(null, '', `#${view}`);
  if(view === 'overview' && state.result) requestAnimationFrame(renderChart);
}

function setBusy(busy, phase = 'analysis') {
  state.busy = busy;
  $('loading').hidden = !busy;
  $('loading').setAttribute('aria-live','polite');
  $('loading').querySelector('strong').textContent = phase === 'refresh' ? 'Scaricamento e verifica Yahoo in corso' : 'Stiamo ricostruendo la strategia';
  $('loading').querySelector('div > span').textContent = phase === 'refresh' ? 'Controllo degli strumenti, delle quotazioni e delle serie storiche.' : 'Segnali, rendimenti e contabilità a partire dai dati.';
  $('config-button').disabled = busy;
  $('source-select').disabled = busy;
  $('refresh-data').disabled = busy;
  $$('#config-form button').forEach((button) => button.disabled = busy);
}

function showError(message) {$('error-banner').textContent = message; $('error-banner').hidden = false;}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => {$('toast').hidden = true;}, 4300); }
function assetFor(ticker) {return state.catalog.find((asset) => asset.ticker === ticker) || {ticker,label:ticker,id:'—'};}
function signalRows(signal) {return Array.isArray(signal?.rows) ? signal.rows : [];}
function weightsOf(signal) {return Object.entries(signal?.weights || {}).filter(([,weight]) => Number(weight) > 0).sort((a,b) => b[1] - a[1]);}
function classFor(value) {return value > 0 ? 'positive' : value < 0 ? 'negative' : '';}

function fillConfig(config) {
  const form = $('config-form');
  for (const [key,value] of Object.entries(config)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value == null ? '' : ['tax_rate','rebalance_band'].includes(key) ? Number(value) * 100 : value;
  }
  toggleMinimum();
}
function readConfig() {
  const config = {};
  for (const input of $('config-form').elements) {
    if (!input.name) continue;
    if (input.type === 'checkbox') config[input.name] = input.checked;
    else if (input.type === 'number' || input.name === 'top_n') config[input.name] = Number(input.value) / (['tax_rate','rebalance_band'].includes(input.name) ? 100 : 1);
    else config[input.name] = input.value || null;
  }
  return config;
}
function toggleMinimum() {
  const form = $('config-form');
  form.elements.namedItem('min_assets').disabled = form.elements.namedItem('universe_mode').value === 'complete';
}
function openConfig() {
  fillConfig(state.config);
  state.previousFocus = document.activeElement;
  $('config-drawer').hidden = false;
  $('drawer-backdrop').hidden = false;
  document.body.style.overflow = 'hidden';
  $('close-config').focus();
}
function closeConfig() {
  $('config-drawer').hidden = true;
  $('drawer-backdrop').hidden = true;
  document.body.style.overflow = '';
  state.previousFocus?.focus();
}

async function runAnalysis(source = $('source-select').value, config = state.config) {
  setBusy(true);
  $('error-banner').hidden = true;
  try {
    const result = await request('/api/backtest', {method:'POST',body:JSON.stringify({config,source})});
    state.result = result;
    state.config = result.config || config;
    fillConfig(state.config);
    renderAll();
    return true;
  } catch (error) {
    showError(`${error.message}${state.result ? ` I risultati visibili restano quelli dell’ultima analisi ${state.result.source === 'demo' ? 'dimostrativa' : 'Yahoo'}.` : ''}`);
    return false;
  } finally {setBusy(false);}
}

function renderAll() {
  const result = state.result;
  if (!result) return;
  const demo = result.source === 'demo' || result.source?.includes('synthetic');
  $('source-badge').classList.toggle('real', !demo);
  $('source-badge').innerHTML = `<span></span>${demo ? 'DEMO · DATI SINTETICI' : 'YAHOO FINANCE · DATI REALI'}`;
  $('source-disclaimer').innerHTML = demo ? '<strong>Modalità dimostrativa.</strong> Serie sintetiche per esplorare il modello, non quotazioni reali.' : '<strong>Fonte Yahoo Finance.</strong> Dati scaricati e conservati; verifica degli strumenti e provenienza nel catalogo.';
  $('updated-label').textContent = `Dati aggiornati: ${date(result.updated_at)}${demo ? ' · simulati' : ''}`;
  const warnings = [...new Set(result.warnings || [])];
  $('warnings-banner').hidden = warnings.length === 0;
  $('warnings-banner').innerHTML = warnings.length ? `<details><summary><strong>Ipotesi e qualità dei dati</strong><span>${warnings.length} note · espandi per verificare</span></summary><ul>${warnings.map((warning) => `<li>${escapeHTML(warning)}</li>`).join('')}</ul></details>` : '';
  const exportURL = result.analysis_id ? `/api/export/${encodeURIComponent(result.analysis_id)}` : null;
  if(exportURL) { $('export-button').href = exportURL; $('export-button').setAttribute('aria-disabled','false'); }
  else {$('export-button').removeAttribute('href'); $('export-button').setAttribute('aria-disabled','true');}
  renderOverview();
  renderSignalOptions();
  renderSignal();
  renderHistory();
  renderAccounting();
  renderData();
}

function renderOverview() {
  const result = state.result;
  const config = state.config;
  const metrics = result.metrics?.strategy || {};
  const period = result.period || {};
  $('period-value').textContent = `${date(period.start || result.months?.[0]?.date)} — ${date(period.end || result.months?.at(-1)?.date)} · ${period.months ?? result.months?.length ?? 0} mesi`;
  $('strategy-chips').innerHTML = `<span class="chip">${config.formula === 'reactive' ? 'Formula reattiva' : 'Formula uniforme'}</span><span class="chip">Top ${escapeHTML(config.top_n)}</span><span class="chip">${{score:'Momentum Score',equal:'Pesi uniformi',inverse_vol:'Inversa volatilità'}[config.weighting] || escapeHTML(config.weighting)}</span>${config.ma_filter ? '<span class="chip">MA12 attiva</span>' : ''}`;
  const cards = [
    {label:'Rendimento cumulato',value:metrics.cumulative_return,note:'Momentum lordo · periodo comune',primary:true,icon:'chart',signed:true},
    {label:'Crescita annua · CAGR',value:metrics.cagr,note:`Su ${period.months ?? result.months?.length ?? 0} mesi interi`,icon:'arrow'},
    {label:'Volatilità annualizzata',value:metrics.annualized_volatility,note:'Rendimenti mensili · campionaria',icon:'layers'},
    {label:'Massimo drawdown',value:metrics.max_drawdown,note:'Sulla curva mensile, capitale incluso',icon:'chart'}
  ];
  $('metrics-grid').innerHTML = cards.map((card) => {const text = percent(card.value,2,card.signed);return `<article class="metric-card${card.primary ? ' primary' : ''}"><div class="metric-label">${card.label}</div><span class="metric-symbol">${icon(card.icon)}</span><div class="metric-value">${text.endsWith('%') ? `${escapeHTML(text.slice(0,-1))}<span class="metric-unit">%</span>` : text}</div><p class="metric-note">${card.note}</p></article>`;}).join('');
  $('chart-capital').textContent = `Capitale iniziale ${money(config.initial_capital)}`;
  renderChart();
  renderAllocation();
  const current = result.current_allocation;
  const provisional = result.provisional_signal;
  const live = result.live_performance || {};
  const nextMonth = current?.signal_date ? new Date(`${current.signal_date}T12:00:00`) : null;
  if(nextMonth) nextMonth.setMonth(nextMonth.getMonth()+1,1);
  $('live-allocation-date').textContent = nextMonth ? month(nextMonth.toISOString().slice(0,10)) : 'Segnale non disponibile';
  $('live-allocation-note').textContent = current?.tradable === false ? 'Storia insufficiente: allocazione non attivabile.' : `Dal segnale del ${date(current?.signal_date)}.`;
  $('provisional-summary').textContent = provisional ? `${provisional.selected?.length || 0} strumenti selezionati` : 'Non disponibile';
  $('provisional-note').textContent = provisional ? `Al ${date(provisional.as_of || provisional.signal_date)} · non definitivo.` : 'Nessun mese incompleto disponibile.';
  $('live-return').textContent = percent(live.return_since_month_end,2,true);
  $('live-note').textContent = live.as_of ? `Al ${date(live.as_of)} · dal fine mese, ${money(live.estimated_equity)}.` : 'Ultima valorizzazione non disponibile.';
  const weights = weightsOf(current);
  $('overview-selection').innerHTML = weights.length ? weights.map(([ticker,weight],i) => {
    const asset = assetFor(ticker);
    const row = signalRows(current).find((item) => item.ticker === ticker);
    const fallback = ticker === config.fallback && !current.selected?.includes(ticker);
    return `<tr><td><div class="asset-cell"><span class="asset-marker" style="--asset-bg:${palette[i % palette.length]}17;--asset-color:${palette[i % palette.length]}">${escapeHTML(String(asset.id))}</span><strong>${escapeHTML(asset.label)}</strong></div></td><td>${escapeHTML(ticker)}</td><td class="number ${classFor(row?.score)}">${percent(row?.score,2,true)}</td><td class="number"><div class="weight-cell"><span class="weight-bar"><i style="width:${Math.min(100,weight*100)}%"></i></span><strong>${percent(weight,1)}</strong></div></td><td><span class="status-tag${fallback ? ' fallback' : ''}">${fallback ? 'Fallback' : ticker === config.fallback ? 'Selezione + fallback' : 'Selezionato'}</span></td></tr>`;
  }).join('') : '<tr><td colspan="5" class="empty-state">Il segnale non dispone di storia sufficiente per costruire un portafoglio.</td></tr>';
}

function renderAllocation() {
  const signal = state.result?.current_allocation;
  const weights = weightsOf(signal);
  const circumference = 2 * Math.PI * 62;
  let offset = 0;
  const segments = weights.map(([ticker,weight],i) => {
    const length = weight * circumference;
    const circle = `<circle cx="84" cy="84" r="62" fill="none" stroke="${palette[i % palette.length]}" stroke-width="18" stroke-dasharray="${Math.max(0,length-2)} ${circumference}" stroke-dashoffset="${-offset}" stroke-linecap="butt"><title>${escapeHTML(assetFor(ticker).label)} ${percent(weight,1)}</title></circle>`;
    offset += length;
    return circle;
  }).join('');
  $('allocation-donut').innerHTML = `<svg class="allocation-donut-svg" viewBox="0 0 168 168" role="img" aria-label="Allocazione: ${escapeHTML(weights.map(([ticker,weight]) => `${assetFor(ticker).label} ${percent(weight,1)}`).join(', '))}"><circle cx="84" cy="84" r="62" fill="none" stroke="#eef2e8" stroke-width="18"/>${segments}</svg><div class="donut-center"><strong>${weights.length}</strong><span>STRUMENTI IN PORTAFOGLIO</span></div>`;
  $('allocation-list').innerHTML = weights.length ? weights.map(([ticker,weight],i) => `<div class="allocation-item" style="--allocation-color:${palette[i % palette.length]}"><span><i></i>${escapeHTML(assetFor(ticker).label)}</span><strong>${percent(weight,1)}</strong></div>`).join('') : '<div class="empty-state">Nessuna allocazione</div>';
  $('allocation-asof').textContent = `Target dal segnale del ${date(signal?.signal_date)} · ${signal?.available_count ?? 0} asset disponibili.`;
}

function renderChart() {
  const result = state.result;
  if (!result?.months?.length) { $('equity-chart').innerHTML = '<div class="empty-state">Nessun mese completo nel periodo selezionato.</div>'; return; }
  const capital = state.config.initial_capital;
  const months = result.months;
  const first = new Date(`${months[0].date}T12:00:00`);
  first.setMonth(first.getMonth(),0);
  const rows = [{date:first.toISOString().slice(0,10),equity:capital,benchmark_world_equity:capital,benchmark_6040_equity:capital},...months];
  const definitions = [{key:'strategy',field:'equity',label:'Momentum lordo',color:'#245e4f',width:2.5},{key:'world',field:'benchmark_world_equity',label:'MSCI World',color:'#9da99d',width:1.5},{key:'balanced',field:'benchmark_6040_equity',label:'60/40',color:'#c29a62',width:1.5}];
  const active = definitions.filter((series) => state.series[series.key]);
  const values = rows.flatMap((row) => active.map((series) => Number(row[series.field])).filter(Number.isFinite));
  if (!values.length) return;
  const mobile = window.innerWidth <= 640;
  const width = Math.max(340,$('equity-chart').clientWidth || 860), height = mobile ? 255 : 305, margin = {top:18,right:20,bottom:33,left:mobile ? 48 : 57};
  const plotW = width-margin.left-margin.right, plotH = height-margin.top-margin.bottom;
  let low = Math.min(...values), high = Math.max(...values);
  const range = high-low || high*.1 || 1;
  low = Math.max(0,low-range*.12); high += range*.12;
  const x = (index) => margin.left+index/(rows.length-1)*plotW;
  const y = (value) => margin.top+(high-value)/(high-low)*plotH;
  const paths = active.map((series) => {
    const points = rows.map((row,i) => [x(i),y(Number(row[series.field]))]).filter((point) => Number.isFinite(point[1]));
    const path = points.map((point,i) => `${i ? 'L' : 'M'}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(' ');
    const area = series.key === 'strategy' ? `<path d="${path} L${points.at(-1)?.[0]},${margin.top+plotH} L${points[0]?.[0]},${margin.top+plotH} Z" fill="url(#chart-fill)" stroke="none"/>` : '';
    return `${area}<path d="${path}" fill="none" stroke="${series.color}" stroke-width="${series.width}" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const grid = Array.from({length:5},(_,i) => {const value = low+(high-low)*i/4; const yy = y(value); return `<line class="chart-grid" x1="${margin.left}" y1="${yy}" x2="${width-margin.right}" y2="${yy}"/><text class="chart-axis" x="${margin.left-12}" y="${yy+3}" text-anchor="end">${number(value/1000,0)}k €</text>`;}).join('');
  const axisIndices = [...new Set(Array.from({length:mobile ? 4 : 6},(_,i) => Math.round(i*(rows.length-1)/(mobile ? 3 : 5))))];
  const axis = axisIndices.map((i) => `<text class="chart-axis" x="${x(i)}" y="${height-10}" text-anchor="${i===0 ? 'start' : i===rows.length-1 ? 'end' : 'middle'}">${date(rows[i].date,{day:undefined,month:'short',year:'numeric'})}</text>`).join('');
  $('equity-chart').innerHTML = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Crescita di ${money(capital)} nei mesi completi del backtest"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#669369" stop-opacity=".14"/><stop offset="100%" stop-color="#669369" stop-opacity="0"/></linearGradient></defs>${grid}${paths}${axis}<g id="chart-crosshair" visibility="hidden"><line id="chart-hover-line" y1="${margin.top}" y2="${margin.top+plotH}" stroke="#a5b09a" stroke-dasharray="3 4"/>${active.map((series) => `<circle id="chart-hover-${series.key}" r="3.5" fill="${series.color}" stroke="#fff" stroke-width="2"/>`).join('')}</g><rect id="chart-interaction" x="${margin.left}" y="${margin.top}" width="${plotW}" height="${plotH}" fill="transparent" stroke="none"/></svg><div class="chart-tooltip" id="chart-tooltip" hidden></div>`;
  const svg = $('equity-chart').querySelector('svg');
  const showAt = (clientX) => {
    const bounds = svg.getBoundingClientRect();
    const pointerX = (clientX-bounds.left)/bounds.width*width;
    const index = Math.min(rows.length-1,Math.max(0,Math.round((pointerX-margin.left)/plotW*(rows.length-1))));
    const row = rows[index], xx = x(index);
    $('chart-crosshair').setAttribute('visibility','visible');
    $('chart-hover-line').setAttribute('x1',xx); $('chart-hover-line').setAttribute('x2',xx);
    for(const series of active) {const circle = $(`chart-hover-${series.key}`);circle.setAttribute('cx',xx);circle.setAttribute('cy',y(Number(row[series.field])));}
    const tip = $('chart-tooltip');
    tip.innerHTML = `<strong>${escapeHTML(month(row.date))}</strong>${active.map((series) => `<div class="tooltip-line"><span>${series.label}</span><b>${money(row[series.field])}</b></div>`).join('')}`;
    tip.hidden = false;
    const outer = $('equity-chart').getBoundingClientRect();
    const left = xx/width*bounds.width + bounds.left-outer.left;
    tip.style.left = `${Math.max(8,Math.min(outer.width-tip.offsetWidth-8,left+14))}px`;
    tip.style.top = '18px';
  };
  $('chart-interaction').addEventListener('pointermove',(event) => showAt(event.clientX));
  svg.addEventListener('pointerleave',() => {$('chart-crosshair').setAttribute('visibility','hidden');$('chart-tooltip').hidden = true;});
}

function renderSignalOptions() {
  const selected = $('signal-select').value;
  const signals = state.result?.signals || [];
  $('signal-select').innerHTML = `${state.result?.provisional_signal ? '<option value="provisional">Mese corrente · provvisorio</option>' : ''}${[...signals].reverse().map((signal) => `<option value="${escapeHTML(signal.signal_date)}">${escapeHTML(month(signal.signal_date))} · completo</option>`).join('')}`;
  if (!signals.some((signal) => signal.signal_date === state.result?.current_allocation?.signal_date) && state.result?.current_allocation) $('signal-select').insertAdjacentHTML('beforeend', `<option value="current">${escapeHTML(month(state.result.current_allocation.signal_date))} · completo</option>`);
  if ([...$('signal-select').options].some((option) => option.value === selected)) $('signal-select').value = selected;
  else $('signal-select').value = state.result?.current_allocation?.signal_date || 'current';
}
function activeSignal() {
  const value = $('signal-select').value;
  if(value === 'provisional') return state.result?.provisional_signal;
  return state.result?.signals?.find((signal) => signal.signal_date === value) || state.result?.current_allocation;
}
function renderSignal() {
  const signal = activeSignal();
  const provisional = $('signal-select').value === 'provisional';
  if (!signal) { $('signal-rows').innerHTML = '<tr><td colspan="11" class="empty-state">Nessun segnale disponibile.</td></tr>'; $('signal-context').innerHTML = ''; return; }
  $('signal-context').innerHTML = `<span class="chip">${provisional ? 'PROVVISORIO · NON ESEGUIBILE COME MESE COMPLETO' : `Segnale ${escapeHTML(date(signal.signal_date))}`}</span><span class="chip">${signal.available_count ?? 0} disponibili</span><span class="chip">${signal.eligible_count ?? 0} eleggibili</span><span class="chip">${signal.selected?.length ?? 0} selezionati</span><p>${signal.tradable === false ? 'Universo non pronto: nessun target attivabile.' : 'Pesi comprensivi dell’eventuale quota di fallback.'}</p>`;
  $('signal-rows').innerHTML = signalRows(signal).map((row) => {
    const weight = signal.weights?.[row.ticker] || 0;
    let label = row.selected ? 'Selezionato' : weight > 0 ? 'Fallback' : row.eligible ? 'Eleggibile' : row.available ? 'Escluso' : 'Storia insufficiente';
    const type = row.selected ? '' : weight > 0 ? ' fallback' : ' muted';
    return `<tr><td>${row.rank ?? '—'}</td><td><strong>${escapeHTML(row.label || assetFor(row.ticker).label)}</strong><small>${escapeHTML(row.ticker)} · ID ${escapeHTML(row.id)}</small></td><td class="number">${number(row.price,2)}</td><td class="number ${classFor(row.r3)}">${percent(row.r3,2,true)}</td><td class="number ${classFor(row.r6)}">${percent(row.r6,2,true)}</td><td class="number ${classFor(row.r12)}">${percent(row.r12,2,true)}</td><td class="number ${classFor(row.score)}"><strong>${percent(row.score,2,true)}</strong></td><td class="number">${number(row.ma12,2)}</td><td class="number">${percent(row.volatility)}</td><td class="number"><strong>${percent(weight,1)}</strong></td><td><span class="status-tag${type}" title="${escapeHTML(row.reason || '')}">${label}</span>${row.reason && !row.selected ? `<small>${escapeHTML(row.reason)}</small>` : ''}</td></tr>`;
  }).join('');
}
function renderHistory() {
  const months = state.result?.months || [];
  $('historical-count').textContent = `${months.length} mesi completi`;
  $('historical-rows').innerHTML = [...months].reverse().map((row) => `<tr><td><strong>${escapeHTML(month(row.date))}</strong></td><td>${escapeHTML(date(row.signal_date))}</td><td class="number ${classFor(row.return)}">${percent(row.return,2,true)}</td><td class="number ${classFor(row.benchmark_world_return)}">${percent(row.benchmark_world_return,2,true)}</td><td class="number ${classFor(row.benchmark_6040_return)}">${percent(row.benchmark_6040_return,2,true)}</td><td class="number">${money(row.equity)}</td><td>${Object.entries(row.weights || {}).filter(([,weight]) => weight>0).map(([ticker,weight]) => `<span class="history-weight">${escapeHTML(ticker)} ${percent(weight,1)}</span>`).join('')}</td></tr>`).join('') || '<tr><td colspan="7" class="empty-state">Nessun mese completo disponibile.</td></tr>';
}

function renderAccounting() {
  const result = state.result;
  let comparison = result.comparison || [];
  if(!comparison.length) {
    comparison = [{name:'Momentum lordo · teorico',...(result.metrics?.strategy || {})},...Object.entries(result.accounting || {}).map(([key,value]) => ({name:key==='standard' ? 'Momentum 1.0 standard' : 'Momentum Score',...(value.metrics || {}),...value}))];
  }
  $('comparison-rows').innerHTML = comparison.map((row) => `<tr><td><strong>${escapeHTML(row.name || row.key)}</strong>${row.key === 'standard' ? '<small>Imposte solo sulle uscite complete</small>' : row.key === 'score' ? '<small>Imposte su tutte le vendite</small>' : ''}</td><td class="number ${classFor(row.cumulative_return)}">${percent(row.cumulative_return,2,true)}</td><td class="number">${percent(row.cagr)}</td><td class="number">${percent(row.annualized_volatility)}</td><td class="number">${percent(row.max_drawdown)}</td><td class="number">${percent(row.turnover)}</td><td class="number">${money(row.total_costs ?? row.costs)}</td><td class="number">${money(row.total_taxes ?? row.taxes)}</td><td class="number"><strong>${money(row.before_liquidation)}</strong></td><td class="number"><strong>${money(row.after_liquidation)}</strong></td></tr>`).join('') || '<tr><td colspan="10" class="empty-state">Confronto contabile non disponibile.</td></tr>';
  renderLedger();
}
function renderLedger() {
  const key = $('ledger-select').value;
  const account = ['world','balanced'].includes(key) ? state.result?.accounting_benchmarks?.[key] : state.result?.accounting?.[key];
  const totals = account?.totals || {};
  $('ledger-summary').innerHTML = `<span>Costi<strong>${money(totals.costs)}</strong></span><span>Imposte<strong>${money(totals.taxes)}</strong></span><span>Plusvalenze realizzate<strong>${money(totals.realized_gains)}</strong></span><span>Turnover<strong>${percent(totals.turnover)}</strong></span>${account?.warnings?.length ? `<span class="operating-warning">${escapeHTML(account.warnings.join(' · '))}</span>` : ''}`;
  const trades = account?.trades || [];
  $('ledger-rows').innerHTML = [...trades].reverse().map((trade) => `<tr><td>${escapeHTML(date(trade.date))}</td><td>${escapeHTML(date(trade.signal_date))}</td><td><strong>${escapeHTML(trade.ticker)}</strong></td><td><span class="status-tag${trade.side?.toLowerCase() === 'sell' ? ' fallback' : ''}">${trade.side?.toLowerCase() === 'sell' ? 'Vendita' : trade.side?.toLowerCase() === 'buy' ? 'Acquisto' : escapeHTML(trade.side)}</span></td><td class="number">${number(trade.quantity,4)}</td><td class="number">${money(trade.price,2)}</td><td class="number">${money(trade.gross,2)}</td><td class="number">${money(trade.cost,2)}</td><td class="number">${money(trade.realized_gain,2)}</td><td class="number">${money(trade.tax,2)}</td></tr>`).join('') || '<tr><td colspan="10" class="empty-state">Il registro dettagliato è disponibile nell’esportazione completa.</td></tr>';
}

function metadataFor(ticker, metadata = state.result?.metadata || {}) {
  const instruments = metadata.instruments || metadata.symbols || metadata;
  if(Array.isArray(instruments)) return instruments.find((item) => item.ticker === ticker || item.symbol === ticker) || {};
  return instruments[ticker] || {};
}
function renderData() {
  const cache = $('catalog-source').value === 'cache';
  const result = cache ? state.status : state.result;
  const demo = result?.source === 'demo' || result?.source?.includes('synthetic');
  const meta = result?.metadata || {};
  $('catalog-source').options[1].disabled = !state.status?.cached;
  const shortHistory = Object.entries(meta.instruments || {}).filter(([,instrument]) => instrument.quality?.complete_monthly_observations != null && instrument.quality.complete_monthly_observations < 13);
  $('catalog-warning').hidden = shortHistory.length === 0;
  $('catalog-warning').textContent = shortHistory.length ? `Storia insufficiente per il momentum: ${shortHistory.map(([ticker,instrument]) => `${ticker} · ${instrument.quality.complete_monthly_observations} chiusure mensili complete`).join('; ')}. Servono 13 chiusure consecutive; questi strumenti non sono sostituiti né estesi automaticamente.` : '';
  $('data-status-grid').innerHTML = `<article class="data-status-card"><span class="mini-label">ORIGINE DELLE SERIE</span><h3${demo ? ' class="unverified"' : ''}>${demo ? 'Dati sintetici' : result ? 'Yahoo Finance' : 'Non disponibile'}</h3><p>${demo ? 'Serie dimostrative deterministiche; nessuna quotazione reale.' : 'Prezzi raw, adjusted e distribuzioni conservati separatamente.'}</p></article><article class="data-status-card"><span class="mini-label">ULTIMO AGGIORNAMENTO</span><h3>${escapeHTML(date(result?.updated_at))}</h3><p>Versione yfinance: ${escapeHTML(meta.yfinance_version || meta.library_version || (demo ? 'non utilizzata in demo' : '—'))}</p></article><article class="data-status-card"><span class="mini-label">CONVENZIONE DI CALCOLO</span><h3>EUR · Total return</h3><p>Mensile per segnali; prezzi negoziabili per la contabilità.</p></article>`;
  $('catalog-rows').innerHTML = state.catalog.map((asset) => {
    const instrument = metadataFor(asset.ticker,meta);
    const verified = !demo && (instrument.listing_verified === true || instrument.verified === true || instrument.status === 'verified' || instrument.validation === 'verified');
    const currency = instrument.provider_currency || instrument.currency || (demo ? 'EUR · sintetico' : '—');
    const exchange = instrument.provider_exchange || instrument.exchange || instrument.fullExchangeName || instrument.market || '';
    return `<tr><td>${asset.role === 'benchmark' ? '<span class="status-tag muted">Benchmark</span>' : `ID ${escapeHTML(asset.id)}`}</td><td><strong>${escapeHTML(asset.label)}</strong><small>${escapeHTML(asset.name)}</small><small>${escapeHTML(asset.exposure || '')}</small></td><td>${escapeHTML(asset.isin)}</td><td><strong>${escapeHTML(asset.ticker)}</strong></td><td>${escapeHTML(currency)}${exchange ? `<small>${escapeHTML(exchange)}</small>` : ''}</td><td><span class="status-tag${verified ? '' : ' fallback'}">${verified ? 'Mercato verificato' : demo ? 'Demo · non verificato' : instrument.verification_status || 'Da verificare'}</span>${!demo ? `<small>${instrument.isin_verified ? 'ISIN confermato' : 'ISIN non comprovato dal provider'}</small>` : ''}${instrument.first_quote || instrument.quality?.first_quote ? `<small>${escapeHTML(date(instrument.first_quote || instrument.quality.first_quote))} → ${escapeHTML(date(instrument.last_quote || instrument.quality?.last_quote))}</small>` : ''}${instrument.quality?.complete_monthly_observations != null ? `<small>${instrument.quality.complete_monthly_observations} chiusure mensili complete</small>` : ''}</td></tr>`;
  }).join('');
  $('metadata-json').textContent = JSON.stringify({source:result?.source || 'nessuna',updated_at:result?.updated_at,provider_status:state.status,metadata:meta},null,2);
}

function parseDelimited(text) {
  const lines = text.trim().split(/\r?\n/).filter((line) => line.trim());
  if(lines.length < 2) throw new Error('Inserisci un’intestazione e almeno una riga di dati.');
  const delimiter = lines[0].includes(';') ? ';' : lines[0].includes('\t') ? '\t' : ',';
  const split = (line) => {
    const cells = [];let current = '', quoted = false;
    for(let i=0;i<line.length;i++) {const character = line[i];if(character === '"'){if(quoted && line[i+1] === '"'){current += '"';i++;}else quoted = !quoted;}else if(character === delimiter && !quoted){cells.push(current.trim());current = '';}else current += character;}
    cells.push(current.trim());return cells;
  };
  const headers = split(lines[0]).map((header) => header.replace(/^\uFEFF/,'').toLowerCase());
  return lines.slice(1).map((line,i) => {const values = split(line);if(values.length !== headers.length) throw new Error(`La riga ${i+2} contiene ${values.length} campi anziché ${headers.length}.`);return Object.fromEntries(headers.map((header,j) => [header,values[j]]));});
}
function parseInput(text, kind) {
  if (!text.trim()) throw new Error('Inserisci o carica i dati prima di calcolare.');
  let rows;
  if(/^[\[{]/.test(text.trim())) {const parsed = JSON.parse(text);rows = Array.isArray(parsed) ? parsed : parsed.valuations || parsed.series || parsed[kind];}
  else rows = parseDelimited(text);
  if (!Array.isArray(rows) || !rows.length) throw new Error('Il documento non contiene una lista di dati valida.');
  const numeric = (value, optional = false) => {if(optional && (value === undefined || value === '')) return 0; const clean = typeof value === 'string' ? value.replace(/\s/g,'').replace(/€/g,'').replace(/^(\d{1,3}(\.\d{3})+),/, (part) => part.replace(/\./g,'')).replace(',','.') : value;const n = Number(clean);if(value === null || value === '' || !Number.isFinite(n)) throw new Error('Ogni riga deve contenere valori numerici validi.');return n;};
  return rows.map((row,index) => {
    const d = row.date || row.data;
    if(!/^\d{4}-\d{2}-\d{2}$/.test(d || '')) throw new Error(`Riga ${index+1}: usa una data nel formato AAAA-MM-GG.`);
    return kind === 'valuations' ? {date:d,value:numeric(row.value ?? row.valore),external_flow:numeric(row.external_flow ?? row.flusso,true)} : {date:d,price:numeric(row.price ?? row.prezzo ?? row.value)};
  });
}
async function submitAccount(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type=submit]');
  $('account-error').hidden = true;
  button.disabled = true;
  try {
    const valuations = parseInput($('account-input').value,'valuations');
    const result = await request('/api/account-performance',{method:'POST',body:JSON.stringify({valuations})});
    state.accountResult = result;
    state.accountValuations = valuations;
    const twr = result.twr ?? result.time_weighted_return;
    const xirr = result.xirr ?? result.money_weighted_return;
    $('account-result').innerHTML = `<div class="eyebrow small">LA PERFORMANCE DEL TUO CONTO</div><h2>Due prospettive, una storia.</h2><div class="account-result-metrics"><div><span class="mini-label">TWR · GESTIONE</span><div class="metric-value">${percent(twr,2,true)}</div><p class="metric-note">Rendimento composto del periodo</p></div><div><span class="mini-label">XIRR · CAPITALE</span><div class="metric-value">${percent(xirr,2,true)}</div><p class="metric-note">Rendimento annualizzato sui flussi</p></div></div><div class="result-detail">Periodo: <strong>${escapeHTML(date(result.start || result.start_date || result.period?.start || valuations[0].date))} — ${escapeHTML(date(result.end || result.end_date || result.period?.end || valuations.at(-1).date))}</strong><br>Valorizzazioni: <strong>${valuations.length}</strong>${result.xirr_error ? `<br><strong>XIRR non disponibile:</strong> ${escapeHTML(result.xirr_error)}` : ''}${result.warnings?.length ? `<br>${result.warnings.map(escapeHTML).join('<br>')}` : ''}</div><details class="result-detail"><summary>Dettagli del calcolo</summary><pre>${escapeHTML(JSON.stringify(result,null,2))}</pre></details><div class="result-export"><button class="button button-quiet" data-result-export="account-json">${icon('download')}Analisi JSON</button><button class="button button-quiet" data-result-export="account-csv">${icon('download')}Rendimenti CSV</button></div>`;
    toast('Performance del conto calcolata.');
  } catch(error) {$('account-error').textContent = error.message; $('account-error').hidden = false;}
  finally {button.disabled = false;}
}
async function submitProxy(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type=submit]');
  button.disabled = true;
  $('proxy-error').hidden = true;
  try {
    const real = parseInput($('proxy-real').value,'real');
    const proxy = parseInput($('proxy-series').value,'proxy');
    const metadata = Object.fromEntries(new FormData(event.currentTarget).entries());
    const result = await request('/api/proxy-analysis',{method:'POST',body:JSON.stringify({real,proxy,metadata})});
    state.proxyResult = result;
    state.proxyInputs = {real,proxy,metadata};
    const series = result.series || [];
    const rows = Array.isArray(series) ? series : Object.entries(series).map(([date,price]) => ({date,price}));
    $('proxy-result').hidden = false;
    $('proxy-result').innerHTML = `<div class="card-heading"><div><div class="eyebrow small">ANALISI SEPARATA · NON UTILIZZATA NEL BACKTEST</div><h2>Serie raccordata</h2></div><span class="label-soft">${rows.length} osservazioni</span></div><div class="proxy-output"><pre>${escapeHTML(JSON.stringify(result.metadata || {},null,2))}</pre><div class="table-scroll proxy-series-scroll"><table><thead><tr><th>Data</th><th class="number">Prezzo normalizzato</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHTML(date(row.date))}</td><td class="number">${number(row.price ?? row.value,4)}</td></tr>`).join('')}</tbody></table></div><div class="result-export"><button class="button button-quiet" data-result-export="proxy-json">${icon('download')}Serie e metadata JSON</button><button class="button button-quiet" data-result-export="proxy-csv">${icon('download')}Serie CSV</button></div></div>`;
    toast('Raccordo verificato. La strategia resta sui dati originali.');
  } catch(error) {$('proxy-error').textContent = error.message; $('proxy-error').hidden = false;}
  finally {button.disabled = false;}
}


function downloadResult(type) {
  const [kind,format] = type.split('-');
  const result = kind === 'account' ? state.accountResult : state.proxyResult;
  if(!result) return;
  let content;
  if(format === 'json') {
    const audit = kind === 'account' ? {analysis_type:'actual_account_performance',valuations:state.accountValuations,result} : {analysis_type:'separate_proxy_extension',...state.proxyInputs,result};
    content = JSON.stringify(audit,null,2);
  } else {
    const rows = kind === 'proxy' ? result.series : state.accountValuations.map((valuation,index) => ({...valuation,subperiod_return_fraction:result.subperiod_returns?.find((period) => period.end === valuation.date)?.return ?? '',twr_fraction:index === state.accountValuations.length-1 ? result.twr : '',xirr_annualized_fraction:index === state.accountValuations.length-1 ? result.xirr ?? '' : ''}));
    if(!rows?.length) return;
    const fields = [...new Set(rows.flatMap((row) => Object.keys(row)))];
    const cell = (value) => {if(value == null) return '';let text = String(value);if(typeof value === 'string' && /^[=+\-@]/.test(text)) text = "'" + text;return /[",\r\n]/.test(text) ? '"'+text.replace(/"/g,'""')+'"' : text;};
    content = [fields.join(','),...rows.map((row) => fields.map((key) => cell(row[key])).join(','))].join('\r\n');
  }
  const blob = new Blob([content],{type:format === 'json' ? 'application/json;charset=utf-8' : 'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url;link.download = `momentum-${kind === 'account' ? 'conto' : 'proxy'}.${format}`;
  document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url),1000);
}

function bindEvents() {
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
  $$('[data-navigate]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.navigate)));
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1), false));
  $('menu-button').addEventListener('click',() => {const open = $('sidebar').classList.toggle('open');$('menu-button').setAttribute('aria-expanded',String(open));});
  $('config-button').addEventListener('click', openConfig);
  $('close-config').addEventListener('click', closeConfig);
  $('drawer-backdrop').addEventListener('click', closeConfig);
  $('config-form').elements.namedItem('universe_mode').addEventListener('change',toggleMinimum);
  $('reset-config').addEventListener('click', () => fillConfig(state.defaults));
  $('config-form').addEventListener('submit',async (event) => {event.preventDefault();const config = readConfig();closeConfig();if(await runAnalysis($('source-select').value,config)) toast('Strategia ricalcolata con la nuova configurazione.');});
  $('source-select').addEventListener('change', async () => {await runAnalysis();});
  $('refresh-data').addEventListener('click',async () => {
    setBusy(true,'refresh');$('error-banner').hidden = true;
    try {
      let refresh = await request('/api/data/refresh',{method:'POST',body:'{}'});
      const jobId = refresh.job_id;
      if(refresh.status === 'running' && !jobId) throw new Error('Identificativo dell’aggiornamento non ricevuto dal server.');
      while(refresh.status === 'running') {
        await new Promise((resolve) => setTimeout(resolve,1000));
        refresh = await request(`/api/data/refresh/status?job_id=${encodeURIComponent(jobId)}`);
      }
      if(refresh.status === 'failed') throw new Error(refresh.error || 'Il provider non ha completato lo scaricamento o la verifica dei dati.');
      if(refresh.status !== 'updated') throw new Error('Il server non ha confermato il completamento dell’aggiornamento.');
      state.status = await request('/api/data/status');
      $('source-select').value = 'yahoo';
      if(await runAnalysis('yahoo')) toast('Dati Yahoo aggiornati e analisi ricalcolata.');
      else toast('Dati Yahoo scaricati. Il backtest richiede una storia sufficiente.');
    } catch(error) {showError(`Aggiornamento Yahoo non completato: ${error.message}. Nessun dato sintetico è stato usato in sostituzione.`);}
    finally {setBusy(false);}
  });
  $('signal-select').addEventListener('change',renderSignal);
  $('show-provisional').addEventListener('click',() => {navigate('allocation');if(state.result?.provisional_signal) {$('signal-select').value = 'provisional';renderSignal();}});
  $('ledger-select').addEventListener('change',renderLedger);
  $('catalog-source').addEventListener('change',renderData);
  window.addEventListener('resize',() => {clearTimeout(state.resizeTimer);state.resizeTimer = setTimeout(() => {if(state.view === 'overview' && state.result) renderChart();},180);});
  $$('.legend-item').forEach((button) => button.addEventListener('click',() => {const key = button.dataset.series;if(state.series[key] && Object.values(state.series).filter(Boolean).length === 1){toast('Mantieni almeno una curva visibile.');return;}state.series[key] = !state.series[key];button.classList.toggle('active',state.series[key]);button.setAttribute('aria-pressed',String(state.series[key]));renderChart();}));
  $('account-form').addEventListener('submit',submitAccount);
  $('account-file').addEventListener('change',async (event) => {const file = event.target.files[0];if(file) {$('account-input').value = await file.text();toast(`Caricato ${file.name}.`);}});
  $('proxy-form').addEventListener('submit',submitProxy);
  document.addEventListener('keydown',(event) => {
    if(event.key === 'Escape') {if(!$('config-drawer').hidden) closeConfig();$('sidebar').classList.remove('open');$('menu-button').setAttribute('aria-expanded','false');}
    if(event.key === 'Tab' && !$('config-drawer').hidden) {const focusable = [...$('config-drawer').querySelectorAll('button,input,select')].filter((element) => !element.disabled); const first = focusable[0],last = focusable.at(-1); if(event.shiftKey && document.activeElement === first){event.preventDefault();last.focus();}else if(!event.shiftKey && document.activeElement === last){event.preventDefault();first.focus();}}
  });
  document.addEventListener('click',(event) => {const exportButton = event.target.closest('[data-result-export]');if(exportButton) downloadResult(exportButton.dataset.resultExport);if($('sidebar').classList.contains('open') && !$('sidebar').contains(event.target) && !$('menu-button').contains(event.target)) {$('sidebar').classList.remove('open');$('menu-button').setAttribute('aria-expanded','false');}});
}

async function initialize() {
  bindEvents();
  navigate(location.hash.slice(1) || 'overview',false);
  setBusy(true);
  const [catalog,status] = await Promise.allSettled([request('/api/catalog'),request('/api/data/status')]);
  if(status.status === 'fulfilled') state.status = status.value;
  if(catalog.status !== 'fulfilled') {showError(`Impossibile leggere la configurazione: ${catalog.reason.message}`);setBusy(false);return;}
  state.catalog = catalog.value.instruments || [];
  state.defaults = catalog.value.defaults || {};
  state.config = {...state.defaults};
  fillConfig(state.config);
  await runAnalysis('demo');
}
initialize();
