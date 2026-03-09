/**
 * DemoBot Dashboard - Extension version.
 * Initial funds, stats, strike, news, positions, signals.
 */
import { endpoints } from "../lib/api.js";
import { stream } from "../lib/ws.js";
import { getInitialFunds, setInitialFunds, getPositionsPeriod, setPositionsPeriod } from "../lib/storage.js";

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

const initialFundsInput = document.getElementById("initialFundsInput");
const toggleEditFunds = document.getElementById("toggleEditFunds");
const statsGrid = document.getElementById("statsGrid");
const assetSpotList = document.getElementById("assetSpotList");
const newsSummaryOverview = document.getElementById("newsSummaryOverview");
const positionsCount = document.getElementById("positionsCount");
const positionsPeriod = document.getElementById("positionsPeriod");
const positionsTable = document.getElementById("positionsTable").querySelector("tbody");
const signalsTable = document.getElementById("signalsTable").querySelector("tbody");
const synthTable = document.getElementById("synthTable").querySelector("tbody");
const engineStatus = document.getElementById("engineStatus");
const headerRemainingFunds = document.getElementById("headerRemainingFunds");
const headerPnl = document.getElementById("headerPnl");
const headerTradesToday = document.getElementById("headerTradesToday");
const remainingFundsEl = document.getElementById("remainingFunds");
const navOverview = document.getElementById("navOverview");
const navNews = document.getElementById("navNews");
const pageOverview = document.getElementById("pageOverview");
const pageNews = document.getElementById("pageNews");
const newsSummaryBox = document.getElementById("newsSummaryBox");
const newsStickyNotes = document.getElementById("newsStickyNotes");
const newsAssetBias = document.getElementById("newsAssetBias");
const newsRawTable = document.getElementById("newsRawTable").querySelector("tbody");
const btnNewsRefresh = document.getElementById("btnNewsRefresh");
const btnNewsSummarize = document.getElementById("btnNewsSummarize");

function formatLocal(ts) {
  if (!ts) return "-";
  try {
    const s = String(ts).trim();
    const asUTC = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s.replace(/\.\d+$/, "") + "Z";
    return new Date(asUTC).toLocaleString();
  } catch {
    return ts;
  }
}

function formatPrice(n) {
  if (n == null || Number.isNaN(n)) return "--";
  return n >= 1 ? n.toFixed(2) : n.toFixed(6);
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

async function initPositionsPeriod() {
  try {
    const p = await getPositionsPeriod();
    if (positionsPeriod) positionsPeriod.value = p;
  } catch (_) {}
}

async function setupInitialFunds(stateEquity) {
  let val = await getInitialFunds();
  if (val == null && typeof stateEquity === "number") {
    val = stateEquity;
    await setInitialFunds(val);
  }
  if (initialFundsInput) initialFundsInput.value = val != null ? String(val) : "--";
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
initialFundsInput.addEventListener("blur", async () => {
  if (!initialFundsInput.readOnly) {
    const n = parseFloat(initialFundsInput.value);
    if (!Number.isNaN(n) && n >= 0) await setInitialFunds(n);
  }
});

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

async function loadPositions(data) {
  try {
    const posData = data ?? await endpoints.positions("all");
    const open = posData.open || [];
    const history = posData.history || posData.closed_positions || [];
    const all = [...open, ...history];
    positionsCount.textContent = `(${open.length} open, ${history.length} closed)`;
    renderExposureByAsset(open);
    const spotBy = posData.spot_by_symbol || {};
    positionsTable.innerHTML = "";
    if (all.length === 0) {
      const tr = document.createElement("tr");
      tr.className = "table-empty-row";
      tr.innerHTML = `<td colspan="14">No data</td>`;
      positionsTable.appendChild(tr);
    }
    all.forEach((p, i) => {
      const tr = document.createElement("tr");
      const isOpen = p.status === "open";
      const status = isOpen ? "Open" : "Closed";
      const spot = parseFloat(spotBy[p.symbol] || p.entry_price) || 0;
      const entry = parseFloat(p.entry_price) || 0;
      const qty = parseFloat(p.qty) || 0;
      const origQty = parseFloat(p.original_qty) ?? qty;
      const tp1 = p.tp1 != null ? parseFloat(p.tp1) : null;
      let pnl;
      let pnlDisplay;
      if (!isOpen) {
        pnl = p.realized_pnl ?? 0;
        pnlDisplay = formatPrice(pnl);
      } else if (p.tp1_closed && tp1 != null && origQty > 0) {
        const closeQty = origQty * 0.5;
        const profitTp1 = p.side === "long" ? (tp1 - entry) * closeQty : (entry - tp1) * closeQty;
        const currentPnl = p.side === "long" ? (spot - entry) * qty : (entry - spot) * qty;
        pnl = profitTp1 + currentPnl;
        const fmt = (n) => (n >= 0 ? formatPrice(n) : `-${formatPrice(Math.abs(n))}`);
        pnlDisplay = `Profit TP1 ${fmt(profitTp1)} + current ${fmt(currentPnl)} = Total ${formatPrice(pnl)}`;
      } else {
        pnl = p.side === "long" ? (spot - entry) * qty : (entry - spot) * qty;
        pnlDisplay = formatPrice(pnl);
      }
      const pnlClass = pnl >= 0 ? "pnl-pos" : "pnl-neg";
      const stop = p.stop_price ?? p.stop ?? "--";
      const tp1Val = p.tp1 != null ? (typeof p.tp1 === "number" ? formatPrice(p.tp1) : p.tp1) : "—";
      const tp2Val = p.tp2 != null ? (typeof p.tp2 === "number" ? formatPrice(p.tp2) : p.tp2) : "—";
      const partial = isOpen && p.tp1_closed ? "50%" : "—";
      const openedAt = formatLocal(p.opened_at);
      const closedAt = formatLocal(p.closed_at);
      const qtyDisplay = isOpen && (p.original_qty != null && p.original_qty !== p.qty) ? `${formatPrice(p.qty)} / ${formatPrice(p.original_qty)}` : formatPrice(p.qty);
      const rowClass = pnl >= 0 ? "row-pnl-pos" : "row-pnl-neg";
      const actionCell = isOpen && p._id
        ? `<button type="button" class="btn-close-position" data-position-id="${p._id}" title="Close this position">Close</button>`
        : "—";
      tr.className = rowClass;
      tr.innerHTML = `<td>${i + 1}</td><td>${status}</td><td>${p.symbol}</td><td>${p.side}</td><td>${qtyDisplay}</td><td>${formatPrice(p.entry_price)}</td><td>${typeof stop === "number" ? formatPrice(stop) : stop}</td><td>${tp1Val}</td><td>${tp2Val}</td><td>${partial}</td><td>${openedAt}</td><td>${closedAt}</td><td class="${pnlClass} pnl-cell">${pnlDisplay}</td><td>${actionCell}</td>`;
      positionsTable.appendChild(tr);
    });
  } catch (_) {}
}

async function loadSignals() {
  try {
    const list = await endpoints.signals(undefined, 500);
    const cutoff = Date.now() - 6 * 60 * 60 * 1000;
    const filtered = list.filter((s) => {
      try {
        const raw = String(s.timestamp || "").trim();
        if (!raw) return true;
        const asUTC = raw.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(raw) ? raw : raw.replace(/\.\d+$/, "") + "Z";
        const ts = new Date(asUTC).getTime();
        return !Number.isNaN(ts) && ts >= cutoff;
      } catch {
        return true;
      }
    });
    signalsTable.innerHTML = "";
    if (filtered.length === 0) {
      const tr = document.createElement("tr");
      tr.className = "table-empty-row";
      tr.innerHTML = `<td colspan="8">No data</td>`;
      signalsTable.appendChild(tr);
    }
    filtered.forEach((s, i) => {
      const tr = document.createElement("tr");
      const reasons = s.reasons?.join(", ") || "none";
      const skipReason = s.trade_skipped_reason || "—";
      const escapeHtml = (str) => String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
      tr.innerHTML = `<td>${i + 1}</td><td>${formatLocal(s.timestamp)}</td><td>${s.symbol}</td><td>${s.bias}</td><td>${formatPrice(s.edge)}</td><td>${s.allowed_to_trade ? "yes" : "no"}</td><td class="signal-reasons">${escapeHtml(reasons)}</td><td class="signal-skip-reason">${escapeHtml(skipReason)}</td>`;
      signalsTable.appendChild(tr);
    });
  } catch (_) {}
}

async function loadSynthCalls() {
  try {
    const list = await endpoints.synthCalls(50);
    synthTable.innerHTML = "";
    list.slice(0, 20).forEach((c, i) => {
      const tr = document.createElement("tr");
      const ts = c.ts ? formatLocal(c.ts) : "-";
      const paramsStr = c.params != null ? JSON.stringify(c.params) : "—";
      const paramsTitle = paramsStr.length > 40 ? paramsStr.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;") : "";
      tr.innerHTML = `<td>${i + 1}</td><td>${ts}</td><td>${c.api || "-"}</td><td><code class="synth-params"${paramsTitle ? ` title="${paramsTitle}"` : ""}>${paramsStr}</code></td>`;
      synthTable.appendChild(tr);
    });
  } catch (_) {}
}

async function loadNewsSummary() {
  try {
    const today = await endpoints.newsToday();
    const trunc = today?.summary ? (today.summary.length > 200 ? today.summary.slice(0, 200) + "…" : today.summary) : "No summary today. Use News tab to refresh.";
    newsSummaryOverview.textContent = trunc;
  } catch (_) {
    newsSummaryOverview.textContent = "—";
  }
}

async function loadNews() {
  try {
    const [today, raw] = await Promise.all([
      endpoints.newsToday(),
      endpoints.newsRaw(100),
    ]);
    newsSummaryBox.innerHTML = today?.summary
      ? `<h4>Today's Summary</h4><p>${today.summary}</p>`
      : "<p>No summary today. Click Scrape & Summarize to fetch news.</p>";
    if (today?.sticky_notes?.length) {
      newsStickyNotes.innerHTML = today.sticky_notes
        .map((n) => `<div class="sticky-note"><h4>${n.title || ""}</h4><p>${n.text || ""}</p></div>`)
        .join("");
      newsStickyNotes.style.display = "grid";
    } else {
      newsStickyNotes.innerHTML = "";
      newsStickyNotes.style.display = "none";
    }
    if (today?.asset_bias && Object.keys(today.asset_bias).length) {
      newsAssetBias.innerHTML = `<h4>Asset Bias</h4><div class="asset-bias-grid">${Object.entries(today.asset_bias)
        .map(([a, b]) => `<span class="bias-${String(b).toLowerCase()}">${a}: ${b}</span>`)
        .join("")}</div>`;
      newsAssetBias.style.display = "block";
    } else {
      newsAssetBias.innerHTML = "";
      newsAssetBias.style.display = "none";
    }
    newsRawTable.innerHTML = "";
    const rawList = raw || [];
    if (rawList.length === 0) {
      const tr = document.createElement("tr");
      tr.className = "table-empty-row";
      tr.innerHTML = `<td colspan="4">No data</td>`;
      newsRawTable.appendChild(tr);
    }
    for (let i = 0; i < rawList.length; i++) {
      const r = rawList[i];
      const tr = document.createElement("tr");
      const title = r.url ? `<a class="news-title-link" href="${r.url}" target="_blank" rel="noreferrer">${r.title || ""}</a>` : (r.title || "");
      const snippet = (r.snippet || "").length > 120 ? `${String(r.snippet).slice(0, 120)}…` : (r.snippet || "");
      tr.innerHTML = `<td>${i + 1}</td><td>${title}</td><td>${r.source || ""}</td><td>${snippet}</td>`;
      newsRawTable.appendChild(tr);
    }
  } catch (e) {
    newsSummaryBox.innerHTML = "<p>Failed to load news.</p>";
  }
}

function setPage(page) {
  const isNews = page === "news";
  navOverview.classList.toggle("active", !isNews);
  navNews.classList.toggle("active", isNews);
  pageOverview.style.display = isNews ? "none" : "block";
  pageNews.style.display = isNews ? "block" : "none";
  const url = new URL(window.location.href);
  url.searchParams.set("page", page);
  window.history.replaceState({}, "", url);
  if (isNews) loadNews();
}

async function refresh() {
  engineStatus.textContent = "Loading...";
  try {
    const period = positionsPeriod?.value || "week";
    const [health, state, popupData] = await Promise.all([
      endpoints.health(),
      endpoints.state(),
      endpoints.popup().catch(() => null),
    ]);
    const positionsFromApi = await endpoints.positions(period).catch(() => ({ open: [], history: [], spot_by_symbol: {} }));
    const positionsData = {
      ...positionsFromApi,
      spot_by_symbol: popupData?.spot_by_symbol || positionsFromApi?.spot_by_symbol || {},
    };
    await Promise.all([
      loadPositions(positionsData),
      loadSignals(),
      loadSynthCalls(),
      loadNewsSummary(),
    ]);
    engineStatus.textContent = health?.ok ? "Connected" : "Engine offline";
    engineStatus.classList.toggle("connected", !!health?.ok);
    const stats = popupData?.stats ?? (positionsData?.today_pnl != null ? positionsData : null);
    renderStats(stats);
    renderAssetSpot(popupData?.strike?.allocations ?? {}, popupData?.spot_by_symbol ?? positionsData?.spot_by_symbol ?? {});
    await setupInitialFunds(state?.account_equity);
    const openCost = positionsData?.open_positions_cost ?? popupData?.stats?.open_positions_cost ?? 0;
    const initial = parseFloat(initialFundsInput?.value) || 0;
    const remaining = Math.max(0, initial - openCost);
    if (remainingFundsEl) remainingFundsEl.textContent = `$${remaining.toFixed(2)}`;
    if (headerRemainingFunds) headerRemainingFunds.textContent = `$${remaining.toFixed(2)}`;
    if (headerPnl) {
      const totalPnl = stats?.total_pnl;
      const cls = typeof totalPnl === "number" && totalPnl >= 0 ? "kpi-pnl-pos" : "kpi-pnl-neg";
      headerPnl.textContent = typeof totalPnl === "number" ? (totalPnl >= 0 ? `+$${totalPnl.toFixed(2)}` : `$${totalPnl.toFixed(2)}`) : "—";
      headerPnl.className = "kpi-pnl " + cls;
    }
    if (headerTradesToday) headerTradesToday.textContent = stats?.today_trades != null ? `${stats.today_trades}` : "—";
  } catch {
    engineStatus.textContent = "Engine offline";
    engineStatus.classList.remove("connected");
    renderStats(null);
    renderAssetSpot({}, {});
    setupInitialFunds(null);
    if (remainingFundsEl) remainingFundsEl.textContent = "—";
    if (headerRemainingFunds) headerRemainingFunds.textContent = "—";
    if (headerPnl) { headerPnl.textContent = "—"; headerPnl.className = "kpi-pnl"; }
    if (headerTradesToday) headerTradesToday.textContent = "—";
    try {
      const fallback = await endpoints.popup().catch(() => endpoints.positions("all"));
      const posData = fallback?.ok ? { open: fallback.open_positions || [], history: fallback.closed_positions || [], spot_by_symbol: fallback.spot_by_symbol || {}, open_positions_cost: fallback.stats?.open_positions_cost, ...fallback.stats } : fallback;
      await Promise.all([
        loadPositions(posData ?? undefined),
        loadSignals(),
        loadSynthCalls(),
        loadNewsSummary(),
      ]);
      const stats = fallback?.stats ?? (fallback?.today_pnl != null ? fallback : null);
      if (stats) renderStats(stats);
      const spotBy = fallback?.spot_by_symbol ?? fallback?.spot_by_symbol ?? {};
      renderAssetSpot(fallback?.strike?.allocations ?? {}, spotBy);
      const openCost = posData?.open_positions_cost ?? 0;
      const initial = parseFloat(initialFundsInput?.value) || 0;
      const remaining = Math.max(0, initial - openCost);
      if (remainingFundsEl) remainingFundsEl.textContent = `$${remaining.toFixed(2)}`;
      if (headerRemainingFunds) headerRemainingFunds.textContent = `$${remaining.toFixed(2)}`;
      if (headerPnl && stats) {
        const totalPnl = stats.total_pnl;
        const cls = typeof totalPnl === "number" && totalPnl >= 0 ? "kpi-pnl-pos" : "kpi-pnl-neg";
        headerPnl.textContent = typeof totalPnl === "number" ? (totalPnl >= 0 ? `+$${totalPnl.toFixed(2)}` : `$${totalPnl.toFixed(2)}`) : "—";
        headerPnl.className = "kpi-pnl " + cls;
      }
      if (headerTradesToday && stats) headerTradesToday.textContent = stats.today_trades != null ? `${stats.today_trades}` : "—";
    } catch (_) {}
  }
}

const urlPage = new URLSearchParams(window.location.search).get("page");
if (urlPage === "news") setPage("news");
else setPage("overview");

navOverview.addEventListener("click", () => setPage("overview"));
navNews.addEventListener("click", () => setPage("news"));

positionsPeriod?.addEventListener("change", async () => {
  try {
    await setPositionsPeriod(positionsPeriod.value);
  } catch (_) {}
  refresh();
});

document.getElementById("positionsTable")?.addEventListener("click", async (e) => {
  const btn = e.target.closest(".btn-close-position");
  if (!btn || btn.disabled) return;
  const id = btn.getAttribute("data-position-id");
  if (!id) return;
  btn.disabled = true;
  btn.textContent = "Closing…";
  try {
    const data = await endpoints.controls({ close_position_id: id });
    if (data && data.ok) {
      await refresh();
    } else {
      alert(data?.detail || "Close failed. Ensure market data is connected and try again.");
    }
  } catch (err) {
    alert(err?.message || "Close position failed.");
    console.error("Close position failed:", err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Close";
  }
});

btnNewsRefresh.addEventListener("click", async () => {
  btnNewsRefresh.disabled = true;
  try {
    await endpoints.newsRefresh();
    await loadNews();
  } finally {
    btnNewsRefresh.disabled = false;
  }
});
btnNewsSummarize.addEventListener("click", async () => {
  btnNewsSummarize.disabled = true;
  try {
    await endpoints.newsSummarize();
    await loadNews();
  } finally {
    btnNewsSummarize.disabled = false;
  }
});

await initPositionsPeriod();
refresh();
const unsub = stream((msg) => {
  if (msg && (msg.type === "signal" || msg.type === "position_opened" || msg.type === "position_closed")) {
    refresh();
  }
});
setInterval(refresh, 5000);
