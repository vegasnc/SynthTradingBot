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
        self.symbols: str = _get_str("SYMBOLS", "BTC-USD:crypto,ETH-USD:crypto")
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
        self.finnhub_api_key: str = _get_str("FINNHUB_API_KEY")
        self.synth_refresh_minutes: int = _get_int("SYNTH_REFRESH_MINUTES", 10)
        self.synth_price_change_refresh_pct: float = _get_float("SYNTH_PRICE_CHANGE_REFRESH_PCT", 1.0)
        self.synth_price_change_period_minutes: int = _get_int("SYNTH_PRICE_CHANGE_PERIOD_MINUTES", 2)

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

    def synth_file_path(self) -> Path:
        p = Path(self.synth_endpoints_file)
        if p.is_absolute():
            return p
        return (Path(__file__).resolve().parent / self.synth_endpoints_file).resolve()
