"""Auditable market data with an explicit, separate synthetic demo provider.

The catalogue is a declaration supplied by the specification. Yahoo's metadata
can verify a listing, currency and product description; it does not routinely
prove an ISIN. That distinction is retained in every exported data set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = Path(os.environ.get("MOMENTUM_DATA_DIR", str(ROOT / "data"))) / "yahoo"
CATALOG_PATH = ROOT / "config" / "instruments.json"
CACHE_SCHEMA = 1


class DataValidationError(ValueError):
    """Data cannot safely be used as the requested EUR instrument series."""


@dataclass
class MarketData:
    adjusted: pd.DataFrame
    close: pd.DataFrame
    dividends: pd.DataFrame
    metadata: dict[str, Any]
    source: str
    updated_at: str
    warnings: list[str] = field(default_factory=list)
    # Yahoo Close is split-adjusted, but accounting requires historic tradable
    # prices AND the corresponding quantity changes on each split date.
    splits: pd.DataFrame | None = None


def instrument_catalog() -> list[dict[str, Any]]:
    """Return fresh catalogue records: declared ISINs are not provider proofs."""
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def _name_key(value: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", value.lower()).strip() + " "


def _validate_identity(instrument: dict, info: dict) -> dict:
    ticker = instrument["ticker"]
    if info.get("symbol") != ticker:
        raise DataValidationError(f"{ticker}: il provider non conferma il ticker esatto richiesto.")
    currency = info.get("currency")
    if currency != "EUR":
        raise DataValidationError(f"{ticker}: valuta della quotazione {currency!r}, richiesta EUR; nessuna conversione automatica.")
    exchange = info.get("exchange")
    if exchange != instrument["expected_exchange"]:
        raise DataValidationError(f"{ticker}: mercato {exchange!r}, atteso MIL (Milano).")
    quote_type = str(info.get("quoteType", "")).upper()
    if quote_type not in {"ETF", "ETC"}:
        raise DataValidationError(f"{ticker}: tipo di strumento {quote_type!r} non confermato come ETF/ETC.")
    name = info.get("longName") or info.get("shortName")
    if not isinstance(name, str) or not name.strip():
        raise DataValidationError(f"{ticker}: nome del prodotto non restituito; identità non verificabile.")
    name_key = _name_key(name)
    for alternatives in instrument["identity_groups"]:
        if not any(_name_key(term).strip() in name_key for term in alternatives):
            raise DataValidationError(f"{ticker}: nome provider {name!r} non coerente con {instrument['name']!r}.")
    reported_isin = info.get("isin") or info.get("ISIN")
    if reported_isin and reported_isin != instrument["isin"]:
        raise DataValidationError(f"{ticker}: ISIN provider {reported_isin!r} diverso da quello richiesto.")
    return {
        "requested_ticker": ticker,
        "declared_isin": instrument["isin"],
        "provider_isin": reported_isin,
        "isin_verified": reported_isin == instrument["isin"],
        "listing_verified": True,
        "identity_basis": "ticker, exchange, quote currency, type and provider product name",
        "provider_name": name,
        "provider_symbol": info["symbol"],
        "provider_currency": currency,
        "provider_exchange": exchange,
        "provider_quote_type": quote_type,
        "provider_exchange_timezone": info.get("exchangeTimezoneName"),
        "provider_first_trade_epoch": info.get("firstTradeDateEpochUtc"),
        "fx_conversion_applied": False,
    }


def _normalize_history(history: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if history is None or history.empty:
        raise DataValidationError(f"{ticker}: storico Yahoo non disponibile; nessun prodotto sostitutivo.")
    required = {"Close", "Adj Close"}
    if not required.issubset(history.columns):
        raise DataValidationError(f"{ticker}: mancano Close/Adj Close nello storico; reinvestimento non verificabile.")
    history = history.copy()
    index = pd.DatetimeIndex(history.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    history.index = index.normalize()
    history.index.name = "Date"
    if history.index.has_duplicates:
        raise DataValidationError(f"{ticker}: date giornaliere duplicate.")
    history = history.sort_index()
    for column in ["Close", "Adj Close", "Dividends", "Stock Splits"]:
        if column not in history:
            history[column] = 0.0
        history[column] = pd.to_numeric(history[column], errors="raise").astype(float)
    for column in ["Close", "Adj Close"]:
        values = history[column].dropna()
        if values.empty or not np.isfinite(values).all() or (values <= 0).any():
            raise DataValidationError(f"{ticker}: prezzi {column} mancanti, non finiti o non positivi.")
    if not history["Close"].notna().equals(history["Adj Close"].notna()):
        raise DataValidationError(f"{ticker}: Close e Adj Close hanno disponibilità diverse.")
    for column in ["Dividends", "Stock Splits"]:
        values = history[column]
        if values.isna().any() or not np.isfinite(values).all() or (values < 0).any():
            raise DataValidationError(f"{ticker}: eventi {column} mancanti o invalidi.")
    events_without_quote = ((history["Dividends"] > 0) | (history["Stock Splits"] > 0)) & history["Close"].isna()
    if events_without_quote.any():
        raise DataValidationError(f"{ticker}: evento societario senza quotazione verificabile.")
    return history


def _quality(history: pd.DataFrame, ticker: str) -> tuple[dict, list[str]]:
    close, adjusted = history["Close"], history["Adj Close"]
    dividends = history["Dividends"]
    warnings: list[str] = []
    # No pct_change default fill: gaps remain unusable for consecutive returns.
    quoted_return = close.pct_change(fill_method=None)
    adjusted_return = adjusted.pct_change(fill_method=None)
    expected_return = (close + dividends) / close.shift(1) - 1
    adjustment_factor = adjusted / close
    dividend_days = dividends > 0
    comparable_dividend_days = dividend_days & close.shift(1).notna()
    if comparable_dividend_days.any():
        factor_change = adjustment_factor / adjustment_factor.shift(1) - 1
        missing_adjustments = factor_change[comparable_dividend_days].abs() < 1e-8
        if missing_adjustments.any():
            raise DataValidationError(f"{ticker}: {int(missing_adjustments.sum())} distribuzioni presenti ma Adj Close non le incorpora; total return non verificato.")
    # Yahoo uses backward dividend factors, so a small discrepancy relative to
    # literal ex-day reinvestment is expected and is measured, never repaired.
    discrepancy = (adjusted_return - expected_return).abs()
    comparable_pairs = close.notna() & close.shift(1).notna()
    max_discrepancy = float(discrepancy.max()) if discrepancy.notna().any() else 0.0
    if max_discrepancy > 0.01:
        raise DataValidationError(f"{ticker}: serie adjusted incoerente con Close/distribuzioni (scarto giornaliero {max_discrepancy:.2%}).")
    if max_discrepancy > 0.002:
        warnings.append(f"{ticker}: scarto adjusted/total-return ex-dividendo fino a {max_discrepancy:.2%}; convenzione di rettifica Yahoo conservata.")
    unexplained_jumps = (adjusted_return.abs() > 0.40) & ~dividend_days
    if unexplained_jumps.any():
        warnings.append(f"{ticker}: {int(unexplained_jumps.sum())} variazioni adjusted giornaliere oltre 40%; verificare eventi e quotazioni prima dell'uso reale.")
    valid = close.dropna()
    interior_missing = int(close.loc[valid.index[0]:valid.index[-1]].isna().sum())
    if interior_missing:
        warnings.append(f"{ticker}: {interior_missing} prezzi giornalieri interni mancanti; nessun riempimento applicato.")
    return {
        "first_quote": valid.index[0].date().isoformat(),
        "last_quote": valid.index[-1].date().isoformat(),
        "observations": int(valid.size),
        "missing_prices_in_provider_rows": interior_missing,
        "distribution_events": int(dividend_days.sum()),
        "split_events": int((history["Stock Splits"] > 0).sum()),
        "price_integrity_checked": True,
        "adjusted_checked": bool(comparable_pairs.any()),
        "return_observations_checked": int(comparable_pairs.sum()),
        "max_daily_total_return_discrepancy": max_discrepancy,
        "unexplained_large_daily_moves": int(unexplained_jumps.sum()),
        "fill_applied": False,
    }, warnings


def _tradable_prices(history: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Undo Yahoo's future-split normalization, without changing adjusted TR.

    The split ratio applies on the event date. A 2:1 split multiplies all earlier
    quoted prices and per-share distributions by two; the event-day close is
    already the new unit. Accounting then doubles actual held quantities.
    """
    ratios = history["Stock Splits"].replace(0.0, 1.0)
    future_factor = ratios.iloc[::-1].cumprod().iloc[::-1] / ratios
    return history["Close"] * future_factor, history["Dividends"] * future_factor


def _build_dataset(histories: dict[str, pd.DataFrame], identities: dict, version: str, updated_at: str) -> MarketData:
    adjusted, close, dividends, splits = {}, {}, {}, {}
    instruments = {}
    warnings: list[str] = []
    split_events = []
    for instrument in instrument_catalog():
        ticker = instrument["ticker"]
        history = _normalize_history(histories[ticker], ticker)
        quality, instrument_warnings = _quality(history, ticker)
        warnings.extend(instrument_warnings)
        quoted_close, quoted_dividends = _tradable_prices(history)
        adjusted[ticker] = history["Adj Close"]
        close[ticker] = quoted_close
        dividends[ticker] = quoted_dividends
        splits[ticker] = history["Stock Splits"]
        instruments[ticker] = {**identities[ticker], "quality": quality}
        for date, ratio in history.loc[history["Stock Splits"] > 0, "Stock Splits"].items():
            split_events.append({"date": date.date().isoformat(), "ticker": ticker, "ratio": float(ratio)})
    frames = [pd.DataFrame(series).sort_index() for series in [adjusted, close, dividends, splits]]
    for frame in frames:
        frame.index.name = "Date"
    # Corporate-event absence is zero, price absence remains NaN. This is not
    # price padding: there is never a fabricated earlier/later quotation.
    frames[2] = frames[2].fillna(0.0)
    frames[3] = frames[3].fillna(0.0)
    for ticker in frames[0].columns:
        valid = frames[0][ticker].dropna()
        interior = frames[0].loc[valid.index[0]:valid.index[-1], ticker]
        missing = int(interior.isna().sum())
        instruments[ticker]["quality"]["missing_quotes_on_union_sessions"] = missing
        monthly_count = interior.resample("ME").count()
        instruments[ticker]["quality"]["empty_calendar_months"] = [d.date().isoformat() for d in monthly_count[monthly_count == 0].index]
        current_month = pd.Timestamp.now(tz="UTC").tz_localize(None).to_period("M")
        complete_observations = int(((monthly_count.index.to_period("M") < current_month) & (monthly_count > 0)).sum())
        instruments[ticker]["quality"]["complete_monthly_observations"] = complete_observations
        if complete_observations < 13:
            warnings.append(f"{ticker}: solo {complete_observations} chiusure mensili complete disponibili; servono 13 mesi consecutivi per il momentum. Lo strumento resta indisponibile, senza proxy automatici.")
        if missing > instruments[ticker]["quality"]["missing_prices_in_provider_rows"]:
            warnings.append(f"{ticker}: {missing} sedute dell'insieme di dati prive di prezzo; nessun forward-fill.")
        if not instruments[ticker]["isin_verified"]:
            warnings.append(f"{ticker}: ISIN {instruments[ticker]['declared_isin']} dichiarato nella specifica, non comprovato dal provider Yahoo.")
    metadata = {
        "provider": "Yahoo Finance via yfinance",
        "source": "yahoo",
        "currency": "EUR",
        "downloaded_at": updated_at,
        "yfinance_version": version,
        "catalogue_basis": "User-supplied strategy specification; declared ISINs are not automatically verified",
        "history_request": {"period": "max", "auto_adjust": False, "actions": True, "repair": False},
        "adjusted_convention": "Yahoo Adj Close; dividend and split adjusted, no additional dividends in theoretical returns",
        "close_convention": "Yahoo Close multiplied by cumulative subsequent split ratios to recover historically tradable units",
        "dividends_convention": "Yahoo distribution per share multiplied by cumulative subsequent split ratios",
        "split_convention": "Ratio applied to quantities and average cost on event date, before distributions and orders",
        "fx_conversion_applied": False,
        "price_fill_applied": False,
        "instruments": instruments,
        "split_events": sorted(split_events, key=lambda e: (e["date"], e["ticker"])),
    }
    return MarketData(frames[0], frames[1], frames[2], metadata, "yahoo", updated_at, warnings, frames[3])


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_snapshot(data: MarketData, histories: dict[str, pd.DataFrame], cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
    staging = cache_dir / (".pending-" + snapshot_name)
    snapshot = cache_dir / snapshot_name
    staging.mkdir()
    try:
        raw_dir = staging / "raw"
        raw_dir.mkdir()
        for ticker, history in histories.items():
            _normalize_history(history, ticker).to_csv(raw_dir / f"{ticker}.csv", float_format="%.17g")
        for name in ["adjusted", "close", "dividends", "splits"]:
            frame = getattr(data, name)
            if frame is not None:
                frame.to_csv(staging / f"{name}.csv", float_format="%.17g")
        files = {str(path.relative_to(staging)): _hash(path) for path in sorted(staging.rglob("*.csv"))}
        manifest = {
            "schema_version": CACHE_SCHEMA,
            "source": data.source,
            "updated_at": data.updated_at,
            "metadata": data.metadata,
            "warnings": data.warnings,
            "files_sha256": files,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=_plain), encoding="utf-8")
        staging.rename(snapshot)
        pointer = cache_dir / (".latest-" + uuid.uuid4().hex + ".json")
        pointer.write_text(json.dumps({"snapshot": snapshot_name, "manifest_sha256": _hash(snapshot / "manifest.json")}), encoding="utf-8")
        pointer.replace(cache_dir / "latest.json")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load_cached(cache_dir: Path | str | None = None) -> MarketData | None:
    """Load the last fully validated Yahoo snapshot, checking stored hashes."""
    directory = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    pointer_path = directory / "latest.json"
    if not pointer_path.exists():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        snapshot_name = pointer["snapshot"]
        if not isinstance(snapshot_name, str) or Path(snapshot_name).name != snapshot_name or snapshot_name.startswith("."):
            raise ValueError("invalid snapshot path")
        snapshot = directory / snapshot_name
        manifest_path = snapshot / "manifest.json"
        if _hash(manifest_path) != pointer["manifest_sha256"]:
            raise ValueError("manifest checksum mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != CACHE_SCHEMA or manifest["source"] != "yahoo":
            raise ValueError("unsupported cache schema or source")
        for name, expected_hash in manifest["files_sha256"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid file path")
            if _hash(snapshot / relative) != expected_hash:
                raise ValueError(f"checksum mismatch: {name}")
        frames = {name: pd.read_csv(snapshot / f"{name}.csv", index_col=0, parse_dates=True, float_precision="round_trip").astype(float) for name in ["adjusted", "close", "dividends", "splits"]}
        expected_columns = [instrument["ticker"] for instrument in instrument_catalog()]
        for name, frame in frames.items():
            frame.index = pd.DatetimeIndex(frame.index).normalize()
            if list(frame.columns) != expected_columns or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
                raise ValueError(f"invalid frame: {name}")
            if not frame.index.equals(frames["adjusted"].index):
                raise ValueError(f"unaligned frame: {name}")
        metadata = manifest["metadata"]
        metadata["cache_snapshot"] = snapshot_name
        metadata["cache_integrity"] = "SHA-256 manifest and all saved CSV files checked"
        return MarketData(frames["adjusted"], frames["close"], frames["dividends"], metadata, "yahoo", manifest["updated_at"], manifest["warnings"], frames["splits"])
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise DataValidationError(f"Cache Yahoo non leggibile o non integra: {exc}") from exc


def refresh_yahoo(cache_dir: Path | str | None = None) -> MarketData:
    """Fetch and validate all fourteen exact listings; never substitute demo."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise DataValidationError("yfinance non installato; eseguire lo script di installazione.") from exc
    directory = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    # Keep yfinance's local caches in the writable, ignored data directory.
    yf_cache = directory.parent / "yfinance-cache"
    yf_cache.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(yf_cache))
    histories: dict[str, pd.DataFrame] = {}
    identities: dict[str, dict] = {}
    for instrument in instrument_catalog():
        ticker = instrument["ticker"]
        try:
            listing = yf.Ticker(ticker)
            identities[ticker] = _validate_identity(instrument, listing.get_info())
            histories[ticker] = listing.history(period="max", auto_adjust=False, actions=True, repair=False, raise_errors=True)
            _normalize_history(histories[ticker], ticker)
            if hasattr(listing, "get_history_metadata"):
                history_metadata = listing.get_history_metadata()
                identities[ticker]["provider_history_metadata"] = {
                    key: _plain(history_metadata.get(key))
                    for key in ["symbol", "currency", "exchangeName", "instrumentType", "firstTradeDate", "validRanges", "dataGranularity", "exchangeTimezoneName", "regularMarketTime"]
                }
        except DataValidationError:
            raise
        except Exception as exc:
            # Do not return partially downloaded instruments or an earlier cache
            # as a successful fresh request. A previous valid cache is preserved.
            raise DataValidationError(f"{ticker}: download Yahoo fallito ({type(exc).__name__}: {exc}). Verificare accesso a query1.finance.yahoo.com e query2.finance.yahoo.com; nessuna sostituzione automatica.") from exc
    data = _build_dataset(histories, identities, yf.__version__, _utc_now())
    _save_snapshot(data, histories, directory)
    return data


def load_demo() -> MarketData:
    """Deterministic fictitious EUR prices, not a historic/live market backtest.

    A stable seed and date-specific model create equity, bond and commodity-like
    paths, with distinct inceptions and explicit cash distributions. Adjusted
    prices compound (close_t + distribution_t) / close_(t-1) exactly once.
    """
    instruments = instrument_catalog()
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    sessions = pd.bdate_range("2011-01-03", today)
    # Keep all historical values deterministic when the demo extends by a day.
    rng = np.random.default_rng(27811)
    noise = rng.normal(size=(len(sessions), len(instruments) + 1))
    elapsed = np.arange(len(sessions), dtype=float)
    cyclical = 0.00025 * np.sin(elapsed / 220.0) + 0.00014 * np.cos(elapsed / 75.0)
    shock = np.zeros(len(sessions))
    for start, end, daily_shock in [("2015-08-10", "2015-09-04", -0.005), ("2020-02-24", "2020-03-23", -0.015), ("2020-04-01", "2020-06-30", 0.003), ("2022-01-10", "2022-10-14", -0.0013)]:
        shock[(sessions >= start) & (sessions <= end)] = daily_shock
    starts = ["2011-01-03", "2011-02-01", "2011-03-01", "2012-04-02", "2011-05-02", "2012-07-02", "2011-07-01", "2011-08-01", "2011-09-01", "2014-06-02", "2011-11-01", "2016-03-01", "2011-01-03", "2017-11-01"]
    volatilities = [.00013, .0018, .007, .0038, .0048, .0058, .010, .011, .014, .012, .010, .013, .010, .004]
    drifts = [.000035, .000075, .000095, .000105, .000145, .00014, .00030, .00037, .00045, .00025, .00022, .00016, .00032, .000085]
    yields = [0, .015, .022, .025, .035, .035, 0, 0, .008, 0, 0, 0, 0, 0]
    adjusted, close, dividends = {}, {}, {}
    metadata_instruments = {}
    for k, instrument in enumerate(instruments):
        ticker = instrument["ticker"]
        active = sessions >= starts[k]
        active_sessions = sessions[active]
        market_weight = 0.0 if k == 0 else (0.75 if k in [6, 7, 8, 9, 12] else 0.3)
        returns = drifts[k] + volatilities[k] * (0.55 * noise[active, 0] + 0.835 * noise[active, k + 1])
        if k:
            returns += cyclical[active] * (1.5 if k in [6, 7, 8, 9, 12] else 0.7) + shock[active] * market_weight
        if k in [2, 3, 13]:
            returns[(active_sessions >= "2022-01-10") & (active_sessions <= "2022-10-14")] -= .00065
        returns = np.maximum(returns, -0.25)
        prices = np.empty(len(active_sessions))
        payouts = np.zeros(len(active_sessions))
        tr = np.empty(len(active_sessions))
        prices[0] = 40.0 + k * 12.0
        tr[0] = prices[0]
        for j in range(1, len(active_sessions)):
            date = active_sessions[j]
            # First weekday on/after the 15th of Mar/Jun/Sep/Dec.
            ex_day = date.month in {3, 6, 9, 12} and date.day >= 15 and active_sessions[j - 1].day < 15
            payouts[j] = prices[j - 1] * yields[k] / 4.0 if ex_day else 0.0
            prices[j] = prices[j - 1] * (1.0 + returns[j]) - payouts[j]
            tr[j] = tr[j - 1] * ((prices[j] + payouts[j]) / prices[j - 1])
        adjusted[ticker] = pd.Series(tr, index=active_sessions)
        close[ticker] = pd.Series(prices, index=active_sessions)
        dividends[ticker] = pd.Series(payouts, index=active_sessions)
        metadata_instruments[ticker] = {
            "declared_isin": instrument["isin"],
            "isin_verified": False,
            "listing_verified": False,
            "source": "synthetic demo",
            "first_quote": active_sessions[0].date().isoformat(),
            "last_quote": active_sessions[-1].date().isoformat(),
            "currency": "EUR",
            "quality": {"adjusted_checked": True, "fill_applied": False, "distribution_events": int((payouts > 0).sum()), "split_events": 0},
        }
    adjusted_frame = pd.DataFrame(adjusted, index=sessions)
    close_frame = pd.DataFrame(close, index=sessions)
    dividend_frame = pd.DataFrame(dividends, index=sessions).fillna(0.0)
    splits = pd.DataFrame(0.0, index=sessions, columns=close_frame.columns)
    for frame in [adjusted_frame, close_frame, dividend_frame, splits]:
        frame.index.name = "Date"
    metadata = {
        "provider": "Deterministic synthetic demonstration",
        "source": "demo",
        "currency": "EUR",
        "synthetic": True,
        "seed": 27811,
        "generated_through": sessions[-1].date().isoformat(),
        "generator_version": 1,
        "adjusted_convention": "Exact cumulative reinvestment of synthetic per-share distributions",
        "close_convention": "Synthetic tradable ex-distribution closing prices; no stock splits generated",
        "fx_conversion_applied": False,
        "price_fill_applied": False,
        "instruments": metadata_instruments,
        "split_events": [],
    }
    return MarketData(adjusted_frame, close_frame, dividend_frame, metadata, "demo", _utc_now(), ["DEMO: tutte le quotazioni, distribuzioni e performance sono sintetiche; non rappresentano rendimenti storici o dati di mercato reali.", "L'anagrafica deriva dalla specifica; ISIN e quotazioni della demo non costituiscono verifica degli strumenti reali.", "La demo usa giorni feriali generici e non riproduce il calendario festivo effettivo di Milano."], splits)
