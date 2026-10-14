"""Operational portfolio accounting at daily, unadjusted closing prices.

Signals execute strictly after publication. Costs and taxes reduce investable
wealth; the rebalance solves for that wealth before creating funded orders.
Acquisition costs enter average tax basis and sale costs reduce realized gains.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import exchange_calendars as exchange


def _setting(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, dict) else getattr(config, name, default)


def _date(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result.normalize()


def _day(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def simulate_accounting(data: Any, targets: pd.DataFrame, config: Any,
                        tax_mode: str = "all_sales", end_date: Any = None) -> dict:
    """Reconstruct quantities, cash, distributions, taxes and executable trades.

    A positive rebalance band freezes positions whose current weight is within
    the band. Remaining target proportions divide the residual investable
    wealth, preventing borrowing when frozen positions have drifted upward.
    Distribution cash is held until a scheduled rebalance. Final liquidation
    always taxes positive gains on full sales, and is reported separately.
    """
    if tax_mode not in {"all_sales", "exit_only"}:
        raise ValueError("Fiscalità non valida: usare all_sales o exit_only.")
    initial = float(_setting(config, "initial_capital", 100000))
    tax_rate = float(_setting(config, "tax_rate", .26))
    fee_rate = (float(_setting(config, "commission_bps", 0))
                + float(_setting(config, "spread_bps", 0))) / 10000
    band = float(_setting(config, "rebalance_band", 0))
    if not np.isfinite([initial, tax_rate, fee_rate, band]).all() or initial <= 0:
        raise ValueError("Capitale e parametri contabili devono essere finiti; capitale positivo.")
    if not 0 <= tax_rate <= 1 or not 0 <= fee_rate < 1 or not 0 <= band <= 1:
        raise ValueError("Aliquota, costi o banda di ribilanciamento fuori intervallo.")
    if not isinstance(targets, pd.DataFrame) or targets.empty:
        raise ValueError("La contabilità richiede almeno un'allocazione target.")
    weights = targets.copy().astype(float)
    weights.index = pd.DatetimeIndex([_date(d) for d in weights.index])
    if weights.index.has_duplicates or weights.columns.has_duplicates:
        raise ValueError("Date o strumenti target duplicati.")
    weights = weights.sort_index()
    if not np.isfinite(weights.to_numpy()).all() or (weights.to_numpy() < 0).any():
        raise ValueError("I pesi target devono essere finiti e non negativi.")
    if not np.allclose(weights.sum(axis=1), 1, atol=1e-9, rtol=0):
        raise ValueError("I pesi target devono sommare a 1.")
    if any(not d.is_month_end for d in weights.index):
        raise ValueError("I segnali operativi devono essere datati a fine mese.")
    close = data.close.copy()
    close.index = pd.DatetimeIndex([_date(d) for d in close.index])
    if close.empty or close.index.has_duplicates:
        raise ValueError("Quotazioni operative assenti o date duplicate.")
    close = close.sort_index()
    unknown = set(weights.columns) - set(close.columns)
    if unknown:
        raise ValueError("Quotazioni mancanti per: " + ", ".join(sorted(unknown)))
    requested_end = end_date if end_date is not None else _setting(config, "end_date", None)
    observation_end = _date(requested_end) if requested_end else _date(pd.Timestamp.now(tz="Europe/Rome"))
    coverage_end = close.index.max()
    calendar_end = coverage_end + pd.offsets.MonthEnd(0)
    calendar = exchange.get_calendar("XMIL", start=coverage_end.replace(day=1), end=calendar_end)
    final_session = calendar.sessions[(calendar.sessions >= coverage_end.replace(day=1))
                                      & (calendar.sessions <= calendar_end)][-1]
    final_session = _date(final_session)
    # A generic weekday calendar misses Milan's Good Friday and 31 December
    # closures. Certification requires the actual final exchange session and
    # a requested observation horizon that has reached calendar month-end.
    if coverage_end >= final_session and observation_end >= calendar_end:
        coverage_end = calendar_end
    cutoff = min(observation_end, coverage_end)
    close = close.loc[close.index <= cutoff]
    first_signal = weights.index.min()
    close = close.loc[close.index > first_signal]
    if close.empty:
        raise ValueError("Nessuna seduta disponibile dopo il primo segnale.")
    dividends = getattr(data, "dividends", pd.DataFrame())
    dividends = dividends.reindex(index=close.index, columns=close.columns, fill_value=0).fillna(0)
    splits = getattr(data, "splits", None)
    if splits is not None:
        splits = splits.reindex(index=close.index, columns=close.columns, fill_value=0).fillna(0)

    cash = initial
    positions: dict[str, dict[str, float]] = {}
    last_prices: dict[str, float] = {}
    trades: list[dict] = []
    distributions: list[dict] = []
    split_events: list[dict] = []
    snapshots: list[dict] = []
    warnings: list[str] = [
        "Simulazione operativa: esecuzione alla chiusura della prima seduta successiva al segnale; "
        "la curva differisce dal backtest mensile idealizzato.",
        "Fiscalità convenzionale: nessuna compensazione minusvalenze; distribuzioni tassate "
        "e liquidità netta reinvestita al successivo ribilanciamento. Costi di acquisto inclusi "
        "nel costo medio fiscale; plusvalenze al netto dei costi di vendita.",
    ]
    if tax_mode == "exit_only":
        warnings.append("Modello fiscale semplificato: ribilanciamenti parziali eseguiti, "
                        "ma plusvalenze tassate solo sulle uscite complete.")
    if band:
        warnings.append("Banda attiva: posizioni entro soglia congelate; target restanti "
                        "proporzionati al patrimonio residuo disponibile.")

    total_costs = total_taxes = total_gains = turnover = distribution_taxes = 0.0
    equity: list[dict] = [{"date": _day(first_signal), "value": initial, "cash": initial}]
    pending = list(weights.iterrows())
    next_signal = 0
    previous_day = first_signal

    def portfolio_value(prices: dict[str, float]) -> float:
        return cash + sum(p["quantity"] * prices[t] for t, p in positions.items())

    def make_trade(ticker: str, desired_value: float, price: float,
                   signal: pd.Timestamp, day: pd.Timestamp, target_weight: float,
                   mode: str, liquidation: bool = False) -> dict | None:
        nonlocal cash
        before = positions.get(ticker, {"quantity": 0., "average_cost": 0.})
        quantity_before, basis = before["quantity"], before["average_cost"]
        difference = desired_value - quantity_before * price
        tolerance = max(initial * 1e-12, 1e-9)
        if abs(difference) <= tolerance:
            return None
        quantity = abs(difference) / price
        side = "BUY" if difference > 0 else "SELL"
        full_exit = side == "SELL" and desired_value <= tolerance
        if full_exit:
            quantity = quantity_before
        gross = quantity * price
        cost = gross * fee_rate
        gain = tax = 0.
        if side == "SELL":
            gain = gross - cost - quantity * basis
            tax = max(gain, 0.) * tax_rate if mode == "all_sales" or full_exit else 0.
            if gross - cost - tax < -tolerance:
                raise ValueError("Costi e imposte superano i proventi disponibili della vendita.")
            cash += gross - cost - tax
            remainder = quantity_before - quantity
            if full_exit or remainder <= 1e-12:
                positions.pop(ticker, None)
            else:
                positions[ticker] = {"quantity": remainder, "average_cost": basis}
        else:
            cash -= gross + cost
            new_quantity = quantity_before + quantity
            positions[ticker] = {"quantity": new_quantity,
                                 "average_cost": (quantity_before * basis + gross + cost) / new_quantity}
        if -tolerance <= cash < 0:
            cash = 0.
        return {"signal_date": _day(signal), "date": _day(day), "ticker": ticker,
                "side": side, "quantity": float(quantity), "price": float(price),
                "gross": float(gross), "cost": float(cost), "realized_gain": float(gain),
                "tax": float(tax), "average_cost_before": float(basis),
                "position_before": float(quantity_before), "target_weight": float(target_weight),
                "cash_after": float(cash), "liquidation": liquidation}

    for day, quotes in close.iterrows():
        valid_prices = {str(t): float(p) for t, p in quotes.items()
                        if pd.notna(p) and np.isfinite(p) and p > 0}
        last_prices.update(valid_prices)
        for ticker in list(positions):
            if splits is not None:
                ratio = float(splits.at[day, ticker])
                if not np.isfinite(ratio) or ratio < 0:
                    raise ValueError("Rapporto split non valido.")
                if ratio and ratio != 1:
                    old = positions[ticker].copy()
                    positions[ticker]["quantity"] *= ratio
                    positions[ticker]["average_cost"] /= ratio
                    if ticker not in valid_prices and ticker in last_prices:
                        last_prices[ticker] /= ratio
                    split_events.append({"date": _day(day), "ticker": ticker, "ratio": ratio,
                                         "quantity_before": old["quantity"],
                                         "quantity_after": positions[ticker]["quantity"],
                                         "average_cost_before": old["average_cost"],
                                         "average_cost_after": positions[ticker]["average_cost"]})
            amount = float(dividends.at[day, ticker])
            if not np.isfinite(amount) or amount < 0:
                raise ValueError("Distribuzione per quota non valida.")
            if amount:
                gross = amount * positions[ticker]["quantity"]
                tax = gross * tax_rate
                cash += gross - tax
                total_taxes += tax
                distribution_taxes += tax
                distributions.append({"date": _day(day), "ticker": ticker,
                                      "quantity": positions[ticker]["quantity"], "per_share": amount,
                                      "gross": gross, "tax": tax, "net": gross - tax})

        # A session is the first calendar row with any required instrument
        # tradable. Missing quotes for other required instruments are explicit
        # failures rather than executions silently postponed into the future.
        if next_signal < len(pending) and pending[next_signal][0] < day:
            signal, row = pending[next_signal]
            required = set(positions) | set(row[row > 0].index)
            if required & set(valid_prices):
                missing = required - set(valid_prices)
                if missing:
                    raise ValueError(f"Quotazioni negoziabili mancanti nella seduta {_day(day)}: "
                                     + ", ".join(sorted(missing)))
                target = {str(t): float(w) for t, w in row.items()}
                market = {t: positions.get(t, {}).get("quantity", 0.) * valid_prices[t]
                          for t in required}
                before_equity = cash + sum(market.values())
                snapshots.append({"signal_date": _day(signal), "date": _day(day), "cash": cash,
                                  "positions": {t: {"quantity": p["quantity"], "price": valid_prices[t],
                                                     "market_value": market[t],
                                                     "average_cost": p["average_cost"],
                                                     "weight": market[t] / before_equity}
                                                for t, p in positions.items()},
                                  "target_weights": target})
                frozen = {t for t in required if band > 0 and
                          abs(market[t] / before_equity - target.get(t, 0.)) <= band}
                active = sorted(required - frozen)
                locked_value = sum(market[t] for t in frozen)
                active_target_sum = sum(target.get(t, 0.) for t in active)

                def values_at(wealth: float) -> dict[str, float]:
                    residual = max(wealth - locked_value, 0.)
                    return {t: residual * target.get(t, 0.) / active_target_sum
                            if active_target_sum else 0. for t in active}

                def rebalance_loss(wealth: float) -> tuple[float, float]:
                    fees = taxes = 0.
                    for ticker, desired in values_at(wealth).items():
                        delta = desired - market[ticker]
                        cost = abs(delta) * fee_rate
                        fees += cost
                        if delta < 0:
                            qty = -delta / valid_prices[ticker]
                            gain = -delta - cost - qty * positions[ticker]["average_cost"]
                            if tax_mode == "all_sales" or desired <= max(initial * 1e-12, 1e-9):
                                taxes += max(gain, 0.) * tax_rate
                    return fees, taxes

                if active:
                    low, high = locked_value, before_equity
                    low_loss = sum(rebalance_loss(low))
                    if low - before_equity + low_loss > initial * 1e-10:
                        raise ValueError("Il patrimonio disponibile non finanzia costi e imposte.")
                    for _ in range(100):
                        mid = (low + high) / 2
                        fees, taxes = rebalance_loss(mid)
                        if mid - before_equity + fees + taxes > 0:
                            high = mid
                        else:
                            low = mid
                    desired = values_at((low + high) / 2)
                    # Sales fund purchases, including all their taxes and costs.
                    order = sorted(active, key=lambda t: desired[t] >= market[t])
                    for ticker in order:
                        trade = make_trade(ticker, desired[ticker], valid_prices[ticker],
                                           signal, day, target.get(ticker, 0.), tax_mode)
                        if trade:
                            trades.append(trade)
                            total_costs += trade["cost"]
                            total_taxes += trade["tax"]
                            total_gains += trade["realized_gain"]
                            turnover += trade["gross"] / (2 * before_equity)
                    if cash < -max(initial * 1e-10, 1e-7):
                        raise ValueError("Ordini non finanziati: la cassa diventerebbe negativa.")
                    if abs(cash) <= max(initial * 1e-10, 1e-7):
                        cash = 0.
                next_signal += 1
                if next_signal < len(pending) and pending[next_signal][0] < day:
                    raise ValueError("Più segnali senza una seduta eseguibile intermedia.")

        missing_marks = set(positions) - set(last_prices)
        if missing_marks:
            raise ValueError("Impossibile valorizzare le posizioni senza quotazioni.")
        stale = sorted(set(positions) - set(valid_prices))
        equity.append({"date": _day(day), "value": float(portfolio_value(last_prices)),
                       "cash": float(cash), "stale_tickers": stale})
        previous_day = day

    if not snapshots:
        raise ValueError("Nessuna allocazione eseguibile nelle sedute disponibili.")
    if any(r.get("stale_tickers") for r in equity):
        warnings.append("Valorizzazioni giornaliere con prezzo precedente nei giorni senza "
                        "quotazione; gli strumenti interessati sono indicati in stale_tickers.")
    not_executed = [signal for signal, _ in pending[next_signal:] if signal < cutoff]
    if not_executed:
        warnings.append("Segnali senza una successiva seduta disponibile: "
                        + ", ".join(_day(s) for s in not_executed))
    before_liquidation = float(portfolio_value(last_prices))
    final_positions = {t: {**p, "price": last_prices[t], "market_value": p["quantity"] * last_prices[t],
                           "weight": p["quantity"] * last_prices[t] / before_liquidation}
                       for t, p in positions.items()}
    final_trades: list[dict] = []
    for ticker in sorted(positions):
        if ticker not in valid_prices:
            raise ValueError("Liquidazione finale impossibile senza prezzo effettivo nella seduta finale: "
                             + ticker)
        trade = make_trade(ticker, 0., valid_prices[ticker], previous_day, previous_day,
                           0., "all_sales", liquidation=True)
        if trade:
            final_trades.append(trade)
    after_liquidation = float(cash)
    final_totals = {"costs": sum(t["cost"] for t in final_trades),
                    "taxes": sum(t["tax"] for t in final_trades),
                    "realized_gains": sum(t["realized_gain"] for t in final_trades),
                    "turnover": sum(t["gross"] for t in final_trades) / (2 * before_liquidation)
                    if before_liquidation else 0.}
    totals = {"costs": total_costs, "taxes": total_taxes, "realized_gains": total_gains,
              "turnover": turnover, "distribution_taxes": distribution_taxes,
              "capital_gains_taxes": total_taxes - distribution_taxes}
    values = pd.Series([r["value"] for r in equity], index=pd.to_datetime([r["date"] for r in equity]))
    monthly_values = values.resample("ME").last()
    monthly_values = monthly_values.loc[(monthly_values.index > first_signal) & (monthly_values.index <= cutoff)]
    monthly_returns: list[dict] = []
    previous = initial
    for day, value in monthly_values.items():
        monthly_returns.append({"date": _day(day), "return": float(value / previous - 1), "equity": float(value)})
        previous = value
    return {"equity": equity, "monthly_returns": monthly_returns, "trades": trades,
            "distributions": distributions, "split_events": split_events, "positions_before": snapshots,
            "final_positions": final_positions, "totals": totals,
            "before_liquidation": before_liquidation, "after_liquidation": after_liquidation,
            "final_liquidation": final_trades, "final_liquidation_totals": final_totals,
            "totals_after_liquidation": {k: totals[k] + final_totals[k] for k in final_totals},
            "warnings": warnings, "tax_mode": tax_mode,
            "period": {"start": _day(first_signal), "first_execution": snapshots[0]["date"],
                       "end": _day(previous_day), "complete_months": len(monthly_returns)},
            "conventions": {"execution": "first_session_after_signal_close",
                            "prices": "unadjusted_close", "distributions": "before_close_trades",
                            "turnover": "sum_half_gross_trade_value_divided_by_pretrade_equity",
                            "cost_basis": "average_acquisition_cost_including_buy_costs",
                            "band": "freeze_in_band_and_rescale_remaining_targets",
                            "totals": "exclude_final_liquidation"}}
