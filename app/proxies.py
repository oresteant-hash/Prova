"""Explicit historical proxy analysis, independent of the live strategy data."""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
import pandas as pd


_REQUIRED_METADATA = ("source", "exposure", "currency", "hedging", "duration", "distributions")


def _monthly_series(series: pd.Series, label: str) -> pd.Series:
    if not isinstance(series, pd.Series) or series.empty:
        raise ValueError(f"{label}: serve una serie mensile non vuota.")
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(series.index)).tz_localize(None).normalize()
        values = pd.to_numeric(series, errors="raise").to_numpy(dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: date o prezzi non validi.") from exc
    if dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError(f"{label}: date uniche e crescenti obbligatorie.")
    if not bool(np.all(dates.is_month_end)):
        raise ValueError(f"{label}: usare date di fine mese per chiusure mensili complete.")
    observed = ~np.isnan(values)
    if not np.any(observed) or np.any(~np.isfinite(values[observed])) or np.any(values[observed] <= 0):
        raise ValueError(f"{label}: i prezzi osservati devono essere finiti e positivi.")
    return pd.Series(values, index=dates, name=series.name)


def extend_with_proxies(real: pd.Series, proxy: pd.Series, metadata: dict) -> tuple[pd.Series, dict]:
    """Link proxy total-return history to the first observed real monthly price.

    The anchor must already exist in both series at the real instrument's first
    observation. Earlier proxy prices are normalized by the anchor's ratio,
    preserving proxy returns and all actual real observations. Missing months
    remain missing, and the extension never replaces post-inception real gaps.
    metadata describes source, exposure, EUR currency, hedging, duration (None
    explicitly means not applicable), distribution treatment, and any assumptions
    actually supplied by the analyst. No economic equivalence is inferred.
    """
    if not isinstance(metadata, dict):
        raise ValueError("Sono obbligatori i metadati espliciti del raccordo proxy.")
    missing = [key for key in _REQUIRED_METADATA if key not in metadata]
    if missing:
        raise ValueError("Metadati proxy mancanti: " + ", ".join(missing))
    for key in _REQUIRED_METADATA:
        if key != "duration" and (metadata[key] is None or metadata[key] == "" or metadata[key] == {}):
            raise ValueError(f"Metadato proxy {key} non documentato.")
    if str(metadata["currency"]).upper() != "EUR":
        raise ValueError("La serie proxy deve essere documentata in EUR; nessuna conversione automatica.")
    if "assumptions" in metadata and not isinstance(metadata["assumptions"], (list, tuple)):
        raise ValueError("assumptions deve essere una lista delle ipotesi esplicitamente fornite.")
    actual = _monthly_series(real, "Strumento reale")
    historical = _monthly_series(proxy, "Proxy")
    anchor = actual.first_valid_index()
    if anchor not in historical.index or pd.isna(historical.loc[anchor]):
        raise ValueError("Serve una chiusura proxy sovrapposta al primo mese reale: un anchor futuro introdurrebbe informazioni successive nel raccordo.")
    if "anchor_date" in metadata and pd.Timestamp(metadata["anchor_date"]).normalize() != anchor:
        raise ValueError("L'anchor deve coincidere con il primo mese reale valido.")
    scale = float(actual.loc[anchor] / historical.loc[anchor])
    prefix = historical.loc[historical.index < anchor] * scale
    if prefix.dropna().empty:
        raise ValueError("Il proxy non contiene osservazioni storiche precedenti allo strumento reale.")
    # Leading NaNs in the real series denote unavailable history. Historical
    # proxy observations may fill those months explicitly; later real NaNs stay.
    suffix = actual.loc[actual.index >= anchor]
    extended = pd.concat([prefix, suffix]).sort_index()
    extended.name = real.name
    if not np.allclose(extended.loc[suffix.index].values, suffix.values, equal_nan=True):
        raise ValueError("Il raccordo altererebbe le osservazioni dello strumento reale.")
    observed = extended.dropna()
    if not np.all(np.isfinite(observed.values)) or np.any(observed.values <= 0):
        raise ValueError("Il raccordo proxy supera la precisione numerica disponibile.")
    overlap = pd.concat([actual.rename("real"), historical.rename("proxy")], axis=1, sort=True).dropna()
    # Use only consecutive shared months: a multi-month gap is never treated as
    # a single monthly return, and no forward fill is performed.
    monthly_returns = overlap.pct_change(fill_method=None)
    serial_months = overlap.index.year * 12 + overlap.index.month
    consecutive = pd.Series(np.r_[False, np.diff(serial_months) == 1], index=overlap.index)
    monthly_returns = monthly_returns.loc[consecutive].dropna()
    difference = monthly_returns["real"] - monthly_returns["proxy"]
    correlation = monthly_returns["real"].corr(monthly_returns["proxy"]) if len(monthly_returns) > 1 else None
    if correlation is not None and not math.isfinite(correlation):
        correlation = None
    expected_months = pd.date_range(extended.index.min(), extended.index.max(), freq="ME")
    missing_months = expected_months.difference(extended.dropna().index)
    report = deepcopy(metadata)
    report.update({
        "analysis_kind": "separate_historical_proxy_extension",
        "anchor_date": anchor.date().isoformat(),
        "real_start": anchor.date().isoformat(),
        "proxy_start": prefix.first_valid_index().date().isoformat(),
        "scale_factor": scale,
        "link_method": "normalized_proxy_returns_to_first_real_price",
        "extended_observations": int(prefix.notna().sum()),
        "overlap_observations": len(overlap),
        "overlap_monthly_return_observations": len(monthly_returns),
        "overlap_return_rmse": float(np.sqrt(np.mean(difference ** 2))) if len(difference) else None,
        "overlap_return_correlation": float(correlation) if correlation is not None else None,
        "missing_months": [day.date().isoformat() for day in missing_months],
        "assumptions": list(metadata.get("assumptions", [])),
        "warnings": [
            "Il proxy è un'analisi storica separata; l'equivalenza economica con lo strumento reale non viene presunta.",
            "Il trattamento delle distribuzioni e la comparabilità total return devono essere verificati rispetto ai metadati dichiarati.",
            "Nessun mese mancante è interpolato o riempito; le lacune successive all'avvio dello strumento reale restano tali.",
        ],
    })
    return extended, report
