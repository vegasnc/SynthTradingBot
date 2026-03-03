from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

from pymongo.errors import DuplicateKeyError

from .broker import BrokerInterface
from .config import SymbolConfig
from .db import MongoStore
from .state import EngineState
from .strategy import build_decision, compute_position_size, confirm_entry, tighten_stop
from .synth_client import SynthClient
from .utils import as_utc, floor_to_minute, is_equity_tradable_now, utc_now

logger = logging.getLogger(__name__)


def synth_asset_for_symbol(symbol: str) -> str:
    if symbol.endswith("-USD"):
        return symbol.split("-")[0]
    return symbol


class EngineScheduler:
    def __init__(
        self,
        store: MongoStore,
        synth: SynthClient,
        broker: BrokerInterface,
        state: EngineState,
        loop_seconds: int = 60,
        synth_refresh_minutes: int = 10,
        synth_price_change_refresh_pct: float = 1.0,
        synth_price_change_period_minutes: int = 2,
    ) -> None:
        self.store = store
        self.synth = synth
        self.broker = broker
        self.state = state
        self.loop_seconds = loop_seconds
        self.synth_refresh_minutes = synth_refresh_minutes
        self.synth_price_change_refresh_pct = synth_price_change_refresh_pct
        self.synth_price_change_period_minutes = synth_price_change_period_minutes
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.exception("scheduler tick failed: %s", exc)
                await self.store.insert_event("error", "scheduler_tick", str(exc))
            await asyncio.sleep(self.loop_seconds)

    async def tick(self) -> None:
        for cfg in self.state.symbols:
            await self._tick_symbol(cfg)

    async def _tick_symbol(self, cfg: SymbolConfig) -> None:
        symbol = cfg.symbol
        now = floor_to_minute(utc_now())
        if cfg.market_type == "equity" and not is_equity_tradable_now(now):
            return

        mkt = self.state.latest_market_data.get(symbol)
        if not mkt:
            logger.info("no market data for %s (waiting for dashboard to push prices)", symbol)
            return
        received = as_utc(mkt.get("received_at"))
        if not received or received < (utc_now() - timedelta(minutes=5)):
            logger.info("market data stale for %s (received_at=%s)", symbol, mkt.get("received_at"))
            return
        candles_1m = mkt.get("candles_1m") or []
        candles_5m = mkt.get("candles_5m") or []
        if not candles_1m or not candles_5m:
            return
        spot = float(mkt.get("spot") or candles_1m[-1].get("close", 0))
        self.state.latest_price[symbol] = spot

        await self._persist_candles(symbol, candles_1m, candles_5m)

        pred = await self._ensure_prediction(cfg, spot, candles_1m)
        if not pred:
            return
        pct = pred["percentiles"]
        in_cooldown = await self._in_cooldown(symbol, now)
        decision = build_decision(
            spot=spot,
            pct=self.synth.parse_percentiles({"percentiles": pct}),
            market_type=cfg.market_type,
            in_cooldown=in_cooldown,
        )
        entry_ok = confirm_entry(decision.bias, decision.entry, candles_1m[-3:], candles_5m[-2:])
        decision.allowed_to_trade = decision.allowed_to_trade and entry_ok and self.state.trading_enabled
        if not entry_ok:
            decision.reasons.append("entry_confirmation_failed")
        signal_doc = {
            "symbol": symbol,
            "market_type": cfg.market_type,
            "timestamp": now,
            "spot": spot,
            "bias": decision.bias,
            "edge": decision.edge,
            "uncertainty": decision.uncertainty,
            "allowed_to_trade": decision.allowed_to_trade,
            "reasons": decision.reasons,
            "levels": {
                "entry": decision.entry,
                "stop": decision.stop,
                "tp1": decision.tp1,
                "tp2": decision.tp2,
                "p05": pct["p05"],
                "p20": pct["p20"],
                "p35": pct["p35"],
                "p50": pct["p50"],
                "p65": pct["p65"],
                "p80": pct["p80"],
                "p95": pct["p95"],
                "range": decision.range_,
                "central_range": decision.central_range,
            },
            "flags": decision.flags | {"entry_confirmation_pass": entry_ok},
        }
        await self.store.db.signals.insert_one(signal_doc)
        self.state.latest_signal[symbol] = signal_doc
        self.state.push_update("signal", signal_doc)

        last_candle = candles_1m[-1] if candles_1m else {}
        await self._manage_position(cfg, signal_doc, spot, last_candle)
        if decision.allowed_to_trade:
            await self._try_open_position(cfg, signal_doc)

    async def _persist_candles(self, symbol: str, candles_1m: list, candles_5m: list) -> None:
        """Persist frontend-provided candles to Mongo for /candles endpoint."""
        for c in candles_1m[-20:]:
            doc = {"symbol": symbol, "ts": c.get("ts"), "open": c.get("open"), "high": c.get("high"), "low": c.get("low"), "close": c.get("close"), "volume": c.get("volume", 0)}
            try:
                await self.store.db.candles_1m.insert_one(doc)
            except DuplicateKeyError:
                pass
        for c in candles_5m[-20:]:
            doc = {"symbol": symbol, "ts": c.get("ts"), "open": c.get("open"), "high": c.get("high"), "low": c.get("low"), "close": c.get("close"), "volume": c.get("volume", 0), "vwap": c.get("vwap")}
            try:
                await self.store.db.candles_5m.insert_one(doc)
            except DuplicateKeyError:
                pass

    async def _ensure_prediction(self, cfg: SymbolConfig, spot: float, candles_1m: list) -> dict[str, Any] | None:
        symbol = cfg.symbol
        latest = await self.store.db.synth_predictions.find_one({"symbol": symbol}, sort=[("timestamp", -1)])
        now = utc_now()
        force_refresh = False
        if latest:
            price_ago = None
            period = self.synth_price_change_period_minutes
            if candles_1m and len(candles_1m) > period:
                idx = -(period + 1)
                c = candles_1m[idx]
                try:
                    price_ago = float(c.get("close", 0) or 0)
                except (TypeError, ValueError):
                    pass
            if price_ago and price_ago > 0:
                pct_change = abs(spot - price_ago) / price_ago * 100
                if pct_change >= self.synth_price_change_refresh_pct:
                    force_refresh = True
            if not force_refresh and latest.get("next_refresh_at") and as_utc(latest["next_refresh_at"]) > now:
                return latest

        asset = synth_asset_for_symbol(symbol)
        payload = await self.synth.get_prediction_percentiles(asset=asset, horizon="1h")
        pct = self.synth.parse_percentiles(payload)
        range_ = pct.p95 - pct.p05
        uncertainty = range_ / spot
        next_refresh_at = now + timedelta(minutes=self.synth_refresh_minutes)
        doc = {
            "symbol": symbol,
            "market_type": cfg.market_type,
            "horizon": "1h",
            "timestamp": now,
            "fetched_at": now,
            "spot_at_fetch": spot,
            "percentiles": asdict(pct),
            "uncertainty": uncertainty,
            "range": range_,
            "central_range": pct.p80 - pct.p20,
            "next_refresh_at": next_refresh_at,
            "raw": payload,
        }
        await self.store.db.synth_predictions.insert_one(doc)
        self.state.latest_prediction[symbol] = doc
        self.state.push_update("prediction", {"symbol": symbol, "uncertainty": uncertainty})
        return doc

    async def _open_position_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        return await self.store.db.positions.find_one({"symbol": symbol, "status": "open"})

    async def _open_positions_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        cursor = self.store.db.positions.find({"symbol": symbol, "status": "open"}).sort("opened_at", -1)
        return await cursor.to_list(length=50)

    async def _try_open_position(self, cfg: SymbolConfig, signal: dict[str, Any]) -> None:
        symbol = cfg.symbol
        new_side = signal["bias"]
        open_positions = await self._open_positions_for_symbol(symbol)
        for pos in open_positions:
            if pos.get("side") == new_side:
                return
        side = "buy" if new_side == "long" else "sell"
        levels = signal["levels"]
        risk_pct = self.state.crypto_risk_pct if cfg.market_type == "crypto" else self.state.equity_risk_pct
        entry_price = levels["entry"]
        stop_price = levels["stop"]
        # Optional liquidation-aware adjustment for crypto: move stop 0.3% outside nearest high-probability band and reduce size.
        size_scale = 1.0
        if cfg.market_type == "crypto":
            liq = await self.synth.get_liquidation_insight(synth_asset_for_symbol(symbol), horizon="1h")
            adjusted = self._adjust_stop_from_liquidation(stop_price, liq, signal["bias"])
            if adjusted is not None:
                stop_price = adjusted
                size_scale = 0.85
        qty = compute_position_size(
            account_equity=self.state.account_equity,
            risk_pct=risk_pct,
            entry_price=entry_price,
            stop_price=stop_price,
            max_symbol_exposure=self.state.max_symbol_exposure,
        )
        qty *= size_scale
        if not await self._within_portfolio_exposure(symbol, entry_price, qty):
            logger.info("skip open %s %s: portfolio/symbol exposure limit", symbol, side)
            return
        if qty <= 0:
            logger.info("skip open %s %s: qty<=0", symbol, side)
            return
        minute_bucket = floor_to_minute(utc_now()).isoformat()
        idempotency_key = str(uuid5(NAMESPACE_DNS, f"{symbol}:{side}:{minute_bucket}"))
        if await self.store.db.orders.find_one({"client_order_id": idempotency_key}):
            logger.debug("skip open %s %s: duplicate order in same minute", symbol, side)
            return
        order = await self.broker.place_order(symbol, side, qty, entry_price, cfg.market_type, client_order_id=idempotency_key)
        order_doc = {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "symbol": symbol,
            "market_type": cfg.market_type,
            "side": side,
            "qty": qty,
            "price": order.fill_price,
            "status": order.status,
            "filled_at": utc_now(),
            "created_at": utc_now(),
            "reason": order.reason,
        }
        await self.store.db.orders.insert_one(order_doc)
        self.state.push_update("order_created", order_doc)
        if order.status != "filled":
            return
        pos = {
            "position_id": order.client_order_id,
            "symbol": symbol,
            "market_type": cfg.market_type,
            "side": signal["bias"],
            "qty": qty,
            "entry_price": order.fill_price,
            "stop_price": stop_price,
            "tp1": levels["tp1"],
            "tp2": levels["tp2"],
            "runner_qty": qty * 0.2,
            "tp1_done": False,
            "tp2_done": False,
            "status": "open",
            "opened_at": utc_now(),
            "closed_at": None,
            "realized_pnl": 0.0,
            "cooldown_until": None,
            "metadata": {"signal": signal},
        }
        await self.store.db.positions.insert_one(pos)
        self.state.push_update("position_opened", pos)

    async def _manage_position(
        self, cfg: SymbolConfig, signal: dict[str, Any], spot: float, last_candle: dict
    ) -> None:
        candle_low = float(last_candle.get("low") or last_candle.get("close") or spot)
        candle_high = float(last_candle.get("high") or last_candle.get("close") or spot)
        open_positions = await self._open_positions_for_symbol(cfg.symbol)
        for pos in open_positions:
            side = pos["side"]
            qty = pos["qty"]
            tp1 = pos["tp1"]
            tp2 = pos["tp2"]
            stop = pos["stop_price"]

            hit_tp1 = (side == "long" and spot >= tp1) or (side == "short" and spot <= tp1)
            hit_tp2 = (side == "long" and spot >= tp2) or (side == "short" and spot <= tp2)
            hit_stop_long = side == "long" and (spot <= stop or candle_low <= stop)
            hit_stop_short = side == "short" and (spot >= stop or candle_high >= stop)
            hit_stop = hit_stop_long or hit_stop_short

            if hit_tp1 and not pos["tp1_done"]:
                close_qty = qty * 0.4
                pnl = ((spot - pos["entry_price"]) if side == "long" else (pos["entry_price"] - spot)) * close_qty
                await self.store.db.positions.update_one(
                    {"_id": pos["_id"]},
                    {
                        "$set": {"tp1_done": True, "stop_price": signal["levels"]["p35"] if side == "long" else signal["levels"]["p65"]},
                        "$inc": {"realized_pnl": pnl, "qty": -close_qty},
                    },
                )
            if hit_tp2 and not pos["tp2_done"]:
                close_qty = qty * 0.4
                pnl = ((spot - pos["entry_price"]) if side == "long" else (pos["entry_price"] - spot)) * close_qty
                await self.store.db.positions.update_one(
                    {"_id": pos["_id"]},
                    {"$set": {"tp2_done": True}, "$inc": {"realized_pnl": pnl, "qty": -close_qty}},
                )

            if hit_tp1:
                new_stop = tighten_stop(stop, side, self.synth.parse_percentiles({"percentiles": signal["levels"]}), tp1_hit=True)
                if side == "long":
                    new_stop = max(stop, new_stop)
                else:
                    new_stop = min(stop, new_stop)
                await self.store.db.positions.update_one({"_id": pos["_id"]}, {"$set": {"stop_price": new_stop}})

            pos_cur = await self.store.db.positions.find_one({"_id": pos["_id"]})
            if not pos_cur:
                continue
            if hit_stop or pos_cur["qty"] <= (pos_cur["runner_qty"] + 1e-9):
                close_qty = pos_cur["qty"]
                pnl = ((spot - pos_cur["entry_price"]) if side == "long" else (pos_cur["entry_price"] - spot)) * close_qty
                cooldown = utc_now() + timedelta(minutes=30) if hit_stop else None
                await self.store.db.positions.update_one(
                    {"_id": pos_cur["_id"]},
                    {
                        "$set": {"status": "closed", "closed_at": utc_now(), "cooldown_until": cooldown},
                        "$inc": {"realized_pnl": pnl},
                    },
                )
                logger.info("closed %s %s: hit_stop=%s spot=%.2f stop=%.2f", cfg.symbol, side, hit_stop, spot, stop)
                self.state.push_update("position_closed", {"symbol": cfg.symbol, "hit_stop": hit_stop, "pnl": pnl})

    async def _in_cooldown(self, symbol: str, now) -> bool:
        last_closed = await self.store.db.positions.find_one(
            {"symbol": symbol, "status": "closed", "cooldown_until": {"$ne": None}},
            sort=[("closed_at", -1)],
        )
        if not last_closed:
            return False
        return as_utc(last_closed["cooldown_until"]) > now

    async def _within_portfolio_exposure(self, symbol: str, entry_price: float, qty: float) -> bool:
        notional = entry_price * qty
        open_positions = await self.store.db.positions.find({"status": "open"}).to_list(length=200)
        current_total = sum(float(p["qty"]) * float(p["entry_price"]) for p in open_positions)
        if current_total + notional > (self.state.account_equity * self.state.max_portfolio_exposure):
            return False
        symbol_total = sum(float(p["qty"]) * float(p["entry_price"]) for p in open_positions if p["symbol"] == symbol)
        if symbol_total + notional > (self.state.account_equity * self.state.max_symbol_exposure):
            return False
        return True

    def _adjust_stop_from_liquidation(self, stop_price: float, payload: dict[str, Any] | None, bias: str) -> float | None:
        if not payload:
            return None
        levels = payload.get("levels") or payload.get("data") or []
        cluster_price = None
        cluster_prob = 0.0
        for row in levels:
            if not isinstance(row, dict):
                continue
            px = row.get("price") or row.get("level")
            lp = row.get("long_6h") or row.get("long_prob") or row.get("prob_long")
            sp = row.get("short_6h") or row.get("short_prob") or row.get("prob_short")
            try:
                price = float(px)
                prob = float(lp if bias == "long" else sp)
            except (TypeError, ValueError):
                continue
            if abs(price - stop_price) / max(stop_price, 1e-9) <= 0.01 and prob > cluster_prob and prob >= 0.2:
                cluster_prob = prob
                cluster_price = price
        if cluster_price is None:
            return None
        if bias == "long":
            return min(stop_price, cluster_price * (1 - 0.003))
        return max(stop_price, cluster_price * (1 + 0.003))

