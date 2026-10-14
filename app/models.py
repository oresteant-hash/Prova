"""Validated strategy settings; percentages in the API are fractions."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    formula: Literal['uniform', 'reactive'] = 'uniform'
    top_n: int = Field(default=6, ge=2, le=6)
    ma_filter: bool = False
    weighting: Literal['score', 'equal', 'inverse_vol'] = 'score'
    fallback: Literal['C3M.MI', 'IBGX.MI'] = 'C3M.MI'
    universe_mode: Literal['complete', 'progressive'] = 'complete'
    min_assets: int = Field(default=10, ge=2, le=12)
    initial_capital: float = Field(default=100000, gt=0, le=1e12)
    tax_rate: float = Field(default=.26, ge=0, le=1)
    commission_bps: float = Field(default=0, ge=0, le=500)
    spread_bps: float = Field(default=0, ge=0, le=500)
    rebalance_band: float = Field(default=0, ge=0, le=.1)
    start_date: str | None = None
    end_date: str | None = None

    @model_validator(mode='after')
    def validate_dates(self):
        dates = []
        for name in ('start_date', 'end_date'):
            text = getattr(self, name)
            if text is not None:
                try:
                    parsed = date.fromisoformat(text)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f'{name}: usare YYYY-MM-DD') from exc
                dates.append((name, parsed))
        if len(dates) == 2 and dates[0][1] > dates[1][1]:
            raise ValueError('La data iniziale deve precedere la data finale.')
        return self


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: StrategyConfig = Field(default_factory=StrategyConfig)
    source: Literal['demo', 'yahoo'] = 'demo'


class AccountValuation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    date: str
    value: float = Field(ge=0)
    external_flow: float = 0


class AccountRequest(BaseModel):
    valuations: list[AccountValuation] = Field(min_length=2, max_length=10000)


class PricePoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    date: str
    price: float = Field(gt=0)


class ProxyRequest(BaseModel):
    real: list[PricePoint] = Field(min_length=2, max_length=10000)
    proxy: list[PricePoint] = Field(min_length=2, max_length=10000)
    metadata: dict
