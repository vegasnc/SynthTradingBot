import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { api, API_BASE, stream } from "./api";
import { BinanceChart } from "./BinanceChart";
import { fetchAndPushPrices } from "./marketData";
import type { Position, Signal, SymbolInfo } from "./types";
import "./styles.css";

const REFRESH_OPTIONS: { label: string; ms: number }[] = [
  { label: "1s", ms: 1000 },
  { label: "5s", ms: 5000 },
  { label: "1m", ms: 60_000 },
  { label: "5m", ms: 300_000 },
  { label: "10m", ms: 600_000 },
  { label: "15m", ms: 900_000 },
  { label: "30m", ms: 1_800_000 },
  { label: "1h", ms: 3_600_000 },
  { label: "1d", ms: 86_400_000 },
  { label: "1 week", ms: 604_800_000 },
  { label: "1 month", ms: 2_592_000_000 },
  { label: "1 year", ms: 31_536_000_000 },
];

function formatLocal(ts: string): string {
  try {
    const s = String(ts).trim();
    const asUTC = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s.replace(/\.\d+$/, "") + "Z";
    return new Date(asUTC).toLocaleString();
  } catch {
    return ts;
  }
}

function formatPrice(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "--";
  return n >= 1 ? n.toFixed(2) : n.toFixed(6);
}

function defaultUncThreshold(marketType: string): string {
  return marketType === "equity" ? "≤0.05" : "≤0.08";
}

type Page = "overview" | "news" | "strike" | "settings";

const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
function signalsWithinWindow(signals: Signal[], windowMs = SIX_HOURS_MS): Signal[] {
  const cutoff = Date.now() - windowMs;
  return signals.filter((s) => {
    try {
      const raw = s.timestamp;
      if (raw == null) return true;
      const s2 = String(raw).trim();
      if (!s2) return true;
      let asUTC = s2;
      if (!s2.endsWith("Z") && !/[+-]\d{2}:?\d{2}$/.test(s2)) {
        asUTC = s2.replace(/\.\d{3,}$/, "").replace(/\.\d+$/, "") + "Z";
      }
      const ts = new Date(asUTC).getTime();
      if (Number.isNaN(ts)) return true;
      return ts >= cutoff;
    } catch {
      return true;
    }
  });
}

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [selected, setSelected] = useState("BTC-USD");
  const [state, setState] = useState<any>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [overviewSignals, setOverviewSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<{
    open: Position[];
    history: Position[];
    spot_by_symbol?: Record<string, number>;
    today_pnl?: number;
    total_pnl?: number;
    win_rate?: number;
    total_trades?: number;
  }>({ open: [], history: [] });
  const [orders, setOrders] = useState<any[]>([]);
  const [tradesPeriod, setTradesPeriod] = useState("day");
  const [closingPositionId, setClosingPositionId] = useState<string | null>(null);
  const [synthCalls, setSynthCalls] = useState<{ ts: string; api: string; params: Record<string, unknown> }[]>([]);
  const [predictions, setPredictions] = useState<any[]>([]);
  const [candles1m, setCandles1m] = useState<any[]>([]);
  const [candles5m, setCandles5m] = useState<any[]>([]);
  const [wsLive, setWsLive] = useState(false);
  const [allowLive, setAllowLive] = useState(false);
  const [refreshMs, setRefreshMs] = useState(5000);
  const [engineStatus, setEngineStatus] = useState<{
    symbols: { symbol: string; has_market_data: boolean; can_produce_signals: boolean }[];
    hint: string;
  } | null>(null);
  const [newsToday, setNewsToday] = useState<{
    date: string;
    summary: string;
    sticky_notes: { title: string; text: string }[];
    asset_bias: Record<string, string>;
    created_at?: string;
  } | null>(null);
  const [newsRaw, setNewsRaw] = useState<any[]>([]);
  const [strikeLatest, setStrikeLatest] = useState<{
    allocations: Record<string, { weight: number; confidence: number; bias: string; edge: number; uncertainty: number }>;
    timestamp: string | null;
    horizon?: string;
  } | null>(null);

  async function loadAll() {
    const syms = await api.symbols();
    setSymbols(syms);
    if (!syms.find((s) => s.symbol === selected)) {
      setSelected(syms[0]?.symbol ?? selected);
    }
    setState(await api.state());
    setPositions(await api.positions(tradesPeriod));
    setOrders(await api.orders(undefined, tradesPeriod));
    setSynthCalls(await api.synthCalls());
  }

  async function loadTradesData(period: string) {
    setPositions(await api.positions(period));
    setOrders(await api.orders(undefined, period));
  }

  async function loadSymbolData(symbol: string) {
    setSignals(await api.signals(symbol, 80));
    setPredictions(await api.predictions(symbol));
    setCandles1m(await api.candles(symbol, "1m"));
    setCandles5m(await api.candles(symbol, "5m"));
  }

  useEffect(() => {
    loadAll().catch(console.error);
    api.signals(undefined, 200).then(setOverviewSignals).catch(console.error);
  }, []);

  useEffect(() => {
    if (symbols.length > 0) return;
    const id = setInterval(() => loadAll().catch(console.error), 5000);
    return () => clearInterval(id);
  }, [symbols.length]);

  useEffect(() => {
    if (!symbols.length) return;
    const push = () => fetchAndPushPrices(symbols, API_BASE).catch(console.warn);
    push();
    const id = setInterval(push, 15000);
    return () => clearInterval(id);
  }, [symbols]);

  useEffect(() => {
    loadSymbolData(selected).catch(console.error);
  }, [selected]);

  useEffect(() => {
    if (page === "overview") {
      loadSymbolData(selected).catch(console.error);
      api.signals(undefined, 200).then(setOverviewSignals).catch(console.error);
      api.status().then(setEngineStatus).catch(() => setEngineStatus(null));
    }
    if (page === "news") {
      api.newsToday().then(setNewsToday).catch(() => setNewsToday(null));
      api.newsRaw(100).then(setNewsRaw).catch(() => setNewsRaw([]));
    }
    if (page === "strike") {
      api.strikeLatest().then(setStrikeLatest).catch(() => setStrikeLatest(null));
    }
  }, [page, selected]);

  useEffect(() => {
    if (page === "overview") {
      loadTradesData(tradesPeriod).catch(console.error);
    }
  }, [page, tradesPeriod]);

  useEffect(() => {
    const off = stream((msg) => {
      setWsLive(true);
      if (msg?.topic === "signal") {
        const payload = msg.payload;
        if (payload?.symbol === selected) {
          setSignals((prev) => [payload, ...prev].slice(0, 100));
        }
        setOverviewSignals((prev) => [payload, ...prev.filter((s) => !(s.symbol === payload?.symbol && s.timestamp === payload?.timestamp))].slice(0, 200));
      }
      if (msg?.topic === "position_opened" || msg?.topic === "position_closed") {
        api.positions().then(setPositions).catch(console.error);
      }
      if (msg?.topic === "order_created") {
        api.orders(undefined, tradesPeriod).then(setOrders).catch(console.error);
        api.positions(tradesPeriod).then(setPositions).catch(console.error);
      }
      if (msg?.topic === "synth_api_call") {
        api.synthCalls().then(setSynthCalls).catch(() => {});
      }
    });
    const poll = setInterval(() => {
      api.state().then(setState).catch(console.error);
      api.positions(tradesPeriod).then(setPositions).catch(console.error);
      api.orders(undefined, tradesPeriod).then(setOrders).catch(console.error);
      api.signals(selected, 80).then(setSignals).catch(console.error);
      api.signals(undefined, 200).then(setOverviewSignals).catch(console.error);
      if (page === "overview") {
        api.synthCalls().then(setSynthCalls).catch(() => {});
        api.status().then(setEngineStatus).catch(() => setEngineStatus(null));
      }
      if (page === "news") {
        api.newsToday().then(setNewsToday).catch(() => setNewsToday(null));
        api.newsRaw(100).then(setNewsRaw).catch(() => setNewsRaw([]));
      }
      if (page === "strike") {
        api.strikeLatest().then(setStrikeLatest).catch(() => setStrikeLatest(null));
      }
    }, refreshMs);
    return () => {
      off();
      clearInterval(poll);
    };
  }, [selected, refreshMs, page, tradesPeriod]);

  const latestSignal = overviewSignals[0];
  const recentSignals = signalsWithinWindow(overviewSignals);
  const signalsToShow =
    recentSignals.length > 0 ? recentSignals : overviewSignals.slice(0, 20);

  async function toggleTrading(enable: boolean) {
    await api.controls({ enable_trading: enable });
    setState(await api.state());
  }

  async function applyPaperLive() {
    if (!allowLive) return;
    await api.controls({ paper_trading: !allowLive });
    setState(await api.state());
  }

  return (
    <div className="app">
      <header>
        <h1>Synth Trading Dashboard</h1>
        <div className="meta">
          <span>WS: {wsLive ? "live" : "polling fallback"}</span>
          <span>Equity: {state?.account_equity?.toFixed?.(2) ?? "--"}</span>
          <label>
            Refresh:{" "}
            <select value={refreshMs} onChange={(e) => setRefreshMs(Number(e.target.value))}>
              {REFRESH_OPTIONS.map((o) => (
                <option key={o.ms} value={o.ms}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => toggleTrading(!(state?.trading_enabled ?? true))}>
            Trading: {state?.trading_enabled ? "ON" : "OFF"}
          </button>
        </div>
      </header>

      <nav>
        <button onClick={() => setPage("overview")}>Dashboard</button>
        <button onClick={() => setPage("news")}>News</button>
        <button onClick={() => setPage("strike")}>Strike</button>
        <button onClick={() => setPage("settings")}>Settings</button>
      </nav>

      {page === "overview" && (
        <section className="panel">
          <h2>Dashboard</h2>
          {engineStatus && (
            <div
              className="panel"
              style={{
                marginBottom: 12,
                padding: 8,
                background: engineStatus.symbols.some((s) => s.can_produce_signals)
                  ? "#e8f5e9"
                  : "#fff3e0",
                fontSize: 13,
              }}
            >
              {engineStatus.symbols.some((s) => s.can_produce_signals) ? (
                <span>Engine OK: {engineStatus.symbols.filter((s) => s.can_produce_signals).map((s) => s.symbol).join(", ")} have market data.</span>
              ) : (
                <span>
                  No signals yet: {engineStatus.hint} Check engine logs for &quot;no market data&quot; or &quot;market data stale&quot;.
                </span>
              )}
            </div>
          )}
          <div className="grid">
            <div>Open positions: {positions.open.length}</div>
            <div>Closed positions: {positions.history.length}</div>
            <div>Last signal: {latestSignal?.bias ?? "n/a"}</div>
            <div>Win-rate: {computeWinRate(positions.history)}%</div>
            <div>
              Market:{" "}
              <span
                className={
                  (latestSignal?.flags?.market_direction as string) === "bullish"
                    ? "market-bullish"
                    : (latestSignal?.flags?.market_direction as string) === "bearish"
                      ? "market-bearish"
                      : "market-neutral"
                }
              >
                {(latestSignal?.flags?.market_direction as string) || "—"}
              </span>
            </div>
          </div>
          <div className="controls">
            <label>
              Asset
              <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                {symbols.map((s) => (
                  <option key={s.symbol} value={s.symbol}>
                    {s.symbol} ({s.market_type})
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="dashboard-chart-row">
            <div className="chart">
              <BinanceChart symbol={selected} height={400} key={selected} />
            </div>
            {latestSignal && (
              <div className="decision">
                <h3>Current Decision</h3>
                <div className="market-status-badge">
                  Market:{" "}
                  <span
                    className={
                      (latestSignal.flags?.market_direction as string) === "bullish"
                        ? "market-bullish"
                        : (latestSignal.flags?.market_direction as string) === "bearish"
                          ? "market-bearish"
                          : "market-neutral"
                    }
                  >
                    {(latestSignal.flags?.market_direction as string) || "neutral"}
                  </span>
                  {(latestSignal.flags?.direction_aligned as boolean) === false && (
                    <span className="counter-trend-label"> (counter-trend)</span>
                  )}
                </div>
                <pre>{JSON.stringify(latestSignal, null, 2)}</pre>
              </div>
            )}
          </div>
          <div className="dashboard-trades">
            <h3>Positions & Orders</h3>
            <div className="trades-controls">
              <label>
                Date filter:{" "}
                <select value={tradesPeriod} onChange={(e) => setTradesPeriod(e.target.value)}>
                  <option value="day">Today</option>
                  <option value="week">Week</option>
                  <option value="month">Month</option>
                  <option value="year">Year</option>
                  <option value="all">All</option>
                </select>
              </label>
              <div className="trades-pnl-stats">
                <span className={typeof positions.today_pnl === "number" && positions.today_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                  Today P/L: {typeof positions.today_pnl === "number" ? positions.today_pnl.toFixed(2) : "--"}
                </span>
                <span className={typeof positions.total_pnl === "number" && positions.total_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                  Total P/L: {typeof positions.total_pnl === "number" ? positions.total_pnl.toFixed(2) : "--"}
                </span>
                <span>Win rate: {typeof positions.win_rate === "number" ? positions.win_rate.toFixed(1) : "--"}%</span>
                <span>Total trades: {positions.total_trades ?? "--"}</span>
                <span>Avg win: {(positions as any).avg_win != null ? Number((positions as any).avg_win).toFixed(2) : "--"}</span>
                <span>Avg loss: {(positions as any).avg_loss != null ? Number((positions as any).avg_loss).toFixed(2) : "--"}</span>
                <span>Profit factor: {(positions as any).profit_factor != null ? Number((positions as any).profit_factor).toFixed(2) : "--"}</span>
              </div>
            </div>
            <h4>Positions</h4>
            <div className="trades-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>No</th>
                    <th>Time (ET)</th>
                    <th>Asset</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>Stop</th>
                    <th>TP</th>
                    <th>Status</th>
                    <th>PnL</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.open.length + positions.history.length === 0 ? (
                    <tr className="table-empty-row">
                      <td colSpan={11}>No data</td>
                    </tr>
                  ) : (
                  [...positions.open, ...positions.history].map((p, idx) => {
                    const isOpen = p.status === "open";
                    const spot = positions.spot_by_symbol?.[p.symbol];
                    const unrealized =
                      isOpen && spot != null
                        ? (p.side === "long" ? (spot - (p.entry_price ?? 0)) : ((p.entry_price ?? 0) - spot)) *
                          (p.qty ?? 0)
                        : null;
                    const displayPnl = isOpen && unrealized != null ? unrealized : Number(p.realized_pnl ?? 0);
                    const pnlLabel = isOpen && unrealized != null ? "unreal." : "realized";
                    const posId = (p as any)._id;
                    const isClosing = posId && closingPositionId === posId;
                    const handleClose = async () => {
                      if (!posId || isClosing) return;
                      setClosingPositionId(posId);
                      try {
                        const data = await api.controls({ close_position_id: posId });
                        if (data && (data as { ok?: boolean }).ok) {
                          setPositions(await api.positions(tradesPeriod));
                        } else {
                          alert((data as { detail?: string })?.detail || "Close failed. Ensure market data is connected and try again.");
                        }
                      } catch (e) {
                        console.error("Close position failed:", e);
                        alert("Close position failed.");
                      } finally {
                        setClosingPositionId(null);
                      }
                    };
                    return (
                      <tr
                        key={p.position_id ?? posId ?? idx}
                        className={displayPnl >= 0 ? "row-pnl-pos" : "row-pnl-neg"}
                      >
                        <td>{idx + 1}</td>
                        <td>{p.opened_at ? formatLocal(p.opened_at) : "--"}</td>
                        <td>{p.symbol}</td>
                        <td>{p.side}</td>
                        <td>{Number(p.qty).toFixed(4)}</td>
                        <td>{Number(p.entry_price ?? 0).toFixed(2)}</td>
                        <td>{Number(p.stop_price ?? 0).toFixed(2)}</td>
                        <td>{Number(p.tp ?? p.tp1 ?? p.tp2 ?? 0).toFixed(2)}</td>
                        <td>{p.status}</td>
                        <td className={displayPnl >= 0 ? "pnl-pos" : "pnl-neg"} title={pnlLabel}>
                          {displayPnl.toFixed(2)}
                          {isOpen && unrealized != null && (
                            <span className="pnl-sublabel"> (u)</span>
                          )}
                        </td>
                        <td>
                          {isOpen && posId ? (
                            <button
                              type="button"
                              className="btn-close-position"
                              onClick={handleClose}
                              disabled={isClosing}
                              title="Close this position"
                            >
                              {isClosing ? "Closing…" : "Close"}
                            </button>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })
                  )}
                </tbody>
              </table>
            </div>
            <h4>Orders</h4>
            <div className="trades-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>No</th>
                    <th>Time (ET)</th>
                    <th>Asset</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Price</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.length === 0 ? (
                    <tr className="table-empty-row">
                      <td colSpan={7}>No data</td>
                    </tr>
                  ) : (
                  orders.map((o, idx) => (
                    <tr key={(o as any).order_id ?? (o as any)._id ?? idx}>
                      <td>{idx + 1}</td>
                      <td>{(o as any).created_at ? formatLocal((o as any).created_at) : "--"}</td>
                      <td>{(o as any).symbol}</td>
                      <td>{(o as any).side}</td>
                      <td>{Number((o as any).qty).toFixed(4)}</td>
                      <td>{Number((o as any).price ?? (o as any).fill_price ?? 0).toFixed(2)}</td>
                      <td>{(o as any).status}</td>
                    </tr>
                  ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="dashboard-signals">
            <h3>Latest Signals (last 6h)</h3>
            <div className="signals-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>No</th>
                    <th>Time (ET)</th>
                    <th>Asset</th>
                    <th>Market</th>
                    <th>Spot</th>
                    <th>Entry</th>
                    <th>SL</th>
                    <th>TP1</th>
                    <th>TP2</th>
                    <th>Bias</th>
                    <th>Edge</th>
                    <th>Unc.</th>
                    <th>Threshold</th>
                    <th>Allowed</th>
                    <th>Reasons</th>
                    <th>Skip Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {signalsToShow.length === 0 ? (
                    <tr className="table-empty-row">
                      <td colSpan={15}>No data</td>
                    </tr>
                  ) : (
                  signalsToShow.map((s, idx) => (
                    <tr
                      key={idx}
                      className={s.allowed_to_trade ? "row-signal-allowed" : "row-signal-disallowed"}
                    >
                      <td>{idx + 1}</td>
                      <td>{s.timestamp ? formatLocal(s.timestamp) : "--"}</td>
                      <td>{s.symbol}</td>
                      <td>
                        <span
                          className={
                            (s.flags?.market_direction as string) === "bullish"
                              ? "market-bullish"
                              : (s.flags?.market_direction as string) === "bearish"
                                ? "market-bearish"
                                : "market-neutral"
                          }
                        >
                          {(s.flags?.market_direction as string) || "—"}
                        </span>
                      </td>
                      <td>{formatPrice(s.spot)}</td>
                      <td>{formatPrice(s.levels?.entry)}</td>
                      <td>{formatPrice(s.levels?.stop)}</td>
                      <td>{formatPrice(s.levels?.tp1)}</td>
                      <td>{formatPrice(s.levels?.tp2)}</td>
                      <td>{s.bias}</td>
                      <td>{typeof s.edge === "number" ? s.edge.toFixed(4) : "--"}</td>
                      <td>{typeof s.uncertainty === "number" ? s.uncertainty.toFixed(4) : "--"}</td>
                      <td>{defaultUncThreshold(s.market_type || "crypto")}</td>
                      <td>{String(s.allowed_to_trade)}</td>
                      <td>{s.reasons?.join(", ") || "none"}</td>
                      <td title={s.trade_skipped_reason}>{s.trade_skipped_reason || "—"}</td>
                    </tr>
                  ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="dashboard-synth-calls">
            <h3>Synth API Calls (today)</h3>
            <div className="synth-calls-wrap">
              <table>
                <thead>
                  <tr>
                    <th>No</th>
                    <th>Time (ET)</th>
                    <th>API</th>
                    <th>Params</th>
                  </tr>
                </thead>
                <tbody>
                  {synthCalls.length === 0 ? (
                    <tr className="table-empty-row">
                      <td colSpan={4}>No data</td>
                    </tr>
                  ) : (
                  synthCalls.slice(0, 50).map((c, idx) => (
                    <tr key={idx}>
                      <td>{idx + 1}</td>
                      <td>{c.ts ? formatLocal(c.ts) : "--"}</td>
                      <td>{c.api || "--"}</td>
                      <td><code>{JSON.stringify(c.params || {})}</code></td>
                    </tr>
                  ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {page === "news" && (
        <section className="panel">
          <h2>News</h2>
          <div style={{ marginBottom: 12 }}>
            <button
              onClick={() =>
                api.newsRefresh().then(() => {
                  api.newsToday().then(setNewsToday);
                  api.newsRaw(100).then(setNewsRaw);
                })
              }
            >
              Scrape & Summarize
            </button>
            <button
              style={{ marginLeft: 8 }}
              onClick={() =>
                api.newsSummarize().then(() => {
                  api.newsToday().then(setNewsToday);
                })
              }
            >
              Summarize from DB
            </button>
          </div>
          {newsToday?.summary && (
            <div className="news-summary">
              <h3>Today&apos;s Summary</h3>
              <p>{newsToday.summary}</p>
            </div>
          )}
          {newsToday?.sticky_notes && newsToday.sticky_notes.length > 0 && (
            <div className="news-sticky-notes">
              {newsToday.sticky_notes.map((note, i) => (
                <div key={i} className="sticky-note">
                  <h4>{note.title}</h4>
                  <p>{note.text}</p>
                </div>
              ))}
            </div>
          )}
          {newsToday?.asset_bias && Object.keys(newsToday.asset_bias).length > 0 && (
            <div className="news-asset-bias">
              <h3>Asset Bias</h3>
              <div className="asset-bias-grid">
                {Object.entries(newsToday.asset_bias).map(([asset, bias]) => (
                  <span key={asset} className={`bias-${bias}`}>
                    {asset}: {bias}
                  </span>
                ))}
              </div>
            </div>
          )}
          <h3>Raw News</h3>
          <div className="trades-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Title</th>
                  <th>Source</th>
                  <th>Snippet</th>
                  <th>Published</th>
                </tr>
              </thead>
              <tbody>
                {newsRaw.map((r, idx) => (
                  <tr key={idx}>
                    <td>{idx + 1}</td>
                    <td><a className="news-title-link" href={r.url} target="_blank" rel="noreferrer">{r.title}</a></td>
                    <td>{r.source}</td>
                    <td>{(r.snippet || "").length > 150 ? `${String(r.snippet).slice(0, 150)}...` : String(r.snippet || "")}</td>
                    <td>{r.published_at ? formatLocal(r.published_at) : "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {page === "strike" && (
        <section className="panel">
          <h2>Strike Portfolio Allocation</h2>
          <p>Last updated: {strikeLatest?.timestamp ? formatLocal(strikeLatest.timestamp) : "--"}</p>
          <button onClick={() => api.strikeRefresh().then(() => api.strikeLatest().then(setStrikeLatest))}>
            Refresh
          </button>
          {strikeLatest?.allocations && Object.keys(strikeLatest.allocations).length > 0 && (
            <div className="strike-chart" style={{ height: 280, marginBottom: 16 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={Object.entries(strikeLatest.allocations).map(([asset, a]) => ({
                    name: asset,
                    weight: Number((a.weight * 100).toFixed(1)),
                    fill: a.bias === "long" ? "#059669" : a.bias === "short" ? "#dc2626" : "#6b7280",
                  }))}
                >
                  <XAxis dataKey="name" />
                  <YAxis unit="%" />
                  <Tooltip formatter={(v: number) => [`${v}%`, "Weight"]} />
                  <Bar dataKey="weight">
                    {Object.entries(strikeLatest.allocations).map(([asset, a], i) => (
                      <Cell key={asset} fill={a.bias === "long" ? "#059669" : a.bias === "short" ? "#dc2626" : "#6b7280"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="strike-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Weight</th>
                  <th>Confidence</th>
                  <th>Bias</th>
                  <th>Edge</th>
                  <th>Uncertainty</th>
                </tr>
              </thead>
              <tbody>
                {strikeLatest?.allocations &&
                  Object.entries(strikeLatest.allocations).map(([asset, a]) => (
                    <tr key={asset}>
                      <td>{asset}</td>
                      <td>{(a.weight * 100).toFixed(1)}%</td>
                      <td>{a.confidence}</td>
                      <td className={`bias-${a.bias}`}>{a.bias}</td>
                      <td>{(a.edge * 100).toFixed(2)}%</td>
                      <td>{(a.uncertainty * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {page === "settings" && (
        <section className="panel">
          <h2>Settings</h2>
          <p>Paper mode is default. Live mode requires explicit confirmation.</p>
          <label className="inline">
            <input type="checkbox" checked={allowLive} onChange={(e) => setAllowLive(e.target.checked)} /> I confirm live-mode risk
          </label>
          <button onClick={applyPaperLive}>Apply Paper/Live Toggle</button>
          <h3>Tracked Symbols</h3>
          <ul>
            {symbols.map((s) => (
              <li key={s.symbol}>
                {s.symbol} - {s.market_type}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function computeWinRate(history: Position[]): string {
  if (!history.length) return "0.0";
  const wins = history.filter((h) => h.realized_pnl > 0).length;
  return ((wins / history.length) * 100).toFixed(1);
}

