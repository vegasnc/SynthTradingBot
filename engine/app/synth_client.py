from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings
from .models import Percentiles
from .utils import utc_now


class SynthClient:
    def __init__(
        self,
        settings: Settings,
        on_api_call: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self.settings = settings
        self._on_api_call = on_api_call
        self._endpoints = self._load_endpoints(settings.synth_file_path())
        self.base_url = self._endpoints.get("baseUrl", "https://api.synthdata.co")
        self._client = httpx.AsyncClient(timeout=20.0)

    def _load_endpoints(self, file_path: Path) -> dict[str, Any]:
        if not file_path.exists():
            return {"baseUrl": "https://api.synthdata.co", "endpoints": []}
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Apikey {self.settings.synth_api_key}"}

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.8, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def get_prediction_percentiles(self, asset: str, horizon: str = "1h") -> dict[str, Any]:
        api_path = "/insights/prediction-percentiles"
        if self._on_api_call:
            coro = self._on_api_call(api_path, {"asset": asset, "horizon": horizon})
            if coro is not None:
                await coro
        resp = await self._client.get(
            f"{self.base_url}{api_path}",
            params={"asset": asset, "horizon": horizon},
            headers=self._headers(),
        )
        if resp.status_code == 429:
            raise httpx.HTTPError("rate limited")
        resp.raise_for_status()
        return resp.json()

    async def get_liquidation_insight(self, asset: str, horizon: str = "1h") -> dict[str, Any] | None:
        api_path = "/insights/liquidation"
        if self._on_api_call:
            coro = self._on_api_call(api_path, {"asset": asset, "horizon": horizon})
            if coro is not None:
                await coro
        try:
            resp = await self._client.get(
                f"{self.base_url}{api_path}",
                params={"asset": asset, "horizon": horizon},
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            return resp.json()
        except httpx.HTTPError:
            return None

    @staticmethod
    def parse_percentiles(payload: dict[str, Any]) -> Percentiles:
        """Parse Synth API response. Supports forecast_future.percentiles[-1] with decimal keys (0.05, 0.5, etc)."""
        candidates: dict[str, Any] = {}
        pct_arr = (payload.get("forecast_future") or {}).get("percentiles")
        if isinstance(pct_arr, list) and pct_arr:
            candidates = pct_arr[-1] if isinstance(pct_arr[-1], dict) else {}
        if not candidates:
            candidates = payload.get("percentiles") or payload.get("data") or payload
        if not isinstance(candidates, dict):
            candidates = {}
        key_map = {
            "p05": ["0.05", "P05", "p05", "5"],
            "p20": ["0.2", "0.20", "P20", "p20", "20"],
            "p35": ["0.35", "P35", "p35", "35"],
            "p50": ["0.5", "0.50", "P50", "p50", "50"],
            "p65": ["0.65", "P65", "p65", "65"],
            "p80": ["0.8", "0.80", "P80", "p80", "80"],
            "p95": ["0.95", "P95", "p95", "95"],
        }
        out: dict[str, float] = {}
        for key, aliases in key_map.items():
            for alias in aliases:
                if alias in candidates:
                    try:
                        out[key] = float(candidates[alias])
                        break
                    except (TypeError, ValueError):
                        continue
            if key not in out:
                raise ValueError(f"missing percentile key: {key} (got keys: {list(candidates.keys())})")
        return Percentiles(**out)

    @staticmethod
    def adaptive_refresh_minutes(uncertainty: float, market_type: str) -> int:
        if uncertainty < 0.02:
            return 20
        if uncertainty < 0.05:
            return 15
        if uncertainty >= 0.08 and market_type == "crypto":
            return 5
        return 10

    @staticmethod
    def compute_refresh_at(uncertainty: float, market_type: str):
        return utc_now() + timedelta(minutes=SynthClient.adaptive_refresh_minutes(uncertainty, market_type))

