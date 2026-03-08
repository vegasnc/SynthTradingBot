/** API client for DemoBot engine - base URL from storage */

import { getEngineUrl } from "./storage.js";

async function getBase() {
  return getEngineUrl();
}

export async function api(path) {
  const base = await getBase();
  const res = await fetch(`${base}${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json();
}

export async function apiPost(path, body) {
  const base = await getBase();
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

export function wsUrl() {
  return getEngineUrl().then((u) => {
    const ws = u.replace(/^http/, "ws");
    return `${ws}/stream`;
  });
}

export const endpoints = {
  health: () => api("/health"),
  popup: () => api("/popup"),
  symbols: () => api("/symbols"),
  state: () => api("/state"),
  signals: (symbol, limit = 100) =>
    api(`/signals?limit=${limit}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`),
  positions: (period) =>
    api(`/positions${period && period !== "all" ? `?period=${period}` : ""}`),
  orders: (symbol, period) => {
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (period && period !== "all") params.set("period", period);
    return api(`/orders${params.toString() ? `?${params}` : ""}`);
  },
  synthCalls: (limit = 50) => api(`/synth-calls?limit=${limit}`),
  candles: (symbol, timeframe, limit = 300) =>
    api(`/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  predictions: (symbol) => api(`/predictions?symbol=${encodeURIComponent(symbol)}`),
  newsToday: () => api("/news/today"),
  newsRaw: (limit = 100) => api(`/news/raw?limit=${limit}`),
  newsRefresh: () => apiPost("/news/refresh"),
  newsSummarize: () => apiPost("/news/summarize"),
  strikeLatest: () => api("/strike/latest"),
  strikeHistory: (limit = 50) => api(`/strike/history?limit=${limit}`),
  status: () => api("/status"),
  controls: (body) => apiPost("/controls", body),
  strikeRefresh: () => apiPost("/strike/refresh"),
};
