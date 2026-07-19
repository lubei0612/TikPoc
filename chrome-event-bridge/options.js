const SETTINGS_KEY = "tikpocSettings";
const STATE_KEYS = [
  "tikpocFollowerBaselines",
  "tikpocProcessedFollowers",
  "tikpocDmBaselines",
  "tikpocDmProcessed",
  "tikpocBindingStatus",
];
const form = document.querySelector("#settings-form");
const status = document.querySelector("#status");
const bindingSelect = document.querySelector("#account-binding");
const bindingDetail = document.querySelector("#binding-detail");
const autoBindingEnabled = document.querySelector("#auto-binding-enabled");
const autoBindingDetail = document.querySelector("#auto-binding-detail");
let lastConnectionTest = null;
let currentSettings = {};
let bindings = [];
let autoConnectStatus = null;

function showStatus(message, state = "") {
  status.textContent = message;
  status.dataset.state = state;
}

function values() {
  return {
    dashboardUrl: document.querySelector("#dashboard-url").value.trim().replace(/\/$/, ""),
    enabled: document.querySelector("#enabled").checked,
    browserFollowbackEnabled: document.querySelector("#browser-followback-enabled").checked,
    browserDmEnabled: document.querySelector("#browser-dm-enabled").checked,
    autoOpenActivity: document.querySelector("#auto-open-activity").checked,
    bindingMode: autoBindingEnabled.checked ? "auto" : "manual",
    lastConnectionTest,
  };
}

function selectedBinding() {
  return bindings.find((binding) => binding.account_id === bindingSelect.value) || null;
}

function updateBindingDetail() {
  const automatic = autoBindingEnabled.checked;
  bindingSelect.disabled = automatic || bindings.length === 0;
  bindingSelect.required = !automatic;
  const observed = autoConnectStatus?.observedUsername
    ? `@${autoConnectStatus.observedUsername}`
    : "尚未观察到页面用户";
  autoBindingDetail.textContent = automatic
    ? `${observed} · ${autoConnectStatus?.state || "等待自动匹配"}`
    : "已切换为人工绑定，保存前请选择一个账号。";
  const binding = selectedBinding();
  bindingDetail.textContent = binding
    ? `设备：${binding.device_id} · TikTok：@${binding.expected_tiktok_username || "未配置"}`
    : "请选择此 Chrome Profile 唯一对应的账号。";
}

function renderBindings(accounts, selectedAccountId = "") {
  bindings = Array.isArray(accounts) ? accounts : [];
  bindingSelect.replaceChildren();
  if (bindings.length === 0) {
    bindingSelect.append(new Option("没有可用账号", ""));
    updateBindingDetail();
    return;
  }
  bindingSelect.append(new Option("请选择账号", ""));
  for (const binding of bindings) {
    const username = binding.expected_tiktok_username
      ? `@${binding.expected_tiktok_username}`
      : "未配置 TikTok 用户名";
    const option = new Option(
      `${binding.browser_profile_label || binding.account_id} · ${binding.account_id} · ${username}`,
      binding.account_id,
    );
    option.disabled = !binding.binding_ready;
    bindingSelect.append(option);
  }
  bindingSelect.value = selectedAccountId;
  updateBindingDetail();
}

function loadBindings(dashboardUrl, selectedAccountId = "") {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "TIKPOC_GET_BINDINGS", dashboardUrl },
      (response) => {
        if (chrome.runtime.lastError || !response?.ok) {
          renderBindings([], "");
          reject(new Error(
            response?.error || chrome.runtime.lastError?.message || "账号配置加载失败。",
          ));
          return;
        }
        renderBindings(response.result?.accounts, selectedAccountId);
        resolve(response.result?.accounts || []);
      },
    );
  });
}

chrome.storage.local.get([SETTINGS_KEY, "tikpocAutoConnectStatus"], (stored) => {
  const settings = stored[SETTINGS_KEY] || {};
  currentSettings = settings;
  autoConnectStatus = stored.tikpocAutoConnectStatus || null;
  lastConnectionTest = settings.lastConnectionTest || null;
  document.querySelector("#dashboard-url").value =
    settings.dashboardUrl || "http://127.0.0.1:8766";
  document.querySelector("#enabled").checked = Boolean(settings.enabled);
  document.querySelector("#browser-followback-enabled").checked =
    settings.browserFollowbackEnabled !== false;
  document.querySelector("#browser-dm-enabled").checked = settings.browserDmEnabled !== false;
  document.querySelector("#auto-open-activity").checked = Boolean(
    settings.autoOpenActivity,
  );
  autoBindingEnabled.checked = TikPocOptionsCore.bindingMode(settings) === "auto";
  updateBindingDetail();
  if (settings.lastConnectionTest?.ok) {
    loadBindings(document.querySelector("#dashboard-url").value, settings.accountId).catch(
      (error) => showStatus(error.message, "error"),
    );
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (autoBindingEnabled.checked) {
    const settings = TikPocOptionsCore.mergeRuntimeSettings(currentSettings, values());
    chrome.storage.local.set({ [SETTINGS_KEY]: settings }, () => {
      currentSettings = settings;
      showStatus("已启用可见用户名自动识别。请打开或重载 TikTok 页面。", "success");
    });
    return;
  }
  const binding = selectedBinding();
  if (!binding) {
    showStatus("请先选择此 Chrome Profile 对应的账号。", "error");
    return;
  }
  if (
    TikPocOptionsCore.requiresRebindConfirmation(
      currentSettings.accountId,
      binding.account_id,
    ) &&
    !confirm(`确认将此 Chrome Profile 从 ${currentSettings.accountId} 换绑到 ${binding.account_id}？`)
  ) {
    bindingSelect.value = currentSettings.accountId || "";
    updateBindingDetail();
    showStatus("已取消换绑。", "");
    return;
  }
  chrome.storage.local.get(STATE_KEYS, (storedState) => {
    const isRebind = TikPocOptionsCore.requiresRebindConfirmation(
      currentSettings.accountId,
      binding.account_id,
    );
    const reset = isRebind
      ? TikPocOptionsCore.resetForAccount(storedState, currentSettings.accountId)
      : {};
    const settings = TikPocOptionsCore.settingsForBinding(values(), binding);
    const unverifiedStatus = TikPocOptionsCore.bindingObservation(
      binding.account_id,
      { state: "unverified", observedUsername: "" },
    );
    chrome.storage.local.set(
      {
        ...reset,
        tikpocBindingStatus: isRebind
          ? unverifiedStatus
          : storedState.tikpocBindingStatus || unverifiedStatus,
        [SETTINGS_KEY]: settings,
      },
      () => {
        currentSettings = settings;
        showStatus("账号绑定与运行设置已保存。", "success");
      },
    );
  });
});

bindingSelect.addEventListener("change", updateBindingDetail);
autoBindingEnabled.addEventListener("change", updateBindingDetail);

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
        currentSettings = TikPocOptionsCore.mergeRuntimeSettings(currentSettings, values());
        chrome.storage.local.set({ [SETTINGS_KEY]: currentSettings });
        showStatus(
          response?.error || chrome.runtime.lastError?.message || "连接失败。",
          "error",
        );
        return;
      }
      lastConnectionTest = { ok: true, testedAt: Date.now() };
      currentSettings = TikPocOptionsCore.mergeRuntimeSettings(currentSettings, values());
      chrome.storage.local.set({ [SETTINGS_KEY]: currentSettings });
      loadBindings(settings.dashboardUrl, currentSettings.accountId)
        .then((accounts) => {
          showStatus(`连接正常，已加载 ${accounts.length} 个账号。`, "success");
        })
        .catch((error) => showStatus(error.message, "error"));
    },
  );
});
