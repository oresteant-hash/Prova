"""Transparent performance calculations for modeled and actual portfolios.

Monthly strategy returns, daily account equity and cash-flow-adjusted account
returns deliberately have separate entry points and annualization conventions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import math
from typing import Iterable

import numpy as np
import pandas as pd


def _finite_number(value, name: str) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} deve essere un numero finito.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} deve essere un numero finito.")
    return result


def _finite_sum(values, name: str) -> float:
    try:
        result = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"{name} supera la precisione numerica disponibile.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} supera la precisione numerica disponibile.")
    return result


def _annual_growth(ratio: float, multiplier: float) -> float:
    if ratio == 0:
        return -1.0
    try:
        return math.expm1(math.log(ratio) * multiplier)
    except OverflowError as exc:
        raise ValueError("Il rendimento annualizzato supera la precisione numerica disponibile.") from exc


def _turnover_total(turnover) -> float:
    if turnover is None:
        return 0.0
    if np.isscalar(turnover):
        values = [_finite_number(turnover, "turnover")]
    else:
        values = [_finite_number(value, "turnover") for value in turnover]
    if any(value < 0 for value in values):
        raise ValueError("Il turnover non può essere negativo.")
    return _finite_sum(values, "Turnover")


def _max_drawdown(values: np.ndarray, initial_capital: float) -> float:
    path = np.concatenate(([initial_capital], values))
    running_max = np.maximum.accumulate(path)
    return float(np.min(path / running_max - 1.0))


def metrics(returns: Iterable[float], initial_capital: float, turnover=None) -> dict:
    """Metrics from complete monthly returns; no incomplete month is added.

    Drawdown includes the initial investment, and volatility is the sample
    standard deviation (ddof=1), annualized with sqrt(12). Empty histories have
    undefined CAGR/volatility rather than an invented annual return.
    """
    initial = _finite_number(initial_capital, "Capitale iniziale")
    if initial <= 0:
        raise ValueError("Il capitale iniziale deve essere positivo.")
    monthly = np.array([_finite_number(v, "Rendimento mensile") for v in returns])
    if np.any(monthly < -1):
        raise ValueError("Un rendimento long-only non può essere inferiore a -100%.")
    count = int(len(monthly))
    growth = np.cumprod(1.0 + monthly)
    if not np.all(np.isfinite(growth)):
        raise ValueError("La curva dei rendimenti supera la precisione numerica disponibile.")
    terminal = float(growth[-1]) if count else 1.0
    cagr = _annual_growth(terminal, 12 / count) if count else None
    return {
        "cumulative_return": terminal - 1.0,
        "cagr": cagr,
        "annualized_volatility": float(np.std(monthly, ddof=1) * math.sqrt(12)) if count > 1 else None,
        "max_drawdown": _max_drawdown(growth * initial, initial),
        "turnover": _turnover_total(turnover),
        "total_costs": 0.0,
        "total_taxes": 0.0,
        "months": count,
        "return_frequency": "monthly_complete",
        "annualization_basis": "integer_months_12_over_M",
        "drawdown_frequency": "monthly_including_initial_capital",
        "volatility_ddof": 1,
    }


def daily_metrics(equity: pd.Series, initial_capital: float, turnover=None, start_date=None) -> dict:
    """Metrics for a daily operational equity curve, with actual calendar CAGR.

    Daily volatility assumes trading-session observations and 252 sessions/year.
    Pass the investment's real start_date if it precedes the first valuation.
    """
    initial = _finite_number(initial_capital, "Capitale iniziale")
    if initial <= 0 or not isinstance(equity, pd.Series) or equity.empty:
        raise ValueError("Servono capitale positivo e una serie patrimoniale giornaliera non vuota.")
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(equity.index)).tz_localize(None).normalize()
    except (ValueError, TypeError) as exc:
        raise ValueError("Le valorizzazioni devono avere date valide.") from exc
    if dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("Le valorizzazioni devono avere date uniche e crescenti.")
    values = np.array([_finite_number(v, "Patrimonio") for v in equity.values])
    if np.any(values < 0):
        raise ValueError("Il patrimonio non può essere negativo.")
    beginning = pd.Timestamp(start_date).tz_localize(None).normalize() if start_date is not None else dates[0]
    if pd.isna(beginning) or beginning > dates[0]:
        raise ValueError("La data iniziale non può seguire la prima valorizzazione.")
    days = int((dates[-1] - beginning).days)
    previous = np.concatenate(([initial], values[:-1]))
    if np.any(previous <= 0):
        raise ValueError("I rendimenti dopo un patrimonio nullo non sono calcolabili.")
    daily_returns = values / previous - 1.0
    ratio = float(values[-1] / initial)
    cagr = _annual_growth(ratio, 365.25 / days) if days else None
    return {
        "cumulative_return": ratio - 1.0,
        "cagr": cagr,
        "annualized_volatility": float(np.std(daily_returns, ddof=1) * math.sqrt(252)) if len(values) > 1 else None,
        "max_drawdown": _max_drawdown(values, initial),
        "turnover": _turnover_total(turnover),
        "total_costs": 0.0,
        "total_taxes": 0.0,
        "observations": len(values),
        "calendar_days": days,
        "return_frequency": "daily_trading_sessions",
        "annualization_basis": "actual_calendar_days_365.25",
        "drawdown_frequency": "daily_including_initial_capital",
        "volatility_ddof": 1,
        "volatility_sessions_per_year": 252,
        "start": beginning.date().isoformat(),
        "end": dates[-1].date().isoformat(),
    }


def _date(value, name="Data") -> date:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} deve essere una data ISO YYYY-MM-DD.") from exc


def _stable_npv(coefficients: np.ndarray, times: np.ndarray, z: float) -> float:
    """NPV scaled by its largest absolute term; preserves sign without overflow."""
    exponents = np.log(np.abs(coefficients)) - times * z
    largest = float(np.max(exponents))
    return math.fsum(float(sign * math.exp(float(exponent) - largest)) for sign, exponent in zip(np.sign(coefficients), exponents))


def _bracket_root(coefficients, times, low, high) -> float:
    # Infinite monotonic intervals are converted to finite brackets by expansion.
    if math.isinf(low):
        boundary = high if math.isfinite(high) else 0.0
        step = 1.0
        low = boundary - step
        expected = float(np.sign(coefficients[-1]))
        for _ in range(60):
            if _stable_npv(coefficients, times, low) * expected > 0:
                break
            step *= 2
            low = boundary - step
        else:
            raise ValueError("XIRR: radice fuori dal dominio numerico supportato.")
    if math.isinf(high):
        boundary = low
        step = 1.0
        high = boundary + step
        expected = float(np.sign(coefficients[0]))
        for _ in range(60):
            if _stable_npv(coefficients, times, high) * expected > 0:
                break
            step *= 2
            high = boundary + step
        else:
            raise ValueError("XIRR: radice fuori dal dominio numerico supportato.")
    low_value = _stable_npv(coefficients, times, low)
    high_value = _stable_npv(coefficients, times, high)
    if low_value == 0:
        return low
    if high_value == 0:
        return high
    if low_value * high_value >= 0:
        raise ValueError("XIRR: impossibile isolare una radice stabile.")
    for _ in range(220):
        middle = (low + high) / 2
        value = _stable_npv(coefficients, times, middle)
        if value == 0 or high - low < 2e-13 * max(1, abs(middle)):
            return middle
        if low_value * value > 0:
            low, low_value = middle, value
        else:
            high = middle
    return (low + high) / 2


def _all_log_roots(coefficients: np.ndarray, times: np.ndarray) -> list[float]:
    """Isolate exponential-polynomial roots using derivative monotonic intervals.

    Multiplying NPV by exp(t0*z) leaves a constant first term. Differentiation
    removes it. Recursive derivative roots partition the original function into
    monotonic intervals, avoiding the arbitrary starting guess used by Newton.
    One sign change is a proven single-root shortcut for conventional flows.
    """
    coefficients = coefficients / np.max(np.abs(coefficients))
    if np.any(coefficients == 0):
        raise ValueError("XIRR: flussi fuori dalla precisione numerica supportata.")
    times = times - times[0]
    signs = np.sign(coefficients)
    variations = int(np.count_nonzero(signs[:-1] != signs[1:]))
    if variations == 0:
        return []
    if variations == 1:
        return [_bracket_root(coefficients, times, -math.inf, math.inf)]
    if len(coefficients) > 400:
        raise ValueError("XIRR: troppi cambi di segno per verificare una radice unica; aggregare i flussi per data.")
    derivative = -coefficients[1:] * times[1:]
    stationary = _all_log_roots(derivative, times[1:])
    bounds = [-math.inf, *stationary, math.inf]
    values = [float(signs[-1]), *[_stable_npv(coefficients, times, point) for point in stationary], float(signs[0])]
    roots = [point for point, value in zip(stationary, values[1:-1]) if abs(value) < 1e-12]
    for low, high, left, right in zip(bounds[:-1], bounds[1:], values[:-1], values[1:]):
        if abs(left) < 1e-12 or abs(right) < 1e-12:
            continue
        if left * right < 0:
            roots.append(_bracket_root(coefficients, times, low, high))
    ordered = sorted(roots)
    unique = []
    for root in ordered:
        if not unique or abs(root - unique[-1]) > 1e-10:
            unique.append(root)
    return unique


def xirr(flows: list[dict]) -> float:
    """Annual money-weighted return, actual days/365 and signed investor flows.

    Contributions/investments are negative; withdrawals and terminal value are
    positive. Same-day flows are netted. Multiple roots are reported as ambiguous
    instead of selecting whichever root an initial guess happens to find.
    """
    if not isinstance(flows, (list, tuple)) or len(flows) < 2:
        raise ValueError("XIRR richiede almeno due flussi datati.")
    grouped = defaultdict(list)
    for flow in flows:
        if not isinstance(flow, dict) or "date" not in flow or "amount" not in flow:
            raise ValueError("Ogni flusso XIRR richiede date e amount.")
        grouped[_date(flow["date"])].append(_finite_number(flow["amount"], "Importo del flusso"))
    items = [(day, _finite_sum(amounts, "Flussi della stessa data")) for day, amounts in sorted(grouped.items())]
    items = [(day, amount) for day, amount in items if amount != 0]
    if len(items) < 2 or not any(amount < 0 for _, amount in items) or not any(amount > 0 for _, amount in items):
        raise ValueError("XIRR richiede flussi di entrambi i segni su date distinte.")
    first = items[0][0]
    coefficients = np.array([amount for _, amount in items], dtype=float)
    times = np.array([(day - first).days / 365.0 for day, _ in items], dtype=float)
    roots = _all_log_roots(coefficients, times)
    if len(roots) != 1:
        if roots:
            raise ValueError("XIRR ambiguo: esistono più radici; nessun rendimento unico.")
        raise ValueError("XIRR: non esiste una radice finita nel dominio r > -1.")
    try:
        rate = math.expm1(roots[0])
    except OverflowError as exc:
        raise ValueError("XIRR: rendimento fuori dal dominio numerico supportato.") from exc
    if not math.isfinite(rate) or rate <= -1:
        raise ValueError("XIRR: rendimento fuori dal dominio numerico supportato.")
    return rate


def account_performance(valuations: list[dict]) -> dict:
    """TWR from valuations immediately after *every* external capital flow.

    external_flow is positive for contributions and negative for withdrawals.
    The first observed value is the opening investment, including its recorded
    opening flow; that first flow is never counted twice. Trade settlements and
    distributions retained inside the account are not external capital flows.
    """
    if not isinstance(valuations, (list, tuple)) or len(valuations) < 2:
        raise ValueError("Servono almeno due valorizzazioni e una valorizzazione a ogni flusso esterno.")
    records = []
    for row in valuations:
        if not isinstance(row, dict) or not {"date", "value", "external_flow"}.issubset(row):
            raise ValueError("Ogni valorizzazione richiede date, value e external_flow (0 in assenza di flussi).")
        day = _date(row["date"])
        value = _finite_number(row["value"], "Valore del conto")
        flow = _finite_number(row["external_flow"], "Flusso esterno")
        if value < 0 or value - flow < 0:
            raise ValueError("Il valore post-flusso e il valore precedente al flusso non possono essere negativi.")
        if records and day <= records[-1]["date"]:
            raise ValueError("Le valorizzazioni devono avere date uniche strettamente crescenti.")
        records.append({"date": day, "value": value, "external_flow": flow})
    if records[0]["value"] <= 0:
        raise ValueError("Il valore iniziale del conto deve essere positivo.")
    subperiods = []
    investor_flows = [{"date": records[0]["date"].isoformat(), "amount": -records[0]["value"], "kind": "initial_investment"}]
    factors = []
    for previous, current in zip(records[:-1], records[1:]):
        if previous["value"] <= 0:
            raise ValueError("TWR non calcolabile dopo un patrimonio nullo: separare i periodi di investimento.")
        factor = (current["value"] - current["external_flow"]) / previous["value"]
        factors.append(factor)
        subperiods.append({
            "start": previous["date"].isoformat(), "end": current["date"].isoformat(),
            "return": factor - 1.0, "external_flow": current["external_flow"],
            "value_before_flow": current["value"] - current["external_flow"],
            "value_after_flow": current["value"],
        })
        if current["external_flow"] != 0:
            investor_flows.append({"date": current["date"].isoformat(), "amount": -current["external_flow"], "kind": "external_flow"})
    investor_flows.append({"date": records[-1]["date"].isoformat(), "amount": records[-1]["value"], "kind": "terminal_value"})
    twr = math.prod(factors) - 1.0
    if not math.isfinite(twr):
        raise ValueError("Il TWR supera la precisione numerica disponibile.")
    try:
        money_weighted = xirr(investor_flows)
        xirr_error = None
    except ValueError as exc:
        money_weighted, xirr_error = None, str(exc)
    total_flow = _finite_sum((row["external_flow"] for row in records[1:]), "Flussi esterni")
    return {
        "twr": twr,
        "xirr": money_weighted,
        "xirr_error": xirr_error,
        "start": records[0]["date"].isoformat(),
        "end": records[-1]["date"].isoformat(),
        "initial_value": records[0]["value"],
        "final_value": records[-1]["value"],
        "net_external_flows": total_flow,
        "net_profit": records[-1]["value"] - records[0]["value"] - total_flow,
        "subperiod_returns": subperiods,
        "investor_flows": investor_flows,
        "flow_convention": "valuations_after_flow; contributions_positive_into_account",
        "xirr_day_count": "actual_365",
        "warnings": [
            "Il TWR è esatto soltanto se sono presenti valorizzazioni immediatamente dopo ogni flusso esterno; i flussi omessi non sono rilevabili da questi dati.",
            "Il valore iniziale comprende il flusso della prima riga: quel flusso non viene aggiunto nuovamente al calcolo XIRR.",
            "TWR e XIRR non sono rendimento diviso per giacenza media; le operazioni interne al conto non sono flussi esterni.",
        ],
    }
