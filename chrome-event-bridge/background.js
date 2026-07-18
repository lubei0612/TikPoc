const REPORT_EVENT = "TIKPOC_REPORT_EVENT";
const PING_DASHBOARD = "TIKPOC_PING_DASHBOARD";
const HEALTH_ALARM = "tikpoc-browser-health";
const HEALTH_TICK = "TIKPOC_HEALTH_TICK";
const POST_ROUTES = new Map([
  [REPORT_EVENT, "/api/browser-events"],
  ["TIKPOC_DM_PLAN", "/api/browser-dm/reply-plan"],
  ["TIKPOC_DM_RESULT", "/api/browser-dm/reply-result"],
  ["TIKPOC_ACTION_CLAIM", "/api/browser-actions/claim"],
  ["TIKPOC_ACTION_RESULT", "/api/browser-actions/result"],
  ["TIKPOC_BROWSER_HEALTH", "/api/browser-health"],
]);

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

async function postLocal(dashboardUrlValue, path, payload) {
  const dashboardUrl = localDashboardUrl(dashboardUrlValue);
  if (!dashboardUrl) {
    throw new Error("Dashboard URL must use http://127.0.0.1 or http://localhost");
  }
  const response = await fetch(`${dashboardUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Dashboard returned HTTP ${response.status}`);
  }
  return body;
}

async function reportEvent(message) {
  return postLocal(message.dashboardUrl, POST_ROUTES.get(REPORT_EVENT), message.event);
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
  if (!message || (!POST_ROUTES.has(message.type) && message.type !== PING_DASHBOARD)) {
    return false;
  }
  let task;
  if (message.type === PING_DASHBOARD) {
    task = pingDashboard(message);
  } else if (message.type === REPORT_EVENT) {
    task = reportEvent(message);
  } else {
    task = postLocal(message.dashboardUrl, POST_ROUTES.get(message.type), message.body);
  }
  task
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  return true;
});

function ensureHealthAlarm() {
  if (chrome.alarms && typeof chrome.alarms.create === "function") {
    chrome.alarms.create(HEALTH_ALARM, { periodInMinutes: 1 });
  }
}

async function notifyTikTokTabs() {
  if (!chrome.tabs || typeof chrome.tabs.query !== "function") {
    return;
  }
  const tabs = await chrome.tabs.query({ url: "https://www.tiktok.com/*" });
  await Promise.allSettled(
    tabs
      .filter((tab) => Number.isInteger(tab.id))
      .map((tab) => chrome.tabs.sendMessage(tab.id, { type: HEALTH_TICK })),
  );
}

if (chrome.alarms && chrome.alarms.onAlarm) {
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm && alarm.name === HEALTH_ALARM) {
      notifyTikTokTabs().catch(() => {});
    }
  });
}

if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(ensureHealthAlarm);
}

chrome.runtime.onInstalled.addListener(({ reason }) => {
  ensureHealthAlarm();
  if (reason === "install") {
    chrome.runtime.openOptionsPage();
  }
});
