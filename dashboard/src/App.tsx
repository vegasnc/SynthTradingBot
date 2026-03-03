import { useEffect, useState } from "react";
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

function formatEST(ts: string): string {
  try {
    const s = String(ts).trim();
    const asUTC = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s.replace(/\.\d+$/, "") + "Z";
    return new Date(asUTC).toLocaleString("en-US", { timeZone: "America/New_York" });
  } catch {
    return ts;
  }
}

type Page = "overview" | "trades" | "settings";

const ONE_HOUR_MS = 60 * 60 * 1000;
function signalsWithinLastHour(signals: Signal[]): Signal[] {
  const cutoff = Date.now() - ONE_HOUR_MS;
  return signals.filter((s) => {
    try {
      const s2 = String(s.timestamp).trim();
      const asUTC = s2.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s2) ? s2 : s2.replace(/\.\d+$/, "") + "Z";
      return new Date(asUTC).getTime() >= cutoff;
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
    today_pnl?: number;
    total_pnl?: number;
    win_rate?: number;
    total_trades?: number;
  }>({ open: [], history: [] });
  const [orders, setOrders] = useState<any[]>([]);
  const [tradesPeriod, setTradesPeriod] = useState("day");
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

  async function loadAll() {
    const syms = await api.symbols();
    setSymbols(syms);
    if (!syms.find((s) => s.symbol === selected)) {
      setSelected(syms[0]?.symbol ?? selected);
    }
    setState(await api.state());
    setPositions(await api.positions());
    setOrders(await api.orders());
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
  }, [page, selected]);

  useEffect(() => {
    if (page === "trades") {
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
        api.orders(undefined, page === "trades" ? tradesPeriod : undefined).then(setOrders).catch(console.error);
        api.positions(page === "trades" ? tradesPeriod : undefined).then(setPositions).catch(console.error);
      }
      if (msg?.topic === "synth_api_call") {
        api.synthCalls().then(setSynthCalls).catch(() => {});
      }
    });
    const poll = setInterval(() => {
      api.state().then(setState).catch(console.error);
      api.positions(page === "trades" ? tradesPeriod : undefined).then(setPositions).catch(console.error);
      api.orders(undefined, page === "trades" ? tradesPeriod : undefined).then(setOrders).catch(console.error);
      api.signals(selected, 80).then(setSignals).catch(console.error);
      api.signals(undefined, 200).then(setOverviewSignals).catch(console.error);
      if (page === "overview") {
        api.synthCalls().then(setSynthCalls).catch(() => {});
        api.status().then(setEngineStatus).catch(() => setEngineStatus(null));
      }
    }, refreshMs);
    return () => {
      off();
      clearInterval(poll);
    };
  }, [selected, refreshMs, page, tradesPeriod]);

  const latestSignal = overviewSignals[0];
  const recentSignals = signalsWithinLastHour(overviewSignals);

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
        <button onClick={() => setPage("trades")}>Trades / Orders</button>
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
                <pre>{JSON.stringify(latestSignal, null, 2)}</pre>
              </div>
            )}
          </div>
          <div className="dashboard-signals">
            <h3>Latest Signals (last 1h)</h3>
            <div className="signals-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time (ET)</th>
                    <th>Asset</th>
                    <th>Bias</th>
                    <th>Edge</th>
                    <th>Allowed</th>
                    <th>Reasons</th>
                  </tr>
                </thead>
                <tbody>
                  {recentSignals.map((s, idx) => (
                    <tr key={idx}>
                      <td>{s.timestamp ? formatEST(s.timestamp) : "--"}</td>
                      <td>{s.symbol}</td>
                      <td>{s.bias}</td>
                      <td>{s.edge.toFixed(4)}</td>
                      <td>{String(s.allowed_to_trade)}</td>
                      <td>{s.reasons.join(", ") || "none"}</td>
                    </tr>
                  ))}
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
                    <th>Time (ET)</th>
                    <th>API</th>
                    <th>Params</th>
                  </tr>
                </thead>
                <tbody>
                  {synthCalls.slice(0, 50).map((c, idx) => (
                    <tr key={idx}>
                      <td>{c.ts ? formatEST(c.ts) : "--"}</td>
                      <td>{c.api || "--"}</td>
                      <td><code>{JSON.stringify(c.params || {})}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {page === "trades" && (
        <section className="panel">
          <h2>Trades / Orders</h2>
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
          <h3>Positions</h3>
          <div className="trades-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time (ET)</th>
                  <th>Asset</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Entry (price)</th>
                  <th>Stop (price)</th>
                  <th>TP1</th>
                  <th>TP2</th>
                  <th>Status</th>
                  <th>PnL</th>
                </tr>
              </thead>
              <tbody>
                {[...positions.open, ...positions.history].map((p, idx) => (
                  <tr key={p.position_id ?? p._id ?? idx}>
                    <td>{p.opened_at ? formatEST(p.opened_at) : "--"}</td>
                    <td>{p.symbol}</td>
                    <td>{p.side}</td>
                    <td>{Number(p.qty).toFixed(4)}</td>
                    <td>{Number(p.entry_price ?? 0).toFixed(2)}</td>
                    <td>{Number(p.stop_price ?? 0).toFixed(2)}</td>
                    <td>{Number(p.tp1 ?? 0).toFixed(2)}</td>
                    <td>{Number(p.tp2 ?? 0).toFixed(2)}</td>
                    <td>{p.status}</td>
                    <td className={Number(p.realized_pnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>
                      {Number(p.realized_pnl ?? 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Orders</h3>
          <div className="trades-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time (ET)</th>
                  <th>Asset</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, idx) => (
                  <tr key={o.order_id ?? o._id ?? idx}>
                    <td>{o.created_at ? formatEST(o.created_at) : "--"}</td>
                    <td>{o.symbol}</td>
                    <td>{o.side}</td>
                    <td>{Number(o.qty).toFixed(4)}</td>
                    <td>{Number(o.price ?? o.fill_price ?? 0).toFixed(2)}</td>
                    <td>{o.status}</td>
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

