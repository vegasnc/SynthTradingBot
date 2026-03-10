from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

import httpx

from .broker import PaperBroker
from .config import Settings, SymbolConfig
from .db import MongoStore
from .logging_utils import setup_logging
from .market_data import fetch_crypto_prices, fetch_spot_prices
from .scheduler import EngineScheduler
from .state import EngineState
from .synth_client import SynthClient
from .utils import as_utc, utc_now
from .ws_manager import WSManager
from .kraken_ws import run_kraken_ws


# Legacy Synth asset -> Kraken xStock (so popup/strike always get allocations under xStock keys)
_STRIKE_LEGACY_TO_XSTOCK: dict[str, str] = {
    "GOOGL": "GOOGLX", "SPY": "SPYX", "NVDA": "NVDAX", "TSLA": "TSLAX", "AAPL": "AAPLX",
}


def _normalize_strike_allocations(allocations: dict[str, Any]) -> dict[str, Any]:
    """Ensure strike allocations include xStock keys so the spot/strike panel finds funds for GOOGLX, SPYX, etc."""
    out = dict(allocations)
    for legacy, xstock in _STRIKE_LEGACY_TO_XSTOCK.items():
        if legacy in out and xstock not in out:
            out[xstock] = out[legacy]
    return out


def _spot_for_symbol(state: EngineState, symbol: str) -> float | None:
    """Resolve current spot for a symbol from state (latest_price, latest_market_data, alternates)."""
    if not symbol:
        return None
    spot = state.latest_price.get(symbol)
    if spot is not None and float(spot) > 0:
        return float(spot)
    mkt = state.latest_market_data.get(symbol)
    if mkt and mkt.get("spot") is not None and float(mkt["spot"]) > 0:
        return float(mkt["spot"])
    alt = f"{symbol}-USD" if "-" not in symbol else symbol.replace("-USD", "")
    spot = state.latest_price.get(alt)
    if spot is not None and float(spot) > 0:
        return float(spot)
    mkt = state.latest_market_data.get(alt)
    if mkt and mkt.get("spot") is not None and float(mkt["spot"]) > 0:
        return float(mkt["spot"])
    return None


def _mongo_to_json(obj: Any) -> Any:
    """Convert MongoDB doc to JSON-serializable (ObjectId, datetime)."""
    from bson import ObjectId
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_mongo_to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _mongo_to_json(v) for k, v in obj.items()}
    if hasattr(obj, "isoformat"):
        s = obj.isoformat()
        if getattr(obj, "tzinfo", None) is None and "Z" not in s and "+" not in s:
            s = s + "Z"
        return s
    return obj


def _parse_ts(s: str | Any) -> datetime | None:
    try:
        if isinstance(s, str):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    return None


def build_app() -> Starlette:
    setup_logging()
    settings = Settings()
    symbols = settings.parse_symbols()
    state = EngineState(
        symbols=symbols,
        paper_trading=settings.paper_trading,
        account_equity=settings.account_equity,
        crypto_risk_pct=settings.crypto_risk_pct,
        equity_risk_pct=settings.equity_risk_pct,
        max_symbol_exposure=settings.max_symbol_exposure,
        max_portfolio_exposure=settings.max_portfolio_exposure,
    )
    store = MongoStore(settings)

    async def on_synth_call(api: str, params: dict) -> None:
        state.push_synth_call(api, params)
        await store.insert_synth_call(api, params)

    synth = SynthClient(settings, on_api_call=on_synth_call)
    broker = PaperBroker(starting_equity=settings.account_equity, slippage_bps=settings.paper_slippage_bps)
    scheduler = EngineScheduler(
        store,
        synth,
        broker,
        state,
        settings,
        loop_seconds=settings.engine_loop_seconds,
        synth_refresh_minutes=settings.synth_refresh_minutes,
        synth_price_change_refresh_pct=settings.synth_price_change_refresh_pct,
        synth_price_change_period_minutes=settings.synth_price_change_period_minutes,
        market_strength_counter_trend_multiplier=settings.market_strength_counter_trend_multiplier,
        market_strength_lookback_minutes=settings.market_strength_lookback_minutes,
    )
    ws_manager = WSManager(state)

    async def _market_data_loop() -> None:
        """Fetch full crypto and equity market data (candles) every 5s."""
        log = logging.getLogger("app.api")
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    prices = await fetch_crypto_prices(client, state.symbols)
                    merged = prices
                    if merged:
                        for sym, data in merged.items():
                            state.latest_market_data[sym] = data
                        state.last_market_data_success_at = utc_now()
                        state.last_market_data_error = None
                    else:
                        state.last_market_data_error = "All symbols failed (Binance/Kraken)"
                except Exception as e:
                    state.last_market_data_error = str(e)
                    log.exception("market data fetch failed: %s", e)
                await asyncio.sleep(5)

    async def _spot_price_loop() -> None:
        """Fetch spot prices every 1s for real-time TP/stop detection (crypto + equity)."""
        log = logging.getLogger("app.api")
        from .config import SymbolConfig
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    config_symbols = list(state.symbols)
                    try:
                        pos_cursor = store.db.positions.find({"status": "open"}, {"symbol": 1, "market_type": 1})
                        for p in await pos_cursor.to_list(200):
                            s = p["symbol"]
                            if s not in (c.symbol for c in config_symbols):
                                mkt = p.get("market_type") or "crypto"
                                config_symbols.append(SymbolConfig(symbol=s, market_type=mkt))
                    except Exception:
                        pass
                    spots = await fetch_spot_prices(client, config_symbols)
                    for sym, spot in spots.items():
                        existing = state.latest_market_data.get(sym)
                        mkt = next((c.market_type for c in config_symbols if c.symbol == sym), "crypto")
                        if existing:
                            state.latest_market_data[sym] = {**existing, "spot": spot, "received_at": utc_now()}
                        else:
                            state.latest_market_data[sym] = {"spot": spot, "received_at": utc_now(), "market_type": mkt, "candles_1m": [], "candles_5m": []}
                except Exception as e:
                    log.debug("spot price fetch failed: %s", e)
                await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        await store.setup_indexes()
        scheduler_task = asyncio.create_task(scheduler.run_forever())
        ws_task = asyncio.create_task(_ws_pump(ws_manager))
        market_task = asyncio.create_task(_market_data_loop())
        spot_task = asyncio.create_task(_spot_price_loop())
        kraken_ws_task = run_kraken_ws(state)
        yield
        scheduler_task.cancel()
        ws_task.cancel()
        market_task.cancel()
        spot_task.cancel()
        kraken_ws_task.cancel()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({
            "ok": True, "trading_enabled": state.trading_enabled, "paper_trading": state.paper_trading
        })

    async def get_symbols(_: Request) -> JSONResponse:
        return JSONResponse([{"symbol": s.symbol, "market_type": s.market_type} for s in state.symbols])

    async def get_state(_: Request) -> JSONResponse:
        open_positions = await store.db.positions.count_documents({"status": "open"})
        return JSONResponse({
            "account_equity": state.account_equity,
            "trading_enabled": state.trading_enabled,
            "paper_trading": state.paper_trading,
            "exposure_by_symbol": dict(state.exposure_by_symbol),
            "open_positions": open_positions,
        })

    async def get_status(_: Request) -> JSONResponse:
        """Diagnostic: show market data status per symbol. Used to debug 'no signals'."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=5)
        status = []
        for s in state.symbols:
            mkt = state.latest_market_data.get(s.symbol)
            received = as_utc(mkt.get("received_at")) if mkt else None
            fresh = bool(received and received >= cutoff)
            candles_1m = (mkt.get("candles_1m") or []) if mkt else []
            candles_5m = (mkt.get("candles_5m") or []) if mkt else []
            status.append({
                "symbol": s.symbol,
                "market_type": s.market_type,
                "has_market_data": mkt is not None,
                "received_at": str(received) if received else None,
                "data_fresh": fresh,
                "candles_1m_count": len(candles_1m),
                "candles_5m_count": len(candles_5m),
                "can_produce_signals": bool(fresh and candles_1m and candles_5m),
            })
        return JSONResponse({
            "symbols": status,
            "last_market_data_error": state.last_market_data_error,
            "last_market_data_success_at": (
                state.last_market_data_success_at.isoformat()
                if state.last_market_data_success_at else None
            ),
            "hint": "Crypto: Binance/Kraken. Equity: Yahoo Finance (auto-fetched).",
        })

    async def get_predictions(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol", "")
        if not symbol:
            return JSONResponse({"detail": "symbol required"}, status_code=400)
        cursor = store.db.synth_predictions.find({"symbol": symbol}).sort("timestamp", -1).limit(100)
        rows = _mongo_to_json(await cursor.to_list(length=100))
        return JSONResponse(rows)

    async def get_signals(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol")
        limit = int(request.query_params.get("limit", 100))
        q = {"symbol": symbol} if symbol else {}
        cursor = store.db.signals.find(q).sort("timestamp", -1).limit(limit)
        rows = _mongo_to_json(await cursor.to_list(length=limit))
        return JSONResponse(rows)

    async def get_synth_calls(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", 200))
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = store.db.synth_api_calls.find({"ts": {"$gte": today_start}}).sort("ts", -1).limit(limit)
        rows = _mongo_to_json(await cursor.to_list(length=limit))
        return JSONResponse(rows)

    async def get_positions(request: Request) -> JSONResponse:
        period = request.query_params.get("period", "all")
        open_cursor = store.db.positions.find({"status": "open"}).sort("opened_at", -1).limit(200)
        open_list = await open_cursor.to_list(length=200)
        now = datetime.now(timezone.utc)
        cutoff = None
        if period == "day":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            cutoff = now - timedelta(days=7)
        elif period == "month":
            cutoff = now - timedelta(days=30)
        elif period == "year":
            cutoff = now - timedelta(days=365)
        hist_q: dict = {"status": "closed"}
        if cutoff:
            hist_q["closed_at"] = {"$gte": cutoff}
        hist_cursor = store.db.positions.find(hist_q).sort("closed_at", -1).limit(500)
        history = await hist_cursor.to_list(length=500)
        all_closed = await store.db.positions.find({"status": "closed"}).sort("closed_at", -1).to_list(length=10000)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_closed = [p for p in all_closed if p.get("closed_at") and as_utc(p.get("closed_at")) >= today_start]
        today_pnl = sum(float(p.get("realized_pnl", 0)) for p in today_closed)
        total_pnl = sum(float(p.get("realized_pnl", 0)) for p in all_closed)
        pnls = [float(p.get("realized_pnl", 0)) for p in all_closed]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        today_trades = len(today_closed)
        win_rate = (len(wins) / len(pnls) * 100) if pnls else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        largest_win = max(wins) if wins else 0.0
        largest_loss = min(losses) if losses else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        spot_by_symbol = dict(state.latest_price)
        for sym, mkt in state.latest_market_data.items():
            if mkt:
                s = mkt.get("spot")
                if s is not None and s > 0:
                    spot_by_symbol[sym] = float(s)
        for p in open_list + history:
            sym = p.get("symbol")
            if sym and sym not in spot_by_symbol:
                alt = f"{sym}-USD" if "-" not in sym else sym.replace("-USD", "")
                if alt in spot_by_symbol:
                    spot_by_symbol[sym] = spot_by_symbol[alt]
        open_positions_cost = sum(float(p.get("entry_price", 0)) * float(p.get("qty", 0)) for p in open_list)
        return JSONResponse(_mongo_to_json({
            "open": open_list, "history": history,
            "today_pnl": today_pnl, "total_pnl": total_pnl, "win_rate": win_rate, "total_trades": len(all_closed),
            "today_trades": today_trades, "wins_count": len(wins), "losses_count": len(losses),
            "avg_win": avg_win, "avg_loss": avg_loss, "largest_win": largest_win, "largest_loss": largest_loss,
            "profit_factor": profit_factor,
            "spot_by_symbol": spot_by_symbol,
            "open_positions_cost": open_positions_cost,
        }))

    async def get_orders(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol")
        limit = int(request.query_params.get("limit", 200))
        period = request.query_params.get("period", "all")
        q: dict = {}
        if symbol:
            q["symbol"] = symbol
        now = datetime.now(timezone.utc)
        if period == "day":
            q["created_at"] = {"$gte": now.replace(hour=0, minute=0, second=0, microsecond=0)}
        elif period == "week":
            q["created_at"] = {"$gte": now - timedelta(days=7)}
        elif period == "month":
            q["created_at"] = {"$gte": now - timedelta(days=30)}
        elif period == "year":
            q["created_at"] = {"$gte": now - timedelta(days=365)}
        cursor = store.db.orders.find(q).sort("created_at", -1).limit(limit)
        rows = _mongo_to_json(await cursor.to_list(length=limit))
        return JSONResponse(rows)

    async def get_candles(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol", "")
        timeframe = request.query_params.get("timeframe", "1m")
        limit = int(request.query_params.get("limit", 300))
        if not symbol:
            return JSONResponse({"detail": "symbol required"}, status_code=400)
        coll = store.db.candles_1m if timeframe == "1m" else store.db.candles_5m
        cursor = coll.find({"symbol": symbol}).sort("ts", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        rows.reverse()
        return JSONResponse(_mongo_to_json(rows))

    async def post_prices(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
        prices = body.get("prices", [])
        if not isinstance(prices, list):
            return JSONResponse({"detail": "prices must be a list"}, status_code=400)
        for p in prices:
            symbol = p.get("symbol", "")
            market_type = p.get("market_type", "crypto")
            spot = float(p.get("spot", 0))
            candles_1m = []
            for c in p.get("candles_1m") or []:
                ts = _parse_ts(c.get("ts")) if isinstance(c, dict) else None
                if ts:
                    candles_1m.append({
                        "ts": ts, "open": float(c.get("open", 0)), "high": float(c.get("high", 0)),
                        "low": float(c.get("low", 0)), "close": float(c.get("close", 0)),
                        "volume": float(c.get("volume", 0)),
                    })
            candles_5m = []
            for c in p.get("candles_5m") or []:
                ts = _parse_ts(c.get("ts")) if isinstance(c, dict) else None
                if ts:
                    candles_5m.append({
                        "ts": ts, "open": float(c.get("open", 0)), "high": float(c.get("high", 0)),
                        "low": float(c.get("low", 0)), "close": float(c.get("close", 0)),
                        "volume": float(c.get("volume", 0)), "vwap": c.get("vwap"),
                    })
            state.latest_market_data[symbol] = {
                "spot": spot, "candles_1m": candles_1m, "candles_5m": candles_5m,
                "received_at": utc_now(), "market_type": market_type,
            }
        return JSONResponse({"ok": True, "received": len(prices)})

    async def get_news_today(_: Request) -> JSONResponse:
        tz_str = getattr(settings, "news_timezone", "America/New_York")
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("America/New_York")
        today = datetime.now(tz).date().isoformat()
        doc = await store.db.news_daily_summary.find_one({"date": today})
        if not doc:
            return JSONResponse({"date": today, "summary": None, "sticky_notes": [], "asset_bias": {}})
        return JSONResponse(_mongo_to_json({
            "date": doc.get("date"),
            "timezone": doc.get("timezone"),
            "created_at": doc.get("created_at"),
            "summary": doc.get("summary", ""),
            "sticky_notes": doc.get("sticky_notes", []),
            "asset_bias": doc.get("asset_bias", {}),
        }))

    async def get_news_raw(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", 100))
        cursor = store.db.news_raw.find().sort("fetched_at", -1).limit(limit)
        rows = _mongo_to_json(await cursor.to_list(length=limit))
        return JSONResponse(rows)

    async def post_news_refresh(_: Request) -> JSONResponse:
        """Scrape news, store in MongoDB, retrieve from MongoDB, generate summary with OpenAI."""
        try:
            from news_analyzer import run_daily_news_analysis
            created = await run_daily_news_analysis(
                store,
                settings.news_timezone,
                settings.openai_api_key,
                force=True,
                scrape=True,
            )
            return JSONResponse({"ok": True, "created": created})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    async def post_news_summarize(_: Request) -> JSONResponse:
        """Generate today's summary from news already in MongoDB (no scrape)."""
        try:
            from news_analyzer import run_daily_news_analysis
            created = await run_daily_news_analysis(
                store,
                settings.news_timezone,
                settings.openai_api_key,
                force=True,
                scrape=False,
            )
            return JSONResponse({"ok": True, "created": created})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    async def get_popup(_: Request) -> JSONResponse:
        """Lightweight endpoint for extension popup: stats, spot prices, strike in one call."""
        all_closed = await store.db.positions.find({"status": "closed"}).sort("closed_at", -1).to_list(length=10000)
        open_list = await store.db.positions.find({"status": "open"}).sort("opened_at", -1).limit(200).to_list(length=200)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_closed = [p for p in all_closed if p.get("closed_at") and as_utc(p.get("closed_at")) >= today_start]
        today_pnl = sum(float(p.get("realized_pnl", 0)) for p in today_closed)
        total_pnl = sum(float(p.get("realized_pnl", 0)) for p in all_closed)
        pnls = [float(p.get("realized_pnl", 0)) for p in all_closed]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        spot_by_symbol = dict(state.latest_price)
        for sym, mkt in state.latest_market_data.items():
            if mkt and mkt.get("spot") is not None and float(mkt.get("spot", 0)) > 0:
                spot_by_symbol[sym] = float(mkt["spot"])
        for p in open_list:
            sym = p.get("symbol")
            if sym and sym not in spot_by_symbol:
                alt = f"{sym}-USD" if "-" not in sym else sym.replace("-USD", "")
                if alt in spot_by_symbol:
                    spot_by_symbol[sym] = spot_by_symbol[alt]
        for legacy, xstock in _STRIKE_LEGACY_TO_XSTOCK.items():
            if legacy in spot_by_symbol and xstock not in spot_by_symbol:
                spot_by_symbol[xstock] = spot_by_symbol[legacy]
            if f"{legacy}-USD" in spot_by_symbol and xstock not in spot_by_symbol:
                spot_by_symbol[xstock] = spot_by_symbol[f"{legacy}-USD"]
        closed_list = all_closed[:100]
        strike_doc = await store.db.strike_snapshots.find_one({}, sort=[("timestamp", -1)])
        strike_alloc_raw = strike_doc.get("allocations", {}) if strike_doc else {}
        strike_alloc = _normalize_strike_allocations(strike_alloc_raw)
        open_positions_cost = sum(float(p.get("entry_price", 0)) * float(p.get("qty", 0)) for p in open_list)
        return JSONResponse(_mongo_to_json({
            "ok": True,
            "stats": {
                "today_pnl": today_pnl,
                "total_pnl": total_pnl,
                "win_rate": (len(wins) / len(pnls) * 100) if pnls else 0.0,
                "today_trades": len(today_closed),
                "total_trades": len(all_closed),
                "wins_count": len(wins),
                "losses_count": len(losses),
                "open_positions_cost": open_positions_cost,
            },
            "spot_by_symbol": spot_by_symbol,
            "strike": {"allocations": strike_alloc},
            "open_positions": open_list,
            "closed_positions": closed_list,
        }))

    async def get_strike_latest(_: Request) -> JSONResponse:
        doc = await store.db.strike_snapshots.find_one({}, sort=[("timestamp", -1)])
        if not doc:
            return JSONResponse({"allocations": {}, "timestamp": None})
        alloc_raw = doc.get("allocations", {})
        allocations = _normalize_strike_allocations(alloc_raw)
        return JSONResponse(_mongo_to_json({
            "allocations": allocations,
            "timestamp": doc.get("timestamp"),
            "horizon": doc.get("horizon"),
        }))

    async def get_strike_history(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", 50))
        cursor = store.db.strike_snapshots.find({}, sort=[("timestamp", -1)]).limit(limit)
        rows = _mongo_to_json(await cursor.to_list(length=limit))
        return JSONResponse(rows)

    async def post_strike_refresh(_: Request) -> JSONResponse:
        try:
            from strike_tool import run_strike_refresh
            result = await run_strike_refresh(
                synth, store, state, settings.strike_symbols,
                synth_asset_map=settings.parse_synth_asset_map(),
            )
            return JSONResponse(_mongo_to_json({
                "ok": True,
                "allocations": _normalize_strike_allocations(result.get("allocations", {})),
            }))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    async def post_controls(request: Request) -> JSONResponse:
        try:
            req = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
        if req.get("enable_trading") is not None:
            state.trading_enabled = bool(req["enable_trading"])
        if req.get("paper_trading") is not None:
            state.paper_trading = bool(req["paper_trading"])
        if req.get("symbols") is not None:
            mapped: list[SymbolConfig] = []
            for item in req["symbols"]:
                p = str(item).strip().split(":")
                if len(p) == 2:
                    mkt = "crypto" if p[1].lower() == "crypto" else "equity"
                    mapped.append(SymbolConfig(symbol=p[0].upper(), market_type=mkt))
            state.symbols = mapped
        if req.get("close_position_symbol"):
            await store.db.positions.update_many(
                {"symbol": str(req["close_position_symbol"]).upper(), "status": "open"},
                {"$set": {"status": "closed", "closed_at": utc_now()}},
            )
        if req.get("close_position_id"):
            from bson import ObjectId
            try:
                oid = ObjectId(str(req["close_position_id"]))
            except Exception:
                return JSONResponse({"detail": "Invalid close_position_id"}, status_code=400)
            pos = await store.db.positions.find_one({"_id": oid, "status": "open"})
            if pos:
                sym = pos.get("symbol") or ""
                # Manual close: use current spot price for PnL (from Kraken WS / latest market data).
                spot_val = _spot_for_symbol(state, sym)
                if spot_val is None or spot_val <= 0:
                    return JSONResponse(
                        {
                            "detail": f"Current price not available for {sym}. Ensure market data is connected and try again.",
                            "code": "NO_SPOT_PRICE",
                        },
                        status_code=400,
                    )
                spot = float(spot_val)
                entry = float(pos.get("entry_price", 0))
                side = pos.get("side", "long")
                qty = float(pos.get("qty", 0))
                # PnL at current spot: long = (spot - entry) * qty, short = (entry - spot) * qty.
                pnl = (spot - entry) * qty if side == "long" else (entry - spot) * qty
                await store.db.positions.update_one(
                    {"_id": oid},
                    {"$set": {"status": "closed", "closed_at": utc_now()}, "$inc": {"realized_pnl": pnl}},
                )
        if req.get("risk_pct_crypto") is not None:
            state.crypto_risk_pct = float(req["risk_pct_crypto"])
        if req.get("risk_pct_equity") is not None:
            state.equity_risk_pct = float(req["risk_pct_equity"])
        return JSONResponse({"ok": True, "trading_enabled": state.trading_enabled, "paper_trading": state.paper_trading})

    async def stream_ws(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            ws_manager.disconnect(websocket)

    routes = [
        Route("/health", health),
        Route("/popup", get_popup),
        Route("/status", get_status),
        Route("/symbols", get_symbols),
        Route("/state", get_state),
        Route("/predictions", get_predictions),
        Route("/signals", get_signals),
        Route("/synth-calls", get_synth_calls),
        Route("/positions", get_positions),
        Route("/orders", get_orders),
        Route("/candles", get_candles),
        Route("/news/today", get_news_today),
        Route("/news/raw", get_news_raw),
        Route("/news/refresh", post_news_refresh, methods=["POST"]),
        Route("/news/summarize", post_news_summarize, methods=["POST"]),
        Route("/strike/latest", get_strike_latest),
        Route("/strike/history", get_strike_history),
        Route("/strike/refresh", post_strike_refresh, methods=["POST"]),
        Route("/prices", post_prices, methods=["POST"]),
        Route("/controls", post_controls, methods=["POST"]),
        WebSocketRoute("/stream", stream_ws),
    ]

    app = Starlette(debug=True, routes=routes, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.engine_state = state
    app.state.ws = ws_manager
    return app


async def _ws_pump(ws_manager: WSManager) -> None:
    async for event in ws_manager.stream_updates():
        await ws_manager.broadcast(event)


app = build_app()
