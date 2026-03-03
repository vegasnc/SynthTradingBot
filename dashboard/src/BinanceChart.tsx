/**
 * TradingView Advanced Chart with Binance data (same as Binance's chart).
 * Uses TradingView widget - no custom chart, no jitter.
 */
import { useEffect, useRef } from "react";

function toBinanceSymbol(symbol: string): string {
  const base = symbol.replace("-USD", "").replace("-", "").toUpperCase();
  return base.endsWith("USDT") ? base : `${base}USDT`;
}

interface BinanceChartProps {
  symbol: string;
  height?: number;
}

export function BinanceChart({ symbol, height = 400 }: BinanceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const binanceSymbol = `BINANCE:${toBinanceSymbol(symbol)}`;
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: binanceSymbol,
      interval: "1",
      timezone: "America/New_York",
      theme: "light",
      style: "1",
      locale: "en",
      enable_publishing: false,
      hide_top_toolbar: false,
      save_image: true,
      support_host: "https://www.tradingview.com",
    });

    container.appendChild(script);

    return () => {
      script.remove();
      const iframe = container.querySelector("iframe");
      if (iframe) iframe.remove();
    };
  }, [symbol]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height: `${height}px`, width: "100%" }}
    >
      <div
        className="tradingview-widget-container__widget"
        style={{ height: "calc(100% - 32px)", width: "100%" }}
      />
    </div>
  );
}
