from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

MarketType = Literal["crypto", "equity"]


@dataclass(slots=True)
class SymbolConfig:
    symbol: str
    market_type: MarketType


def _get_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        self.synth_api_key: str = _get_str("SYNTH_API_KEY")
        self.mongo_uri: str = _get_str("MONGO_URI")
        self.broker_api_key_crypto: str = _get_str("BROKER_API_KEY_CRYPTO")
        self.broker_api_secret_crypto: str = _get_str("BROKER_API_SECRET_CRYPTO")
        self.broker_api_key_equity: str = _get_str("BROKER_API_KEY_EQUITY")
        self.broker_api_secret_equity: str = _get_str("BROKER_API_SECRET_EQUITY")
        self.paper_trading: bool = _get_bool("PAPER_TRADING", True)
        # Use Kraken xStock symbols (GOOGLX, SPYX, etc.) so WS + Synth predictions align; Synth API uses GOOGL, SPY via SYNTH_ASSET_MAP
        self.symbols: str = _get_str(
            "SYMBOLS", "BTC:crypto,ETH:crypto,XAU:crypto,GOOGLX:equity,SPYX:equity,TSLAX:equity,NVDAX:equity,AAPLX:equity"
        )
        self.engine_db_name: str = _get_str("ENGINE_DB_NAME", "trading_bot")
        self.synth_endpoints_file: str = _get_str("SYNTH_ENDPOINTS_FILE", "../synth_api_endpoints.json")
        self.engine_loop_seconds: int = _get_int("ENGINE_LOOP_SECONDS", 60)
        self.enable_live_trading: bool = _get_bool("ENABLE_LIVE_TRADING", False)
        self.account_equity: float = _get_float("ACCOUNT_EQUITY", 100_000.0)
        self.crypto_risk_pct: float = _get_float("CRYPTO_RISK_PCT", 0.0075)
        self.equity_risk_pct: float = _get_float("EQUITY_RISK_PCT", 0.005)
        self.max_symbol_exposure: float = _get_float("MAX_SYMBOL_EXPOSURE", 0.2)
        self.max_portfolio_exposure: float = _get_float("MAX_PORTFOLIO_EXPOSURE", 0.7)
        self.paper_slippage_bps: float = _get_float("PAPER_SLIPPAGE_BPS", 5.0)
        self.synth_refresh_minutes: int = _get_int("SYNTH_REFRESH_MINUTES", 10)
        self.synth_price_change_refresh_pct: float = _get_float("SYNTH_PRICE_CHANGE_REFRESH_PCT", 0.3)
        self.synth_price_change_period_minutes: int = _get_int("SYNTH_PRICE_CHANGE_PERIOD_MINUTES", 2)
        self.market_strength_counter_trend_multiplier: float = _get_float(
            "MARKET_STRENGTH_COUNTER_TREND_MULTIPLIER", 1.5
        )
        self.market_strength_lookback_minutes: int = _get_int("MARKET_STRENGTH_LOOKBACK_MINUTES", 120)
        # Profit optimization filters
        self.min_expected_profit: float = _get_float("MIN_EXPECTED_PROFIT", 0.004)
        self.min_volatility_width: float = _get_float("MIN_VOLATILITY_WIDTH", 0.003)
        self.trading_fee_rate: float = _get_float("TRADING_FEE_RATE", 0.001)
        # News analyzer
        self.news_timezone: str = _get_str("NEWS_TIMEZONE", "America/New_York")
        self.openai_api_key: str = _get_str("OPENAI_API_KEY")
        # Strike tool (xStocks = Kraken symbols GOOGLX, SPYX, etc.; Synth API uses GOOGL, SPY, etc.)
        self.strike_symbols: str = _get_str(
            "STRIKE_SYMBOLS", "BTC,ETH,XAU,GOOGLX,SPYX,TSLAX,NVDAX,AAPLX"
        )
        self.strike_refresh_minutes: int = _get_int("STRIKE_REFRESH_MINUTES", 60)
        # Maps trading symbol (e.g. GOOGLX) -> Synth API asset (e.g. GOOGL). Required for xStocks predictions + strike.
        self.synth_asset_map: str = _get_str(
            "SYNTH_ASSET_MAP", "GOOGLX:GOOGL,SPYX:SPY,TSLAX:TSLA,NVDAX:NVDA,AAPLX:AAPL"
        )

    def parse_symbols(self) -> list[SymbolConfig]:
        parsed: list[SymbolConfig] = []
        for part in self.symbols.split(","):
            clean = part.strip()
            if not clean:
                continue
            if ":" in clean:
                symbol, market = clean.split(":", 1)
                market_type: MarketType = "crypto" if market.strip().lower() == "crypto" else "equity"
            else:
                symbol = clean
                market_type = "crypto" if "-" in clean else "equity"
            parsed.append(SymbolConfig(symbol=symbol.strip().upper(), market_type=market_type))
        return parsed

    def parse_synth_asset_map(self) -> dict[str, str]:
        """Parse SYNTH_ASSET_MAP (trading_symbol -> synth_asset). Example: SPY:SPYX,AAPL:AAPLX"""
        out: dict[str, str] = {}
        for part in self.synth_asset_map.split(","):
            part = part.strip()
            if ":" in part:
                trading, synth = part.split(":", 1)
                out[trading.strip().upper()] = synth.strip().upper()
        return out

    def synth_file_path(self) -> Path:
        p = Path(self.synth_endpoints_file)
        if p.is_absolute():
            return p
        base = Path(__file__).resolve().parent
        candidates = [
            (base / self.synth_endpoints_file).resolve(),
            (base / "../../synth_api_endpoints.json").resolve(),
            (base / "../../reference_doc/synth_api_endpoints.json").resolve(),
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]  # return first even if missing; SynthClient handles missing
