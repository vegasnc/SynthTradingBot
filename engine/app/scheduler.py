from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

import httpx
from pymongo.errors import DuplicateKeyError

from .broker import BrokerInterface
from .config import SymbolConfig, Settings
from .db import MongoStore
from .state import EngineState
from .strategy import (
    build_decision_with_market_strength,
    compute_position_size,
    confirm_entry,
    market_direction_from_momentum,
)
from .market_data import fetch_spot_prices
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
        settings: Settings,
        loop_seconds: int = 60,
        synth_refresh_minutes: int = 10,
        synth_price_change_refresh_pct: float = 1.0,
        synth_price_change_period_minutes: int = 2,
        market_strength_counter_trend_multiplier: float = 1.5,
        market_strength_lookback_minutes: int = 120,
    ) -> None:
        self.store = store
        self.synth = synth
        self.broker = broker
        self.state = state
        self.settings = settings
        self.loop_seconds = loop_seconds
        self.synth_refresh_minutes = synth_refresh_minutes
        self.synth_price_change_refresh_pct = synth_price_change_refresh_pct
        self.synth_price_change_period_minutes = synth_price_change_period_minutes
        self.market_strength_counter_trend_multiplier = market_strength_counter_trend_multiplier
        self.market_strength_lookback_minutes = market_strength_lookback_minutes
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        monitor_task = asyncio.create_task(self._position_monitor_loop())
        news_task = asyncio.create_task(self._daily_news_loop())
        strike_task = asyncio.create_task(self._strike_refresh_loop())
        try:
            while self._running:
                try:
                    await self.tick()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("scheduler tick failed: %s", exc)
                    await self.store.insert_event("error", "scheduler_tick", str(exc))
                await asyncio.sleep(self.loop_seconds)
        finally:
            monitor_task.cancel()
            news_task.cancel()
            strike_task.cancel()
            for t in (monitor_task, news_task, strike_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _daily_news_loop(self) -> None:
        """Run daily news analysis at 00:05 in NEWS_TIMEZONE."""
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(self.settings.news_timezone)
        except Exception:
            tz = ZoneInfo("America/New_York")
        while self._running:
            try:
                now = datetime.now(tz)
                next_run = now.replace(hour=0, minute=5, second=0, microsecond=0)
                if now >= next_run:
                    next_run = next_run + timedelta(days=1)
                wait_secs = (next_run - now).total_seconds()
                wait_secs = max(60, min(wait_secs, 86400))
                for _ in range(int(wait_secs)):
                    if not self._running:
                        return
                    await asyncio.sleep(1)
                if not self._running:
                    return
                from news_analyzer import run_daily_news_analysis
                await run_daily_news_analysis(
                    self.store,
                    self.settings.news_timezone,
                    self.settings.openai_api_key,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("daily news loop failed: %s", exc)
                await asyncio.sleep(3600)

    async def _strike_refresh_loop(self) -> None:
        """Run strike allocation refresh every STRIKE_REFRESH_MINUTES."""
        interval_secs = max(60, self.settings.strike_refresh_minutes * 60)
        while self._running:
            try:
                from strike_tool import run_strike_refresh
                await run_strike_refresh(
                    self.synth,
                    self.store,
                    self.state,
                    self.settings.strike_symbols,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("strike refresh failed: %s", exc)
            for _ in range(int(interval_secs)):
                if not self._running:
                    return
                await asyncio.sleep(1)

    async def _position_monitor_loop(self) -> None:
        """Check open positions for stop/TP hits every 1s for fast crypto exits."""
        while self._running:
            await asyncio.sleep(1)
            try:
                await self._check_positions_now()
            except Exception as exc:  # noqa: BLE001
                logger.exception("position monitor failed: %s", exc)

    async def _check_positions_now(self) -> None:
        """Immediate check of all open positions for stop/TP - uses latest market data or fetches spot directly."""
        cursor = self.store.db.positions.find({"status": "open"})
        open_positions = await cursor.to_list(length=200)
        symbol_to_market = {p["symbol"]: p.get("market_type", "crypto") for p in open_positions}
        symbols_to_check = list(symbol_to_market.keys())
        if not symbols_to_check:
            return

        for symbol in symbols_to_check:
            alt = f"{symbol}-USD" if "-" not in symbol else symbol.replace("-USD", "")
            mkt = self.state.latest_market_data.get(symbol) or self.state.latest_market_data.get(alt)
            candles_1m = (mkt or {}).get("candles_1m") or []
            spot = 0.0
            if mkt:
                spot = float(mkt.get("spot", 0) or (candles_1m[-1].get("close", 0) if candles_1m else 0))
            if (not spot or spot <= 0) and symbol_to_market.get(symbol) == "crypto":
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        cfgs = [SymbolConfig(symbol=symbol, market_type=symbol_to_market.get(symbol, "crypto"))]
                        spots = await fetch_spot_prices(client, cfgs)
                        spot = float(spots.get(symbol, 0) or spots.get(alt, 0))
                        if spot and spot > 0:
                            existing = mkt or {}
                            self.state.latest_market_data[symbol] = {
                                **existing,
                                "spot": spot,
                                "candles_1m": existing.get("candles_1m") or [],
                                "candles_5m": existing.get("candles_5m") or [],
                                "received_at": utc_now(),
                            }
                except Exception as e:
                    logger.warning("position monitor: fetch spot for %s failed: %s", symbol, e)
            if not spot or spot <= 0:
                continue
            last_candle = candles_1m[-1] if candles_1m else {"open": spot, "high": spot, "low": spot, "close": spot}
            cfg = next((c for c in self.state.symbols if c.symbol == symbol or c.symbol == alt), None)
            if not cfg:
                cfg = SymbolConfig(symbol=symbol, market_type=symbol_to_market.get(symbol, "crypto"))
            signal = self.state.latest_signal.get(symbol)
            if not signal:
                pos_with_meta = next(
                    (p for p in open_positions if p["symbol"] == symbol and p.get("metadata", {}).get("signal")),
                    None,
                )
                signal = pos_with_meta.get("metadata", {}).get("signal") if pos_with_meta else None
            if not signal:
                latest = await self.store.db.signals.find_one({"symbol": symbol}, sort=[("timestamp", -1)])
                if latest:
                    signal = latest
                    self.state.latest_signal[symbol] = latest
            await self._manage_position(cfg, signal, spot, last_candle)

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
        market_dir = market_direction_from_momentum(
            spot, candles_5m, self.market_strength_lookback_minutes
        )
        decision = build_decision_with_market_strength(
            spot=spot,
            pct=self.synth.parse_percentiles({"percentiles": pct}),
            market_type=cfg.market_type,
            counter_trend_multiplier=self.market_strength_counter_trend_multiplier,
            market_direction_override=market_dir,
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
                "tp": decision.tp,
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
        result = await self.store.db.signals.insert_one(signal_doc)
        signal_id = result.inserted_id
        self.state.latest_signal[symbol] = signal_doc
        self.state.push_update("signal", signal_doc)

        last_candle = candles_1m[-1] if candles_1m else {}
        await self._manage_position(cfg, signal_doc, spot, last_candle)
        if decision.allowed_to_trade:
            skip_reason = await self._try_open_position(cfg, signal_doc, spot)
            if skip_reason:
                await self.store.db.signals.update_one(
                    {"_id": signal_id},
                    {"$set": {"trade_skipped_reason": skip_reason}},
                )

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
        adaptive_mins = SynthClient.adaptive_refresh_minutes(uncertainty, cfg.market_type)
        next_refresh_at = now + timedelta(minutes=adaptive_mins)
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

    async def _try_open_position(self, cfg: SymbolConfig, signal: dict[str, Any], spot: float) -> str | None:
        """Try to open a position. Returns skip_reason if skipped, None if opened."""
        symbol = cfg.symbol
        new_side = signal["bias"]
        levels = signal["levels"]
        entry_price = float(levels["entry"])
        tp = float(levels.get("tp", levels.get("tp1", entry_price)))
        p05 = float(levels.get("p05", 0))
        p95 = float(levels.get("p95", 0))

        # 1) Minimum expected profit filter
        expected_profit = abs(tp - entry_price) / max(entry_price, 1e-9)
        if expected_profit < self.settings.min_expected_profit:
            reason = f"expected_profit={expected_profit:.4f} < threshold={self.settings.min_expected_profit}"
            logger.info("Trade skipped for %s: %s", symbol, reason)
            return reason

        # 2) Volatility width filter
        if spot > 1e-9 and p95 > p05:
            range_width = (p95 - p05) / spot
            if range_width < self.settings.min_volatility_width:
                reason = f"range_width={range_width:.4f} < MIN_VOLATILITY_WIDTH={self.settings.min_volatility_width}"
                logger.info("Trade skipped for %s: %s", symbol, reason)
                return reason

        # 3) Fee-aware profit check
        if expected_profit <= 3 * self.settings.trading_fee_rate:
            reason = f"expected_profit={expected_profit:.4f} <= 3*fee={3 * self.settings.trading_fee_rate}"
            logger.info("Trade skipped for %s: %s", symbol, reason)
            return reason

        open_positions = await self._open_positions_for_symbol(symbol)
        same_side = [p for p in open_positions if p.get("side") == new_side]
        if same_side:
            existing_entries = [float(p["entry_price"]) for p in same_side]
            if new_side == "long":
                best_existing = min(existing_entries)
                if entry_price >= best_existing:
                    reason = f"new entry {entry_price:.2f} not better than existing (min={best_existing:.2f})"
                    logger.info("skip open %s long: %s", symbol, reason)
                    return reason
                if spot > best_existing:
                    reason = f"would chase: spot {spot:.2f} above existing entry {best_existing:.2f}"
                    logger.info("skip open %s long: %s", symbol, reason)
                    return reason
            else:
                best_existing = max(existing_entries)
                if entry_price <= best_existing:
                    reason = f"new entry {entry_price:.2f} not better than existing (max={best_existing:.2f})"
                    logger.info("skip open %s short: %s", symbol, reason)
                    return reason
                if spot < best_existing:
                    reason = f"would chase: spot {spot:.2f} below existing entry {best_existing:.2f}"
                    logger.info("skip open %s short: %s", symbol, reason)
                    return reason
        stop_price = float(levels.get("stop", 0))
        side = "buy" if new_side == "long" else "sell"
        risk_pct = self.state.crypto_risk_pct if cfg.market_type == "crypto" else self.state.equity_risk_pct
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
            reason = "portfolio/symbol exposure limit"
            logger.info("skip open %s %s: %s", symbol, side, reason)
            return reason
        if qty <= 0:
            reason = "qty<=0"
            logger.info("skip open %s %s: %s", symbol, side, reason)
            return reason
        minute_bucket = floor_to_minute(utc_now()).isoformat()
        idempotency_key = str(uuid5(NAMESPACE_DNS, f"{symbol}:{side}:{minute_bucket}"))
        if await self.store.db.orders.find_one({"client_order_id": idempotency_key}):
            reason = "duplicate order in same minute"
            logger.info("Trade skipped for %s: %s", symbol, reason)
            return reason
        logger.info("Opening %s %s: qty=%.4f entry=%.2f", symbol, side, qty, entry_price)
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
            reason = f"order not filled (status={order.status})"
            logger.info("Trade skipped for %s: %s", symbol, reason)
            return reason
        logger.info("Position opened for %s %s at %.2f", symbol, side, order.fill_price)
        pos = {
            "position_id": order.client_order_id,
            "symbol": symbol,
            "market_type": cfg.market_type,
            "side": signal["bias"],
            "qty": qty,
            "entry_price": order.fill_price,
            "stop_price": stop_price,
            "tp": float(levels.get("tp", (float(levels.get("tp1", entry_price)) + float(levels.get("tp2", entry_price))) / 2)),
            "tp1": levels.get("tp1"),
            "tp2": levels.get("tp2"),
            "status": "open",
            "opened_at": utc_now(),
            "closed_at": None,
            "realized_pnl": 0.0,
            "cooldown_until": None,
            "metadata": {"signal": signal},
        }
        await self.store.db.positions.insert_one(pos)
        self.state.push_update("position_opened", pos)
        return None

    async def _manage_position(
        self, cfg: SymbolConfig, signal: dict[str, Any] | None, spot: float, last_candle: dict
    ) -> None:
        candle_low = float(last_candle.get("low") or last_candle.get("close") or spot)
        candle_high = float(last_candle.get("high") or last_candle.get("close") or spot)
        open_positions = await self._open_positions_for_symbol(cfg.symbol)
        for pos in open_positions:
            side = pos["side"]
            qty = float(pos["qty"])
            tp = float(pos.get("tp") or pos.get("tp1") or pos.get("tp2") or pos["entry_price"])
            stop = float(pos.get("stop_price") or 0)

            hit_tp = (side == "long" and spot >= tp) or (side == "short" and spot <= tp)
            hit_stop_long = side == "long" and (spot <= stop or candle_low <= stop)
            hit_stop_short = side == "short" and (spot >= stop or candle_high >= stop)
            hit_stop = hit_stop_long or hit_stop_short

            if hit_tp and pos.get("status") == "open":
                pos_cur = await self.store.db.positions.find_one({"_id": pos["_id"]})
                if pos_cur and pos_cur.get("status") == "open":
                    close_qty = float(pos_cur["qty"])
                    pnl = ((spot - pos_cur["entry_price"]) if side == "long" else (pos_cur["entry_price"] - spot)) * close_qty
                    await self.store.db.positions.update_one(
                        {"_id": pos["_id"]},
                        {
                            "$set": {"status": "closed", "closed_at": utc_now(), "cooldown_until": None},
                            "$inc": {"realized_pnl": pnl},
                        },
                    )
                    logger.info("TP hit - closed %s %s: spot=%.2f tp=%.2f qty=%.4f pnl=%.2f", cfg.symbol, side, spot, tp, close_qty, pnl)
                    self.state.push_update("position_closed", {"symbol": cfg.symbol, "hit_stop": False, "pnl": pnl, "reason": "tp"})
                    continue

            pos_cur = await self.store.db.positions.find_one({"_id": pos["_id"]})
            if not pos_cur:
                continue
            if hit_stop:
                close_qty = pos_cur["qty"]
                pnl = ((spot - pos_cur["entry_price"]) if side == "long" else (pos_cur["entry_price"] - spot)) * close_qty
                await self.store.db.positions.update_one(
                    {"_id": pos_cur["_id"]},
                    {
                        "$set": {"status": "closed", "closed_at": utc_now(), "cooldown_until": None},
                        "$inc": {"realized_pnl": pnl},
                    },
                )
                logger.info("closed %s %s: hit_stop=%s spot=%.2f stop=%.2f pnl=%.2f", cfg.symbol, side, hit_stop, spot, stop, pnl)
                self.state.push_update("position_closed", {"symbol": cfg.symbol, "hit_stop": hit_stop, "pnl": pnl})

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

