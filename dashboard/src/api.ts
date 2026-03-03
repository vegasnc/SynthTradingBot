import type { Position, Signal, SymbolInfo } from "./types";

export const API_BASE = "http://localhost:8000";
const BASE = API_BASE;

async function json<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => json<{ ok: boolean; trading_enabled: boolean; paper_trading: boolean }>("/health"),
  status: () =>
    json<{
      symbols: { symbol: string; has_market_data: boolean; can_produce_signals: boolean }[];
      hint: string;
    }>("/status"),
  symbols: () => json<SymbolInfo[]>("/symbols"),
  state: () =>
    json<{
      account_equity: number;
      trading_enabled: boolean;
      paper_trading: boolean;
      open_positions: number;
    }>("/state"),
  signals: (symbol?: string, limit = 100) =>
    json<Signal[]>(`/signals?limit=${limit}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`),
  predictions: (symbol: string) => json<any[]>(`/predictions?symbol=${encodeURIComponent(symbol)}`),
  positions: (period?: string) =>
    json<any>(`/positions${period && period !== "all" ? `?period=${encodeURIComponent(period)}` : ""}`),
  orders: (symbol?: string, period?: string) => {
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (period && period !== "all") params.set("period", period);
    return json<any[]>(`/orders${params.toString() ? `?${params}` : ""}`);
  },
  synthCalls: (limit = 50) => json<any[]>(`/synth-calls?limit=${limit}`),
  candles: (symbol: string, timeframe: "1m" | "5m") =>
    json<any[]>(`/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=300`),
  controls: (payload: Record<string, unknown>) =>
    fetch(`${BASE}/controls`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then((r) => r.json())
};

export function stream(onMessage: (msg: any) => void): () => void {
  const ws = new WebSocket("ws://localhost:8000/stream");
  ws.onopen = () => ws.send("subscribe");
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data));
    } catch {
      // ignore malformed frames
    }
  };
  return () => ws.close();
}

