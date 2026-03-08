// Background service worker - keeps extension alive; can add periodic health checks
chrome.runtime.onInstalled.addListener(() => {
  console.log("DemoBot extension installed");
});
