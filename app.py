#!/usr/bin/env python3
"""Atlas Market Regime: local, dependency-free data server."""
from __future__ import annotations
import csv, io, json, math, os, statistics, time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
PORT = int(os.getenv("ATLAS_PORT", "8765"))
CACHE_SECONDS = int(os.getenv("ATLAS_CACHE_SECONDS", "3600"))
_cache = {"at": 0.0, "payload": None}

ASSETS = {"SPY":"Azioni USA","EFA":"Sviluppati ex-USA","EEM":"Emergenti","IWM":"Small cap USA","HYG":"High Yield","IEF":"Treasury intermedi","TLT":"Treasury lunga duration","GLD":"Oro","DBC":"Materie prime","BIL":"Liquidità T-Bill"}

def fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent":"Mozilla/5.0 Atlas/3.1"})
    with urlopen(req, timeout=15) as r:
        return json.load(r)

def prices(symbol: str) -> tuple[list[float], str]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&events=div%2Csplits"
    result = fetch_json(url)["chart"]["result"][0]
    closes = [float(x) for x in result["indicators"]["quote"][0]["close"] if x is not None]
    return closes, datetime.fromtimestamp(result["timestamp"][-1], timezone.utc).date().isoformat()

def change(xs: list[float], days: int) -> float:
    if len(xs) <= days: return 0.0
    return (xs[-1] / xs[-days-1] - 1) * 100

def trend_score(xs: list[float]) -> float:
    checks = [xs[-1] > statistics.fmean(xs[-200:]), change(xs,126)>0, change(xs,252)>0]
    return 25 + 25 * sum(checks)

def relative(a: list[float], b: list[float]) -> tuple[float,float,bool]:
    n=min(len(a),len(b)); ratio=[a[-n+i]/b[-n+i] for i in range(n)]
    c3=change(ratio,63); c6=change(ratio,126); above=ratio[-1]>statistics.fmean(ratio[-200:])
    return c3,c6,above

def correlation(a:list[float], b:list[float], n=60)->float:
    n=min(len(a),len(b),n+1); ra=[math.log(a[i]/a[i-1]) for i in range(len(a)-n+1,len(a))]; rb=[math.log(b[i]/b[i-1]) for i in range(len(b)-n+1,len(b))]
    ma,mb=statistics.fmean(ra),statistics.fmean(rb); den=math.sqrt(sum((x-ma)**2 for x in ra)*sum((y-mb)**2 for y in rb))
    return sum((x-ma)*(y-mb) for x,y in zip(ra,rb))/den if den else 0

def item(value, score, source, date, details=None):
    return {"value":round(value,3),"score":float(score),"source":source,"date":date,"status":"AUTO","details":details or {}}

def fred(series:str)->tuple[float,str]:
    req=Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",headers={"User-Agent":"Atlas/3.1"})
    with urlopen(req,timeout=15) as r: rows=list(csv.reader(io.StringIO(r.read().decode())))
    vals=[(d,float(v)) for d,v in rows[1:] if v not in (".","")]
    return vals[-1][1],vals[-1][0]

def build_payload() -> dict:
    data,warnings={},[]; series={}
    symbols=set(ASSETS)|{"XLY","XLP","SOXX","^VIX","^VIX3M","DX-Y.NYB","HG=F"}
    for symbol in symbols:
        try: series[symbol]=prices(symbol)
        except Exception as e: warnings.append(f"{symbol}: dato non disponibile ({type(e).__name__})")
    for symbol,name in ASSETS.items():
        if symbol not in series: continue
        xs,date=series[symbol]; score=trend_score(xs)
        data[f"asset_{symbol.lower()}"]=item(change(xs,126),score,f"Yahoo Finance: {symbol}",date,{"aboveSMA200":xs[-1]>statistics.fmean(xs[-200:]),"change6m":change(xs,126),"change12m":change(xs,252)})
    if "SPY" in series:
        xs,date=series["SPY"]; data["equity_trend"]=item(change(xs,126),trend_score(xs),"Yahoo Finance: SPY",date,{"aboveSMA200":xs[-1]>statistics.fmean(xs[-200:]),"change6m":change(xs,126),"change12m":change(xs,252)})
    risk=[data[k]["score"] for k in ["asset_spy","asset_efa","asset_eem","asset_iwm","asset_hyg"] if k in data]
    if risk: data["asset_momentum"]=item(statistics.fmean(risk),statistics.fmean(risk),"Yahoo Finance: trend multi-asset",datetime.now(timezone.utc).date().isoformat(),{"riskAssetsPositive":f"{sum(x>=75 for x in risk)}/{len(risk)}"})
    pairs={"credit_risk_appetite":("HYG","IEF"),"cyclical_defensive":("XLY","XLP"),"small_large":("IWM","SPY"),"semis_market":("SOXX","SPY")}
    for key,(a,b) in pairs.items():
        if a in series and b in series:
            c3,c6,above=relative(series[a][0],series[b][0]); score=25+25*sum([c3>0,c6>0,above]); data[key]=item(c3,score,f"Yahoo Finance: {a}/{b}",series[a][1],{"change3m":c3,"change6m":c6,"aboveSMA200":above})
    if "^VIX" in series and "^VIX3M" in series:
        v,v3=series["^VIX"][0][-1],series["^VIX3M"][0][-1]; spread=v3-v; data["vix_term"]=item(spread,100 if spread>2 else 75 if spread>0 else 25,"Yahoo Finance: VIX/VIX3M",series["^VIX"][1],{"vix":v,"vix3m":v3})
    if "SPY" in series and "TLT" in series:
        c=correlation(series["SPY"][0],series["TLT"][0]); data["stock_bond_corr"]=item(c,100 if c<-.3 else 75 if c<0 else 50 if c<.3 else 25,"Yahoo Finance: SPY/TLT",series["SPY"][1],{"correlation60d":c})
    fred_map={"yield_curve":("T10Y3M",lambda v:100 if v>0 else 25),"sahm":("SAHMREALTIME",lambda v:100 if v<.3 else 50 if v<.5 else 25),"hy":("BAMLH0A0HYM2",lambda v:100 if v<3 else 50 if v<5 else 25),"nfci":("NFCI",lambda v:100 if v<0 else 25),"breakeven_5y":("T5YIE",lambda v:75 if 1.5<=v<=3 else 50),"real_yield_10y":("DFII10",lambda v:75 if v<2 else 50)}
    for key,(sid,scorefn) in fred_map.items():
        try: v,date=fred(sid); data[key]=item(v,scorefn(v),f"FRED: {sid}",date)
        except Exception as e: warnings.append(f"{sid}: dato non disponibile ({type(e).__name__})")
    if not data:
        snapshot = json.loads((ROOT / "snapshot.json").read_text(encoding="utf-8"))
        snapshot["warnings"] = ["Aggiornamento online non riuscito: sono mostrati gli ultimi dati salvati"] + warnings
        return snapshot
    return {"updatedAt":datetime.now(timezone.utc).isoformat(timespec="seconds"),"warnings":warnings,"data":data}

def get_payload(force=False):
    if not force and _cache["payload"] and time.time()-_cache["at"]<CACHE_SECONDS: return _cache["payload"]
    payload=build_payload(); _cache.update(at=time.time(),payload=payload); return payload

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/data"):
            try: body=json.dumps(get_payload("refresh=1" in self.path),ensure_ascii=False).encode(); self.send_response(200)
            except Exception as e: body=json.dumps({"error":str(e)}).encode(); self.send_response(502)
            self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path=="/": self.path="/index.html"
        return super().do_GET()

if __name__=="__main__":
    os.chdir(ROOT); print(f"Atlas avviato su http://127.0.0.1:{PORT}"); ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
