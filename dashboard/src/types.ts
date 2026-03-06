export type MarketType = "crypto" | "equity";

export interface SymbolInfo {
  symbol: string;
  market_type: MarketType;
}

export interface Signal {
  symbol: string;
  market_type: MarketType;
  timestamp: string;
  spot: number;
  bias: "long" | "short" | "flat";
  edge: number;
  uncertainty: number;
  allowed_to_trade: boolean;
  reasons: string[];
  levels: Record<string, number>;
  flags: Record<string, boolean | string>;
  trade_skipped_reason?: string;
}

export interface Position {
  symbol: string;
  side: "long" | "short";
  qty: number;
  entry_price: number;
  stop_price: number;
  tp1: number;
  tp2: number;
  status: "open" | "closed";
  realized_pnl: number;
  opened_at: string;
  closed_at?: string;
}

