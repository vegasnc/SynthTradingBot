from app.backtest import run_backtest


def main() -> None:
    # Minimal harness; replace with Mongo-loaded datasets for realistic replay.
    candles_1m = {
        "BTC-USD": [{"close": 100, "high": 101, "low": 99}, {"close": 101, "high": 102, "low": 100}, {"close": 102, "high": 103, "low": 101}],
    }
    candles_5m = {
        "BTC-USD": [{"open": 99, "close": 100, "vwap": 99.5}, {"open": 100, "close": 102, "vwap": 100.7}],
    }
    preds = {
        "BTC-USD": [{"market_type": "crypto", "percentiles": {"p05": 95, "p20": 98, "p35": 100, "p50": 104, "p65": 106, "p80": 108, "p95": 112}}],
    }
    result = run_backtest(candles_1m, candles_5m, preds)
    print({"total_signals": result.total_signals, "tradable_signals": result.tradable_signals})


if __name__ == "__main__":
    main()
