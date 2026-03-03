/**
 * Market data: Engine fetches crypto prices server-side (avoids browser CORS + Binance 451).
 * Dashboard no longer fetches from Binance; fetchAndPushPrices is a no-op.
 */

export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap?: number;
}

export interface SymbolInfo {
  symbol: string;
  market_type: "crypto" | "equity";
}

/**
 * No-op: Engine fetches Binance/Kraken internally. Avoids browser CORS and Binance 451.
 */
export async function fetchAndPushPrices(
  _symbols: SymbolInfo[],
  _apiBase: string
): Promise<void> {}
