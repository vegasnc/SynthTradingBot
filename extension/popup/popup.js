import { endpoints } from "../lib/api.js";
import { getEngineUrl, getInitialFunds, setInitialFunds, getPositionsPeriod, setPositionsPeriod } from "../lib/storage.js";

const STRIKE_ASSETS = [
  { label: "BTC", key: "BTC" },
  { label: "ETH", key: "ETH" },
  { label: "XAU", key: "XAU" },
  { label: "GOOGLX", key: "GOOGL" },
  { label: "SPYX", key: "SPY" },
  { label: "NVDAX", key: "NVDA" },
  { label: "TSLAX", key: "TSLA" },
  { label: "AAPLX", key: "AAPL" },
];

const status = document.getElementById("status");
const initialFundsInput = document.getElementById("initialFundsInput");
const toggleEditFunds = document.getElementById("toggleEditFunds");
const statsGrid = document.getElementById("statsGrid");
const assetSpotList = document.getElementById("assetSpotList");
const newsSummary = document.getElementById("newsSummary");
const newsLink = document.getElementById("newsLink");
const positionsCount = document.getElementById("positionsCount");
const positionsPeriod = document.getElementById("positionsPeriod");
const positionsList = document.getElementById("positionsList");
const remainingFundsEl = document.getElementById("remainingFunds");
const openDashboard = document.getElementById("openDashboard");

function formatPrice(n) {
  if (n == null || Number.isNaN(n)) return "--";
  return n >= 1 ? n.toFixed(2) : n.toFixed(6);
}

function formatLocal(ts) {
  if (!ts) return "-";
  try {
    const s = String(ts).trim();
    const asUTC = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s.replace(/\.\d+$/, "") + "Z";
    return new Date(asUTC).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

function renderStats(pos) {
  const todayPnl = pos?.today_pnl;
  const totalPnl = pos?.total_pnl;
  const todayPnlClass = typeof todayPnl === "number" && todayPnl >= 0 ? "pnl-pos" : "pnl-neg";
  const totalPnlClass = typeof totalPnl === "number" && totalPnl >= 0 ? "pnl-pos" : "pnl-neg";
  const todayStr = typeof todayPnl === "number" ? (todayPnl >= 0 ? `$${todayPnl.toFixed(2)}` : `-$${Math.abs(todayPnl).toFixed(2)}`) : "--";
  const totalStr = typeof totalPnl === "number" ? (totalPnl >= 0 ? `$${totalPnl.toFixed(2)}` : `-$${Math.abs(totalPnl).toFixed(2)}`) : "--";
  statsGrid.innerHTML = `
    <span>Today PnL:</span><span class="${todayPnlClass}">${todayStr}</span>
    <span>Total PnL:</span><span class="${totalPnlClass}">${totalStr}</span>
    <span>Win rate:</span><span>${typeof pos?.win_rate === "number" ? pos.win_rate.toFixed(1) : "--"}%</span>
    <span>Today trades:</span><span>${pos?.today_trades ?? "--"}</span>
    <span>Total trades:</span><span>${pos?.total_trades ?? "--"}</span>
    <span>Wins / Losses:</span><span>${pos?.wins_count ?? "--"} / ${pos?.losses_count ?? "--"}</span>
  `;
}

async function setupInitialFunds(stateEquity) {
  let val = await getInitialFunds();
  if (val == null && typeof stateEquity === "number") {
    val = stateEquity;
    await setInitialFunds(val);
  }
  initialFundsInput.value = val != null ? String(val) : "--";
}

toggleEditFunds.addEventListener("click", async () => {
  const isReadonly = initialFundsInput.readOnly;
  initialFundsInput.readOnly = !isReadonly;
  initialFundsInput.classList.toggle("editable", !isReadonly);
  toggleEditFunds.textContent = isReadonly ? "🔓" : "🔒";
  if (isReadonly) {
    const n = parseFloat(initialFundsInput.value);
    if (!Number.isNaN(n) && n >= 0) await setInitialFunds(n);
  }
});

function renderAssetSpot(strikeAllocations, spotBy) {
  const alloc = strikeAllocations || {};
  const spot = spotBy || {};
  assetSpotList.innerHTML = STRIKE_ASSETS.map(({ label, key }) => {
    const a = alloc[label] || alloc[key] || alloc[`${key}-USD`];
    const strikePct = a?.weight != null ? (a.weight * 100).toFixed(1) : "--";
    const spotPrice = spot[label] ?? spot[key] ?? spot[`${key}-USD`] ?? spot[`${label}-USD`];
    const spotStr = spotPrice != null && !Number.isNaN(spotPrice) ? formatPrice(spotPrice) : "--";
    return `<div class="asset-spot-row"><span class="asset-label">${label}</span><span class="asset-strike">${strikePct}%</span><span class="asset-spot">$${spotStr}</span></div>`;
  }).join("");
}

function renderExposureByAsset(openPositions) {
  const el = document.getElementById("exposureByAssetList");
  if (!el) return;
  const bySymbol = {};
  for (const p of openPositions || []) {
    const sym = p.symbol || "";
    if (!sym) continue;
    const cost = (parseFloat(p.entry_price) || 0) * (parseFloat(p.qty) || 0);
    bySymbol[sym] = (bySymbol[sym] || 0) + cost;
  }
  const entries = Object.entries(bySymbol).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    el.innerHTML = "<span class='exposure-empty'>No open exposure</span>";
    return;
  }
  el.innerHTML = entries.map(([sym, cost]) => `<div class="exposure-row"><span class="exposure-asset">${sym}</span><span class="exposure-amount">$${cost.toFixed(2)}</span></div>`).join("");
}

async function initPositionsPeriod() {
  try {
    const p = await getPositionsPeriod();
    if (positionsPeriod) positionsPeriod.value = p;
  } catch (_) {}
}

async function load() {
  const base = await getEngineUrl();
  try {
    const popupData = await endpoints.popup();
    if (!popupData?.ok) throw new Error("Invalid popup response");
    const period = positionsPeriod?.value || "week";
    const pos = await endpoints.positions(period).catch(() => null);
    const { stats, spot_by_symbol, strike, open_positions, closed_positions } = popupData;
    const positionsData = {
      ...stats,
      spot_by_symbol: spot_by_symbol || {},
      open: pos?.open || open_positions || [],
      closed: pos?.history || closed_positions || [],
    };
    renderAssetSpot(strike?.allocations, spot_by_symbol);
    renderStats(positionsData);
    await setupInitialFunds(null);
    const openCost = pos?.open_positions_cost ?? stats?.open_positions_cost ?? 0;
    const initial = parseFloat(initialFundsInput.value) || 0;
    const remaining = Math.max(0, initial - openCost);
    if (remainingFundsEl) remainingFundsEl.textContent = `$${remaining.toFixed(2)}`;
    status.textContent = "Engine OK";

    const open = positionsData.open || [];
    const closed = positionsData.closed || [];
    renderExposureByAsset(open);
    positionsCount.textContent = `${open.length} open, ${closed.length} closed`;
    positionsList.innerHTML = "";
    const spotBy = positionsData?.spot_by_symbol || {};
    const all = [...open.map((p) => ({ ...p, status: "open" })), ...closed.map((p) => ({ ...p, status: "closed" }))];
    for (const p of all) {
      const isOpen = p.status === "open";
      const pnl = isOpen
        ? (p.side === "long" ? (spotBy[p.symbol] || p.entry_price) - p.entry_price : p.entry_price - (spotBy[p.symbol] || p.entry_price)) * p.qty
        : (p.realized_pnl ?? 0);
      const sideClass = p.side === "long" ? "position-side-long" : "position-side-short";
      const pnlClass = pnl >= 0 ? "pnl-pos" : "pnl-neg";
      const row = document.createElement("div");
      row.className = "position-row " + (pnl >= 0 ? "row-pnl-pos" : "row-pnl-neg");
      const statusBadge = `<span class="position-status ${isOpen ? "status-open" : "status-closed"}">${isOpen ? "Open" : "Closed"}</span>`;
      const details = isOpen
        ? `qty ${formatPrice(p.qty)} · entry ${formatPrice(p.entry_price)} · stop ${formatPrice(p.stop_price)}`
        : `qty ${formatPrice(p.qty)} · entry ${formatPrice(p.entry_price)} · closed ${formatLocal(p.closed_at)}`;
      row.innerHTML = `
        <div>
          ${statusBadge}
          <span class="position-asset">${p.symbol}</span>
          <span class="${sideClass}">${p.side}</span>
        </div>
        <div class="position-details">
          ${details}
          <span class="${pnlClass}">PnL ${formatPrice(pnl)}</span>
        </div>
      `;
      positionsList.appendChild(row);
    }
    if (all.length === 0) {
      const empty = document.createElement("div");
      empty.className = "position-row table-empty-msg";
      empty.textContent = "No data";
      positionsList.appendChild(empty);
    }

    const newsData = await endpoints.newsToday().catch(() => null);
    if (newsData?.summary) {
      const trunc = newsData.summary.length > 180 ? newsData.summary.slice(0, 180) + "…" : newsData.summary;
      newsSummary.textContent = trunc;
    } else {
      newsSummary.textContent = "No summary today. Use dashboard to refresh.";
    }
    const state = await endpoints.state().catch(() => null);
    await setupInitialFunds(state?.account_equity);
  } catch (e) {
    status.classList.remove("connected");
    status.textContent = `${base}: ${e?.message || "Connect failed"}`;
    renderAssetSpot({}, {});
    renderStats(null);
    setupInitialFunds(null);
    if (remainingFundsEl) remainingFundsEl.textContent = "—";
    renderExposureByAsset([]);
    positionsCount.textContent = "—";
    positionsList.innerHTML = "<div class='position-row exposure-empty'>Connect failed</div>";
    newsSummary.textContent = "—";
  }
}

const dashboardUrl = chrome.runtime.getURL("dashboard/dashboard.html");
openDashboard.href = dashboardUrl;
openDashboard.target = "_blank";
newsLink.href = `${dashboardUrl}?page=news`;
newsLink.target = "_blank";

(async () => {
  await initPositionsPeriod();
  load();
  positionsPeriod?.addEventListener("change", async () => {
    try {
      await setPositionsPeriod(positionsPeriod.value);
    } catch (_) {}
    load();
  });
  setInterval(load, 5000);
})();
