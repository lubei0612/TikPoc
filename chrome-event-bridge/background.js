const REPORT_EVENT = "TIKPOC_REPORT_EVENT";
const PING_DASHBOARD = "TIKPOC_PING_DASHBOARD";
const GET_BINDINGS = "TIKPOC_GET_BINDINGS";
const HEALTH_ALARM = "tikpoc-browser-health";
const HEALTH_TICK = "TIKPOC_HEALTH_TICK";
const TRUSTED_SEND = "TIKPOC_TRUSTED_SEND";
const SET_MONITORING = "TIKPOC_SET_MONITORING";
const SETTINGS_KEY = "tikpocSettings";
const trustedSendQueues = new Map();
let monitoringTabsQueue = Promise.resolve();
const POST_ROUTES = new Map([
  [REPORT_EVENT, "/api/browser-events"],
  ["TIKPOC_DM_PLAN", "/api/browser-dm/reply-plan"],
  ["TIKPOC_DM_RESULT", "/api/browser-dm/reply-result"],
  ["TIKPOC_WELCOME_PLAN", "/api/browser-dm/welcome-plan"],
  ["TIKPOC_WELCOME_RESULT", "/api/browser-dm/welcome-result"],
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

async function getLocal(dashboardUrlValue, path) {
  const dashboardUrl = localDashboardUrl(dashboardUrlValue);
  if (!dashboardUrl) {
    throw new Error("Dashboard URL must use http://127.0.0.1 or http://localhost");
  }
  const response = await fetch(`${dashboardUrl}${path}`, { method: "GET" });
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

function trustedMessagesUrl(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return false;
  }
  return url.origin === "https://www.tiktok.com" &&
    (url.pathname.startsWith("/messages") ||
      url.pathname.startsWith("/business-suite/messages"));
}

async function runTrustedSend(tabId, text) {
  const target = { tabId };
  let attached = false;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown", key: "a", code: "KeyA", modifiers: 4,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "a", code: "KeyA", modifiers: 4,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown", key: "Backspace", code: "Backspace",
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "Backspace", code: "Backspace",
    });
    await chrome.debugger.sendCommand(target, "Input.insertText", { text });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    return { submitted: true };
  } finally {
    if (attached) {
      await chrome.debugger.detach(target);
    }
  }
}

function queueTrustedSend(tabId, text) {
  const previous = trustedSendQueues.get(tabId) || Promise.resolve();
  const task = previous.catch(() => {}).then(() => runTrustedSend(tabId, text));
  const tail = task.catch(() => {}).finally(() => {
    if (trustedSendQueues.get(tabId) === tail) {
      trustedSendQueues.delete(tabId);
    }
  });
  trustedSendQueues.set(tabId, tail);
  return task;
}

function trustedSend(message, sender) {
  const tabId = sender && sender.tab && sender.tab.id;
  const senderUrl = sender && (sender.url || sender.tab && sender.tab.url);
  const text = String(message && message.text || "").trim();
  if (!Number.isInteger(tabId) || !trustedMessagesUrl(senderUrl)) {
    throw new Error("Trusted send requires a TikTok Messages tab");
  }
  if (!text || text.length > 6000) {
    throw new Error("Trusted send text must contain 1 to 6000 characters");
  }
  if (!chrome.debugger || typeof chrome.debugger.attach !== "function") {
    throw new Error("Chrome debugger input is unavailable");
  }
  return queueTrustedSend(tabId, text);
}

function storageSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get([SETTINGS_KEY], (stored) => {
      resolve(stored && stored[SETTINGS_KEY] || {});
    });
  });
}

function storeSettings(settings) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ [SETTINGS_KEY]: settings }, resolve);
  });
}

function isMessagesTab(tab) {
  return trustedMessagesUrl(tab && tab.url);
}

function isTikTokObserverTab(tab) {
  let url;
  try {
    url = new URL(String(tab && tab.url || ""));
  } catch (_error) {
    return false;
  }
  return url.origin === "https://www.tiktok.com" && !isMessagesTab(tab);
}

async function ensureMonitoringTabsOnce({ refreshExisting = false } = {}) {
  if (!chrome.tabs || typeof chrome.tabs.query !== "function" ||
      typeof chrome.tabs.create !== "function") {
    throw new Error("Chrome tab management is unavailable");
  }
  const tabs = await chrome.tabs.query({ url: "https://www.tiktok.com/*" });
  const created = [];
  const observerTab = tabs.find(isTikTokObserverTab);
  const messagesTab = tabs.find(isMessagesTab);
  if (!observerTab) {
    await chrome.tabs.create({ url: "https://www.tiktok.com/" });
    created.push("https://www.tiktok.com/");
  } else if (refreshExisting && typeof chrome.tabs.reload === "function") {
    await chrome.tabs.reload(observerTab.id);
  }
  if (!messagesTab) {
    await chrome.tabs.create({ url: "https://www.tiktok.com/messages" });
    created.push("https://www.tiktok.com/messages");
  } else if (refreshExisting && typeof chrome.tabs.reload === "function") {
    await chrome.tabs.reload(messagesTab.id);
  }
  return created;
}

function ensureMonitoringTabs(options = {}) {
  const run = monitoringTabsQueue.then(
    () => ensureMonitoringTabsOnce(options),
    () => ensureMonitoringTabsOnce(options),
  );
  monitoringTabsQueue = run.catch(() => {});
  return run;
}

async function setAccountAutomation(settings, enabled) {
  const accountId = String(settings && settings.accountId || "").trim();
  if (!accountId) {
    return false;
  }
  const dashboardUrl = settings.dashboardUrl || "http://127.0.0.1:8766";
  const prefix = `monitor-${enabled ? "start" : "stop"}-${Date.now()}`;
  await postLocal(
    dashboardUrl,
    `/api/accounts/${encodeURIComponent(accountId)}/ai-enable`,
    { command_id: `${prefix}-ai`, enabled: Boolean(enabled) },
  );
  await postLocal(
    dashboardUrl,
    `/api/accounts/${encodeURIComponent(accountId)}/followback-enable`,
    { command_id: `${prefix}-followback`, enabled: Boolean(enabled) },
  );
  return true;
}

async function setMonitoring(message) {
  const started = message && message.started === true;
  const dashboardUrl = localDashboardUrl(message && message.dashboardUrl);
  if (!dashboardUrl) {
    throw new Error("Dashboard URL must use http://127.0.0.1 or http://localhost");
  }
  if (started) {
    await pingDashboard({ dashboardUrl });
  }
  const current = await storageSettings();
  const settings = {
    ...current,
    dashboardUrl,
    enabled: started,
    monitoringStarted: started,
    browserFollowbackEnabled: started,
    browserDmEnabled: started,
    autoOpenActivity: current.autoOpenActivity !== false,
    bindingMode: current.bindingMode === "manual" ? "manual" : "auto",
  };
  await storeSettings(settings);
  if (started) {
    await ensureMonitoringTabs({ refreshExisting: true });
  }
  await setAccountAutomation(settings, started);
  return { started, account_id: settings.accountId || "" };
}

async function recoverMonitoring(options = {}) {
  if (!chrome.storage || !chrome.storage.local) {
    return false;
  }
  const settings = await storageSettings();
  if (!settings.monitoringStarted) {
    return false;
  }
  await ensureMonitoringTabs(options);
  return true;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (
    !message ||
    (!POST_ROUTES.has(message.type) &&
      message.type !== PING_DASHBOARD &&
      message.type !== GET_BINDINGS &&
      message.type !== TRUSTED_SEND &&
      message.type !== SET_MONITORING)
  ) {
    return false;
  }
  let task;
  if (message.type === TRUSTED_SEND) {
    task = Promise.resolve().then(() => trustedSend(message, sender));
  } else if (message.type === SET_MONITORING) {
    task = setMonitoring(message);
  } else if (message.type === PING_DASHBOARD) {
    task = pingDashboard(message);
  } else if (message.type === GET_BINDINGS) {
    task = getLocal(message.dashboardUrl, "/api/browser-bindings");
  } else if (message.type === REPORT_EVENT) {
    task = reportEvent(message);
  } else {
    task = postLocal(message.dashboardUrl, POST_ROUTES.get(message.type), message.body);
  }
  task
    .then((result) => {
      sendResponse({ ok: true, result });
      if (
        message.type === "TIKPOC_ACTION_RESULT" &&
        message.body &&
        message.body.action_type === "followback" &&
        message.body.state === "completed"
      ) {
        notifyTikTokTabs().catch(() => {});
      }
    })
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
      recoverMonitoring().catch(() => {});
    }
  });
}

if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    ensureHealthAlarm();
    recoverMonitoring().catch(() => {});
  });
}

if (chrome.storage && chrome.storage.onChanged) {
  chrome.storage.onChanged.addListener((changes) => {
    const change = changes && changes[SETTINGS_KEY];
    const previous = change && change.oldValue || {};
    const current = change && change.newValue || {};
    if (current.monitoringStarted) {
      ensureMonitoringTabs().catch(() => {});
      if (!previous.accountId && current.accountId) {
        setAccountAutomation(current, true).catch(() => {});
      }
    }
  });
}

if (chrome.tabs && chrome.tabs.onRemoved) {
  chrome.tabs.onRemoved.addListener(() => {
    recoverMonitoring().catch(() => {});
  });
}

chrome.runtime.onInstalled.addListener(({ reason }) => {
  ensureHealthAlarm();
  if (reason === "install") {
    chrome.runtime.openOptionsPage();
  } else if (reason === "update") {
    recoverMonitoring({ refreshExisting: true }).catch(() => {});
  }
});
