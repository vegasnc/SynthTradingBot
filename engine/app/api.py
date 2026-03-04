from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
from .market_data import fetch_crypto_prices
from .scheduler import EngineScheduler
from .state import EngineState
from .synth_client import SynthClient
from .utils import as_utc, utc_now
from .ws_manager import WSManager


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
        loop_seconds=settings.engine_loop_seconds,
        synth_refresh_minutes=settings.synth_refresh_minutes,
        synth_price_change_refresh_pct=settings.synth_price_change_refresh_pct,
        synth_price_change_period_minutes=settings.synth_price_change_period_minutes,
        market_strength_counter_trend_multiplier=settings.market_strength_counter_trend_multiplier,
        market_strength_lookback_minutes=settings.market_strength_lookback_minutes,
    )
    ws_manager = WSManager(state)

    async def _market_data_loop() -> None:
        """Fetch crypto prices server-side every 5s for fast position monitoring (crypto volatility)."""
        log = logging.getLogger("app.api")
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    prices = await fetch_crypto_prices(client, state.symbols)
                    if prices:
                        for sym, data in prices.items():
                            state.latest_market_data[sym] = data
                        state.last_market_data_success_at = utc_now()
                        state.last_market_data_error = None
                    else:
                        state.last_market_data_error = "All symbols failed (Binance/Kraken)"
                except Exception as e:
                    state.last_market_data_error = str(e)
                    log.exception("market data fetch failed: %s", e)
                await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        await store.setup_indexes()
        scheduler_task = asyncio.create_task(scheduler.run_forever())
        ws_task = asyncio.create_task(_ws_pump(ws_manager))
        market_task = asyncio.create_task(_market_data_loop())
        yield
        scheduler_task.cancel()
        ws_task.cancel()
        market_task.cancel()

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
            "hint": "Crypto: server-side Binance/Kraken. Equity: needs dashboard POST /prices.",
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
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        all_closed = await store.db.positions.find({"status": "closed"}).sort("closed_at", -1).to_list(length=10000)
        today_pnl = sum(float(p.get("realized_pnl", 0)) for p in all_closed if as_utc(p.get("closed_at")) >= today_start)
        total_pnl = sum(float(p.get("realized_pnl", 0)) for p in all_closed)
        pnls = [float(p.get("realized_pnl", 0)) for p in all_closed]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
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
            if sym not in spot_by_symbol and mkt:
                s = mkt.get("spot")
                if s is not None:
                    spot_by_symbol[sym] = float(s)
        return JSONResponse(_mongo_to_json({
            "open": open_list, "history": history,
            "today_pnl": today_pnl, "total_pnl": total_pnl, "win_rate": win_rate, "total_trades": len(all_closed),
            "avg_win": avg_win, "avg_loss": avg_loss, "largest_win": largest_win, "largest_loss": largest_loss,
            "profit_factor": profit_factor,
            "spot_by_symbol": spot_by_symbol,
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
        Route("/status", get_status),
        Route("/symbols", get_symbols),
        Route("/state", get_state),
        Route("/predictions", get_predictions),
        Route("/signals", get_signals),
        Route("/synth-calls", get_synth_calls),
        Route("/positions", get_positions),
        Route("/orders", get_orders),
        Route("/candles", get_candles),
        Route("/prices", post_prices, methods=["POST"]),
        Route("/controls", post_controls, methods=["POST"]),
        WebSocketRoute("/stream", stream_ws),
    ]

    app = Starlette(debug=True, routes=routes, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
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
