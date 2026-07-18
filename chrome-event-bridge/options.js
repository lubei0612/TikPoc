const SETTINGS_KEY = "tikpocSettings";
const form = document.querySelector("#settings-form");
const status = document.querySelector("#status");
let lastConnectionTest = null;

function showStatus(message, state = "") {
  status.textContent = message;
  status.dataset.state = state;
}

function values() {
  return {
    accountId: document.querySelector("#account-id").value.trim(),
    deviceId: document.querySelector("#device-id").value.trim(),
    dashboardUrl: document.querySelector("#dashboard-url").value.trim().replace(/\/$/, ""),
    enabled: document.querySelector("#enabled").checked,
    browserFollowbackEnabled: document.querySelector("#browser-followback-enabled").checked,
    browserDmEnabled: document.querySelector("#browser-dm-enabled").checked,
    autoOpenActivity: document.querySelector("#auto-open-activity").checked,
    lastConnectionTest,
  };
}

chrome.storage.local.get([SETTINGS_KEY], (stored) => {
  const settings = stored[SETTINGS_KEY] || {};
  lastConnectionTest = settings.lastConnectionTest || null;
  document.querySelector("#account-id").value = settings.accountId || "";
  document.querySelector("#device-id").value = settings.deviceId || "";
  document.querySelector("#dashboard-url").value =
    settings.dashboardUrl || "http://127.0.0.1:8766";
  document.querySelector("#enabled").checked = Boolean(settings.enabled);
  document.querySelector("#browser-followback-enabled").checked =
    settings.browserFollowbackEnabled !== false;
  document.querySelector("#browser-dm-enabled").checked = settings.browserDmEnabled !== false;
  document.querySelector("#auto-open-activity").checked = Boolean(
    settings.autoOpenActivity,
  );
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const settings = values();
  chrome.storage.local.set({ [SETTINGS_KEY]: settings }, () => {
    showStatus("设置已保存。", "success");
  });
});

document.querySelector("#test-connection").addEventListener("click", () => {
  const settings = values();
  showStatus("正在连接...", "");
  chrome.runtime.sendMessage(
    {
      type: "TIKPOC_PING_DASHBOARD",
      dashboardUrl: settings.dashboardUrl,
    },
    (response) => {
      if (chrome.runtime.lastError || !response?.ok) {
        lastConnectionTest = { ok: false, testedAt: Date.now() };
        chrome.storage.local.set({ [SETTINGS_KEY]: values() });
        showStatus(
          response?.error || chrome.runtime.lastError?.message || "连接失败。",
          "error",
        );
        return;
      }
      lastConnectionTest = { ok: true, testedAt: Date.now() };
      chrome.storage.local.set({ [SETTINGS_KEY]: values() });
      showStatus("Dashboard 连接正常。", "success");
    },
  );
});
