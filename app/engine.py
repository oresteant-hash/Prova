"""Auditable monthly momentum signals and an idealized total-return backtest.

Signals formed at month t's close are applied only to month t+1.  This is
an idealized close-to-close model; the operational ledger lives in accounting.
The month containing ``asof`` is never certified complete by a date alone.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import numpy as np
import pandas as pd
import exchange_calendars as exchange

from .data import MarketData, instrument_catalog
from .models import StrategyConfig


def _asof(value: Any = None) -> pd.Timestamp:
    if value is None:
        value = datetime.now(ZoneInfo("Europe/Rome")).date()
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("La data di osservazione non è valida.")
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Europe/Rome").tz_localize(None)
    return stamp.normalize()


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("I prezzi richiedono un indice DatetimeIndex.")
    if frame.index.has_duplicates:
        raise ValueError("I prezzi contengono date duplicate.")
    result = frame.sort_index().copy()
    if result.index.tz is not None:
        result.index = result.index.tz_localize(None)
    result.index = result.index.normalize()
    if result.index.has_duplicates:
        raise ValueError("I prezzi contengono più osservazioni nella stessa giornata.")
    return result.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)


def monthly_prices(adjusted: pd.DataFrame, asof: Any = None) -> pd.DataFrame:
    """Last observed adjusted closes, excluding the unverified as-of month.

    Each ticker must have an observation at or after that month's final XMIL
    exchange session. Stale mid-month observations never become month-end
    closes. Empty interior months remain NaN; wholly incomplete trailing months
    are removed. No forward fill or retroactive fill is used. Even an as-of
    date on the last calendar day does not assert that the exchange has closed.
    """
    today = _asof(asof)
    daily = _frame(adjusted)
    cutoff = today.replace(day=1)
    daily = daily.loc[daily.index < cutoff]
    if daily.empty:
        return pd.DataFrame(columns=adjusted.columns, index=pd.DatetimeIndex([], name="date"), dtype=float)
    monthly = daily.resample("ME").last()
    calendar = exchange.get_calendar("XMIL", start=monthly.index[0].replace(day=1), end=monthly.index[-1])
    # Constructor bounds may themselves be exchange holidays. Its session index
    # already covers the requested range, without parsing those as sessions.
    sessions = calendar.sessions
    if sessions.tz is not None:
        sessions = sessions.tz_localize(None)
    expected = pd.Series(sessions, index=sessions).resample("ME").last().reindex(monthly.index)
    observation_dates = pd.Series(daily.index, index=daily.index)
    coverage = pd.DataFrame({ticker: observation_dates.where(daily[ticker].notna()).resample("ME").last()
                             for ticker in daily.columns}).reindex(monthly.index)
    monthly = monthly.where(coverage.ge(expected, axis=0))
    nonempty = monthly.notna().any(axis=1)
    if nonempty.any():
        monthly = monthly.loc[:nonempty[nonempty].index[-1]]
    else:
        monthly = monthly.iloc[:0]
    monthly.index.name = "date"
    return monthly


def _assets() -> list[dict]:
    return sorted((item for item in instrument_catalog() if item["role"] == "asset"), key=lambda item: item["id"])


def _number(value: Any) -> float | None:
    try:
        converted = float(value)
        return converted if np.isfinite(converted) else None
    except (TypeError, ValueError):
        return None


def compute_signal(monthly: pd.DataFrame, config: StrategyConfig, at: Any = None) -> dict:
    """Rank the fixed universe using 13 consecutive, valid month-end closes."""
    prices = _frame(monthly)
    if not prices.empty:
        if not prices.index.is_month_end.all():
            raise ValueError("Il segnale richiede chiusure indicizzate a fine mese.")
    if at is None:
        if prices.empty:
            raise ValueError("Nessuna chiusura mensile disponibile per il segnale.")
        date = prices.index[-1]
    else:
        date = _asof(at)
        if not date.is_month_end:
            raise ValueError("La data del segnale deve essere una fine mese.")
    window_index = pd.date_range(end=date, periods=13, freq="ME")
    window = prices.reindex(window_index)
    coefficients = (.5, .3, .2) if config.formula == "reactive" else (1 / 3, 1 / 3, 1 / 3)
    rows: list[dict] = []
    for asset in _assets():
        ticker = asset["ticker"]
        series = window[ticker] if ticker in window else pd.Series(np.nan, index=window_index)
        values = series.to_numpy(dtype=float)
        valid = bool(np.isfinite(values).all() and (values > 0).all())
        row = {
            "id": asset["id"], "ticker": ticker, "label": asset["label"],
            "price": _number(values[-1]), "r3": None, "r6": None,
            "r12": None, "score": None, "ma12": None, "volatility": None,
            "available": valid, "eligible": False, "selected": False,
            "rank": None, "reason": "Servono 13 chiusure mensili consecutive, positive e valide.",
        }
        if valid:
            r3, r6, r12 = (values[-1] / values[-1 - horizon] - 1 for horizon in (3, 6, 12))
            score = sum(coefficient * ret for coefficient, ret in zip(coefficients, (r3, r6, r12)))
            volatility = float(np.std(values[1:] / values[:-1] - 1, ddof=1))
            row.update(r3=float(r3), r6=float(r6), r12=float(r12), score=float(score),
                       ma12=float(values[-12:].mean()), volatility=volatility)
            if config.weighting == "inverse_vol" and (not np.isfinite(volatility) or volatility <= 0):
                row.update(available=False, reason="Volatilità campionaria nulla o non calcolabile.")
            elif score <= 0:
                row["reason"] = "Score non strettamente positivo."
            elif config.ma_filter and values[-1] <= row["ma12"]:
                row["reason"] = "Prezzo non superiore alla media delle ultime 12 chiusure."
            else:
                row.update(eligible=True, reason="Eleggibile.")
        rows.append(row)
    rows.sort(key=lambda row: (not row["available"], -row["score"] if row["available"] else 0, row["id"]))
    for rank, row in enumerate((row for row in rows if row["available"]), start=1):
        row["rank"] = rank
    eligible = [row for row in rows if row["eligible"]]
    selected = eligible[:config.top_n]
    available_count = sum(row["available"] for row in rows)
    required = len(rows) if config.universe_mode == "complete" else config.min_assets
    fallback_row = next(row for row in rows if row["ticker"] == config.fallback)
    fallback_valid = fallback_row["price"] is not None and fallback_row["price"] > 0
    tradable = available_count >= required and fallback_valid
    weights: dict[str, float] = {}
    if tradable:
        if selected:
            if config.weighting == "equal":
                relative = np.ones(len(selected), dtype=float)
            elif config.weighting == "inverse_vol":
                volatilities = np.array([row["volatility"] for row in selected], dtype=float)
                relative = volatilities.min() / volatilities
            else:
                relative = np.array([row["score"] for row in selected], dtype=float)
            # Scale before normalization to keep arbitrary absolute magnitudes safe.
            relative /= relative.max()
            relative /= relative.sum()
            quota = len(selected) / config.top_n
            weights = {row["ticker"]: float(quota * weight) for row, weight in zip(selected, relative)}
        else:
            quota = 0.0
        weights[config.fallback] = weights.get(config.fallback, 0.0) + 1 - quota
        weights[config.fallback] += 1 - sum(weights.values())
        weights = {ticker: weight for ticker, weight in weights.items() if weight > 0}
    for row in selected:
        row["selected"] = True
        row["reason"] = "Selezionato." if tradable else "In graduatoria; universo non ancora negoziabile."
    return {
        "signal_date": date.date().isoformat(), "tradable": bool(tradable),
        "available_count": int(available_count), "eligible_count": len(eligible),
        "selected": [row["ticker"] for row in selected], "weights": weights,
        "rows": rows, "required_count": required,
        "readiness_reason": ("Pronto." if tradable else
                             "Prezzo del fallback assente o non valido." if not fallback_valid else
                             f"Disponibili {available_count} strumenti su {required} richiesti."),
    }


def _monthly_return(prices: pd.DataFrame, date: pd.Timestamp, previous: pd.Timestamp, ticker: str) -> float:
    try:
        start, end = float(prices.at[previous, ticker]), float(prices.at[date, ticker])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Prezzi mancanti per {ticker} nel mese {date:%Y-%m}.") from exc
    if not (np.isfinite(start) and np.isfinite(end) and start > 0 and end > 0):
        raise ValueError(f"Prezzi mancanti o non validi per {ticker} nel mese {date:%Y-%m}.")
    return end / start - 1


def _turnover(targets: dict[str, float], drifted: dict[str, float]) -> float:
    return .5 * sum(abs(targets.get(ticker, 0.0) - drifted.get(ticker, 0.0)) for ticker in targets.keys() | drifted.keys())


def _metrics(returns: list[float], initial: float, turnover: list[float]) -> dict:
    from .analytics import metrics
    return metrics(returns, initial, turnover=turnover)


def _date_limits(config: StrategyConfig) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start = pd.Timestamp(config.start_date) if config.start_date else None
    end = pd.Timestamp(config.end_date) if config.end_date else None
    if start is not None:
        # A mid-month request begins with the next whole calendar month.
        start = start if start.day == 1 else (start.to_period("M") + 1).start_time
    if end is not None:
        end = end if end.is_month_end else end.replace(day=1) - pd.Timedelta(days=1)
    return start, end


def run_backtest(data: MarketData, config: StrategyConfig, asof: Any = None) -> dict:
    """Monthly gross strategy plus global equity and annual 60/40 benchmarks.

    The common period begins when the selected universe and both benchmarks
    have usable history. Once invested, missing held-asset returns or a gap in
    the required signal data raise an error instead of silently skipping time.
    """
    observed_at = _asof(asof)
    daily = _frame(data.adjusted)
    daily = daily.loc[daily.index <= observed_at].dropna(how="all")
    monthly = monthly_prices(daily, observed_at)
    if len(monthly) < 14:
        raise ValueError("Storia insufficiente: occorrono 13 chiusure per il primo segnale e il successivo mese completo di rendimento.")
    required_benchmarks = {"SWDA.MI", "AGGH.MI"}
    if not required_benchmarks.issubset(monthly.columns):
        raise ValueError("Per il confronto sul periodo comune servono SWDA.MI e AGGH.MI.")
    start_limit, end_limit = _date_limits(config)
    signal_cache: dict[pd.Timestamp, dict] = {}
    months: list[dict] = []
    signal_records: list[dict] = []
    equity = world_equity = balanced_equity = float(config.initial_capital)
    drifted: dict[str, float] = {"CASH": 1.0}
    balanced_drift: dict[str, float] = {"CASH": 1.0}
    world_turnovers: list[float] = []
    balanced_turnovers: list[float] = []
    waiting_reason = "Nessun mese con storia comune sufficiente."
    for position in range(1, len(monthly)):
        date, signal_date = monthly.index[position], monthly.index[position - 1]
        first_day = date.replace(day=1)
        if start_limit is not None and first_day < start_limit:
            continue
        if end_limit is not None and date > end_limit:
            break
        if signal_date not in signal_cache:
            signal_cache[signal_date] = compute_signal(monthly, config, signal_date)
        signal = signal_cache[signal_date]
        if not signal["tradable"]:
            waiting_reason = signal["readiness_reason"]
            if months:
                raise ValueError(f"Segnale non negoziabile a {signal_date:%Y-%m-%d}: {waiting_reason} Lo storico non viene saltato.")
            continue
        try:
            world_return = _monthly_return(monthly, date, signal_date, "SWDA.MI")
            bond_return = _monthly_return(monthly, date, signal_date, "AGGH.MI")
        except ValueError:
            if months:
                raise
            waiting_reason = "Storia dei benchmark non ancora disponibile sul periodo comune."
            continue
        targets = signal["weights"]
        asset_returns = {ticker: _monthly_return(monthly, date, signal_date, ticker) for ticker in targets}
        portfolio_return = float(sum(targets[ticker] * ret for ticker, ret in asset_returns.items()))
        turnover = _turnover(targets, drifted)
        trades = [{"ticker": ticker, "weight_before": drifted.get(ticker, 0.0),
                   "target_weight": targets.get(ticker, 0.0),
                   "weight_change": targets.get(ticker, 0.0) - drifted.get(ticker, 0.0)}
                  for ticker in sorted(targets.keys() | drifted.keys())
                  if targets.get(ticker, 0.0) != drifted.get(ticker, 0.0)]
        balanced_targets = {"SWDA.MI": .6, "AGGH.MI": .4} if not months or date.month == 1 else balanced_drift.copy()
        balanced_turnover = _turnover(balanced_targets, balanced_drift)
        balanced_return = balanced_targets["SWDA.MI"] * world_return + balanced_targets["AGGH.MI"] * bond_return
        equity *= 1 + portfolio_return
        world_equity *= 1 + world_return
        balanced_equity *= 1 + balanced_return
        months.append({
            "date": date.date().isoformat(), "signal_date": signal["signal_date"],
            "weights": targets.copy(), "drifted_weights": drifted.copy(), "trades": trades,
            "turnover": turnover, "asset_returns": asset_returns,
            "return": portfolio_return, "equity": equity,
            "benchmark_world_return": world_return, "benchmark_6040_return": float(balanced_return),
            "benchmark_world_equity": world_equity, "benchmark_6040_equity": balanced_equity,
            "benchmark_6040_weights": balanced_targets.copy(), "benchmark_6040_turnover": balanced_turnover,
        })
        signal_records.append(signal)
        world_turnovers.append(1.0 if len(months) == 1 else 0.0)
        balanced_turnovers.append(balanced_turnover)
        drifted = {ticker: weight * (1 + asset_returns[ticker]) / (1 + portfolio_return) for ticker, weight in targets.items()}
        balanced_drift = {"SWDA.MI": balanced_targets["SWDA.MI"] * (1 + world_return) / (1 + balanced_return),
                          "AGGH.MI": balanced_targets["AGGH.MI"] * (1 + bond_return) / (1 + balanced_return)}
    if not months:
        raise ValueError(f"Nessun mese completo negoziabile nel periodo richiesto. {waiting_reason}")

    last_complete = monthly.index[-1]
    current = signal_cache.get(last_complete) or compute_signal(monthly, config, last_complete)
    provisional = None
    latest_date = daily.index[-1]
    latest_bucket = latest_date.to_period("M").end_time.normalize()
    if latest_bucket > last_complete:
        interim = monthly.copy()
        interim.loc[latest_bucket] = daily.loc[daily.index.to_period("M") == latest_date.to_period("M")].iloc[-1]
        provisional = compute_signal(interim.sort_index(), config, latest_bucket)
        provisional.update(signal_date=latest_date.date().isoformat(),
                           month_bucket=latest_bucket.date().isoformat(), provisional=True,
                           as_of=latest_date.date().isoformat())
    warnings = list(data.warnings)
    prior_month_data = daily.loc[daily.index < observed_at.replace(day=1)]
    if not prior_month_data.empty and prior_month_data.index[-1].to_period("M") > last_complete.to_period("M"):
        warnings.append("L'ultimo mese concluso nel calendario non dispone della chiusura finale XMIL: è escluso dallo storico e l'allocazione rimane riferita all'ultimo mese certificato.")
    warnings.extend([
        "Backtest lordo teorico: ribilanciamento ai prezzi di fine mese, senza ritardo di esecuzione, costi o imposte dell'investitore.",
        "Volatilità e drawdown sono calcolati su rendimenti e valorizzazioni mensili; CAGR usa solo mesi interi.",
        "Turnover: metà della somma delle variazioni assolute dei pesi dopo il drift, inclusa la liquidità iniziale.",
        "Benchmark 60/40 ribilanciato all'inizio di gennaio; negli altri mesi i pesi fluttuano.",
    ])
    if config.rebalance_band or config.commission_bps or config.spread_bps:
        warnings.append("Banda, commissioni e spread sono applicati nella simulazione operativa separata; la curva Momentum lordo rimane ai pesi target mensili.")
    if config.start_date and pd.Timestamp(config.start_date).day != 1:
        warnings.append("La data iniziale cade a mese iniziato: il backtest parte dal successivo mese intero.")
    if config.end_date and not pd.Timestamp(config.end_date).is_month_end:
        warnings.append("La data finale cade a mese iniziato: il backtest termina all'ultima fine mese precedente.")
    latest_return: float | None = None
    latest_equity: float | None = None
    if current["tradable"]:
        latest_returns = {}
        for ticker in current["weights"]:
            base = _number(monthly.at[last_complete, ticker])
            quote = _number(daily.at[latest_date, ticker])
            if base is None or quote is None or base <= 0 or quote <= 0:
                warnings.append(f"Performance provvisoria indisponibile: manca la quotazione aggiornata di {ticker}.")
                latest_returns = {}
                break
            latest_returns[ticker] = quote / base - 1
        if latest_returns:
            latest_return = float(sum(current["weights"][ticker] * ret for ticker, ret in latest_returns.items()))
            if pd.Timestamp(months[-1]["date"]) == last_complete:
                latest_equity = equity * (1 + latest_return)
            else:
                warnings.append("Il periodo storico termina prima dell'ultima fine mese: la valorizzazione corrente del capitale simulato non è calcolata.")
    if latest_date < observed_at - pd.Timedelta(days=7):
        warnings.append(f"Quotazioni ferme al {latest_date:%Y-%m-%d}; l'allocazione è riferita all'ultima chiusura disponibile, non a dati aggiornati.")
    return {
        "months": months, "signals": signal_records,
        "metrics": {
            "strategy": _metrics([row["return"] for row in months], config.initial_capital, [row["turnover"] for row in months]),
            "world": _metrics([row["benchmark_world_return"] for row in months], config.initial_capital, world_turnovers),
            "balanced": _metrics([row["benchmark_6040_return"] for row in months], config.initial_capital, balanced_turnovers),
        },
        "current_allocation": current, "provisional_signal": provisional,
        "live_performance": {"as_of": latest_date.date().isoformat(), "signal_date": current["signal_date"],
                             "return_since_month_end": latest_return, "estimated_equity": latest_equity,
                             "complete_months": len(months)},
        "warnings": list(dict.fromkeys(warnings)),
        "period": {"start": pd.Timestamp(months[0]["date"]).replace(day=1).date().isoformat(),
                   "end": months[-1]["date"], "months": len(months)},
    }


def targets_from_backtest(result: dict) -> pd.DataFrame:
    """Theoretical signal targets for the exact common backtest interval."""
    records = {pd.Timestamp(row["signal_date"]): row["weights"] for row in result["months"]}
    frame = pd.DataFrame.from_dict(records, orient="index").fillna(0.0).sort_index()
    frame.index.name = "signal_date"
    return frame
