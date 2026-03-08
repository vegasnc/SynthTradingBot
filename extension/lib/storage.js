/** Chrome storage helpers for engine URL and config */

const DEFAULTS = {
  engineUrl: "http://127.0.0.1:8000",
  initialFunds: null,
  positionsPeriod: "week",
};

export async function getEngineUrl() {
  const r = await chrome.storage.local.get("engineUrl");
  return r.engineUrl || DEFAULTS.engineUrl;
}

export async function setEngineUrl(url) {
  await chrome.storage.local.set({ engineUrl: url.trim() || DEFAULTS.engineUrl });
}

export async function getInitialFunds() {
  const r = await chrome.storage.local.get("initialFunds");
  return r.initialFunds;
}

export async function setInitialFunds(val) {
  await chrome.storage.local.set({ initialFunds: val });
}

export async function getPositionsPeriod() {
  const r = await chrome.storage.local.get("positionsPeriod");
  return r.positionsPeriod || DEFAULTS.positionsPeriod;
}

export async function setPositionsPeriod(val) {
  const clean = String(val || "").trim().toLowerCase();
  const allowed = new Set(["day", "week", "month", "year", "all"]);
  await chrome.storage.local.set({ positionsPeriod: allowed.has(clean) ? clean : DEFAULTS.positionsPeriod });
}
