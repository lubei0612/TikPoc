const REPORT_EVENT = "TIKPOC_REPORT_EVENT";
const PING_DASHBOARD = "TIKPOC_PING_DASHBOARD";

function localDashboardUrl(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return null;
  }
  const localHosts = new Set(["127.0.0.1", "localhost"]);
  if (url.protocol !== "http:" || !localHosts.has(url.hostname)) {
    return null;
  }
  return url.origin;
}

async function reportEvent(message) {
  const dashboardUrl = localDashboardUrl(message.dashboardUrl);
  if (!dashboardUrl) {
    throw new Error("Dashboard URL must use http://127.0.0.1 or http://localhost");
  }
  const response = await fetch(`${dashboardUrl}/api/browser-events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.event),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Dashboard returned HTTP ${response.status}`);
  }
  return body;
}

async function pingDashboard(message) {
  const dashboardUrl = localDashboardUrl(message.dashboardUrl);
  if (!dashboardUrl) {
    throw new Error("Dashboard URL must use http://127.0.0.1 or http://localhost");
  }
  const response = await fetch(`${dashboardUrl}/api/status`);
  if (!response.ok) {
    throw new Error(`Dashboard returned HTTP ${response.status}`);
  }
  return { ok: true };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !new Set([REPORT_EVENT, PING_DASHBOARD]).has(message.type)) {
    return false;
  }
  const task = message.type === REPORT_EVENT ? reportEvent(message) : pingDashboard(message);
  task
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  return true;
});

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === "install") {
    chrome.runtime.openOptionsPage();
  }
});
