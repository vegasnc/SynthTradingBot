import { createChart, ColorType, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface TradeMarker {
  ts: string;
  side: "long" | "short" | "buy" | "sell";
}

interface CandleChartProps {
  data: { ts: string; open: number; high: number; low: number; close: number }[];
  trades?: TradeMarker[];
  height?: number;
}

function toUTCTimestamp(ts: string): UTCTimestamp {
  return Math.floor(new Date(ts).getTime() / 1000) as UTCTimestamp;
}

export function CandleChart({ data, trades = [], height = 360 }: CandleChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<ReturnType<typeof createChart> | null>(null);
  const seriesRef = useRef<ReturnType<ReturnType<typeof createChart>["addCandlestickSeries"]> | null>(null);
  const lastDataLenRef = useRef(0);
  const lastDataTimeRef = useRef<UTCTimestamp | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const el = chartRef.current;
    let cancelled = false;
    let handleResize: (() => void) | null = null;

    const init = () => {
      if (cancelled || !el) return;
      const w = el.clientWidth;
      if (w <= 0) {
        requestAnimationFrame(init);
        return;
      }
      const chart = createChart(el, {
        layout: { background: { type: ColorType.Solid, color: "#fff" }, textColor: "#333" },
        grid: { vertLines: { color: "#eee" }, horzLines: { color: "#eee" } },
        width: w,
        height,
        timeScale: { timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderVisible: true },
      });

      const candleSeries = chart.addCandlestickSeries({
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderVisible: false,
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350",
      });

      chartInstanceRef.current = chart;
      seriesRef.current = candleSeries;

      const chartData = data.map((c) => ({
        time: toUTCTimestamp(c.ts),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));
      candleSeries.setData(chartData);
      lastDataLenRef.current = data.length;
      lastDataTimeRef.current = chartData.length ? (chartData[chartData.length - 1].time as UTCTimestamp) : null;
      chart.timeScale().fitContent();

      handleResize = () => {
        if (el) chart.applyOptions({ width: el.clientWidth });
      };
      window.addEventListener("resize", handleResize);
    };

    requestAnimationFrame(init);

    return () => {
      cancelled = true;
      if (handleResize) window.removeEventListener("resize", handleResize);
      chartInstanceRef.current?.remove();
      chartInstanceRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !data.length) return;

    const lastCandle = data[data.length - 1];
    const lastTime = toUTCTimestamp(lastCandle.ts);
    const prevLen = lastDataLenRef.current;
    const prevTime = lastDataTimeRef.current;

    const chartData = data.map((c) => ({
      time: toUTCTimestamp(c.ts),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    if (prevLen === 0) {
      series.setData(chartData);
    } else if (lastTime < prevTime! || data.length < prevLen) {
      series.setData(chartData);
    } else if (data.length === prevLen && prevTime === lastTime) {
      series.update({
        time: lastTime,
        open: lastCandle.open,
        high: lastCandle.high,
        low: lastCandle.low,
        close: lastCandle.close,
      });
    } else {
      series.update({
        time: lastTime,
        open: lastCandle.open,
        high: lastCandle.high,
        low: lastCandle.low,
        close: lastCandle.close,
      });
    }
    lastDataLenRef.current = data.length;
    lastDataTimeRef.current = lastTime;
  }, [data]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const markers = trades
      .filter((t) => t.side === "long" || t.side === "short" || t.side === "buy" || t.side === "sell")
      .map((t, i) => {
        const isBuy = t.side === "long" || t.side === "buy";
        return {
          time: toUTCTimestamp(t.ts),
          position: (isBuy ? "belowBar" : "aboveBar") as "belowBar" | "aboveBar",
          shape: (isBuy ? "arrowUp" : "arrowDown") as "arrowUp" | "arrowDown",
          color: isBuy ? "#26a69a" : "#ef5350",
          id: `trade-${i}`,
        };
      });
    series.setMarkers(markers);
  }, [trades]);

  return <div ref={chartRef} style={{ width: "100%", minHeight: height }} />;
}
