"""Data models using dataclasses (no Pydantic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Percentiles:
    p05: float
    p20: float
    p35: float
    p50: float
    p65: float
    p80: float
    p95: float
