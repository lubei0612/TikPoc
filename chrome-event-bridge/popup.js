let popupSettings = {};
let monitoringPending = false;

function renderMonitoring() {
  const state = TikPocPopupCore.monitoringButton(popupSettings, monitoringPending);
  const button = document.querySelector("#toggle-monitoring");
  button.textContent = state.label;
  button.disabled = state.disabled;
  button.dataset.action = state.action;
}

chrome.storage.local.get([
  "tikpocSettings",
  "tikpocBindingStatus",
  "tikpocAutoConnectStatus",
], (stored) => {
  const settings = TikPocOptionsCore.defaultSettings(stored.tikpocSettings || {});
  popupSettings = settings;
  const binding = TikPocOptionsCore.popupBinding(settings, stored.tikpocBindingStatus);
  const state = document.querySelector("#state");
  state.textContent = settings.enabled ? "运行中" : "未启用";
  state.classList.toggle("off", !settings.enabled);
  document.querySelector("#binding-mode").textContent =
    TikPocOptionsCore.bindingModeLabel(settings);
  document.querySelector("#profile").textContent = settings.browserProfileLabel || "-";
  document.querySelector("#account").textContent = settings.accountId || "-";
  document.querySelector("#device").textContent = settings.deviceId || "-";
  document.querySelector("#expected").textContent = settings.expectedTikTokUsername
    ? `@${settings.expectedTikTokUsername}`
    : "-";
  document.querySelector("#observed").textContent = binding.observedUsername
    ? `@${binding.observedUsername}`
    : "-";
  const bindingState = document.querySelector("#binding-state");
  bindingState.textContent = TikPocOptionsCore.bindingStateLabel(binding.state);
  bindingState.classList.toggle("off", binding.state !== "ready");
  const autoConnect = stored.tikpocAutoConnectStatus;
  document.querySelector("#auto-connect-state").textContent = autoConnect
    ? `${autoConnect.state}${autoConnect.observedUsername ? ` · @${autoConnect.observedUsername}` : ""}`
    : "等待页面";
  document.querySelector("#followback").textContent =
    settings.enabled && settings.browserFollowbackEnabled !== false ? "已启用" : "已停用";
  document.querySelector("#dm").textContent =
    settings.enabled && settings.browserDmEnabled !== false ? "已启用" : "已停用";
  const connection = settings.lastConnectionTest;
  document.querySelector("#connection").textContent = connection
    ? `${connection.ok ? "正常" : "失败"} ${new Date(connection.testedAt).toLocaleString()}`
    : "未测试";
  renderMonitoring();
});

document.querySelector("#toggle-monitoring").addEventListener("click", () => {
  if (monitoringPending) {
    return;
  }
  monitoringPending = true;
  renderMonitoring();
  const started = !popupSettings.monitoringStarted;
  document.querySelector("#monitoring-detail").textContent = started
    ? "正在连接服务并准备 TikTok 页面..."
    : "正在停止账号动作...";
  chrome.runtime.sendMessage(
    {
      type: "TIKPOC_SET_MONITORING",
      dashboardUrl: popupSettings.dashboardUrl || "http://127.0.0.1:8766",
      started,
    },
    (response) => {
      monitoringPending = false;
      if (chrome.runtime.lastError || !response || !response.ok) {
        document.querySelector("#monitoring-detail").textContent =
          response && response.error ||
          chrome.runtime.lastError && chrome.runtime.lastError.message ||
          "监控状态更新失败。";
        renderMonitoring();
        return;
      }
      chrome.storage.local.get(["tikpocSettings"], (stored) => {
        popupSettings = stored.tikpocSettings || popupSettings;
        document.querySelector("#monitoring-detail").textContent = started
          ? "监控已开始，缺失页面会自动恢复。"
          : "监控已停止，账号和页面配置已保留。";
        renderMonitoring();
      });
    },
  );
});

document.querySelector("#open-options").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
