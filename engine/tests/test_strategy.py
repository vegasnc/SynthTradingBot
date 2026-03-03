from app.strategy import build_decision, compute_position_size, tighten_stop
from app.synth_client import SynthClient


def test_percentile_parsing():
    payload = {"percentiles": {"P05": 90, "P20": 95, "P35": 98, "P50": 100, "P65": 103, "P80": 106, "P95": 110}}
    p = SynthClient.parse_percentiles(payload)
    assert p.p05 == 90
    assert p.p95 == 110


def test_trade_filter_rules():
    p = SynthClient.parse_percentiles({"percentiles": {"p05": 98, "p20": 99, "p35": 100, "p50": 101, "p65": 102, "p80": 103, "p95": 104}})
    d = build_decision(spot=100, pct=p, market_type="equity", in_cooldown=False)
    assert isinstance(d.allowed_to_trade, bool)
    assert "edge_filter_pass" in d.flags


def test_entry_stop_tp_math_long():
    p = SynthClient.parse_percentiles({"percentiles": {"p05": 90, "p20": 93, "p35": 95, "p50": 105, "p65": 110, "p80": 115, "p95": 120}})
    d = build_decision(spot=100, pct=p, market_type="crypto", in_cooldown=False)
    assert d.bias == "long"
    assert d.stop < d.entry < d.tp1 < d.tp2


def test_trailing_tighten_only():
    p = SynthClient.parse_percentiles({"percentiles": {"p05": 90, "p20": 93, "p35": 99, "p50": 105, "p65": 110, "p80": 115, "p95": 120}})
    new_stop = tighten_stop(current_stop=95, bias="long", pct=p, tp1_hit=True)
    assert new_stop >= 95
    newer_stop = tighten_stop(current_stop=new_stop, bias="long", pct=p, tp1_hit=True)
    assert newer_stop >= new_stop


def test_position_sizing():
    qty = compute_position_size(
        account_equity=100_000,
        risk_pct=0.0075,
        entry_price=100,
        stop_price=95,
        max_symbol_exposure=0.2,
    )
    assert qty > 0
    assert qty <= (100_000 * 0.2 / 100)

