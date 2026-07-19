chrome.storage.local.get(["tikpocSettings", "tikpocBindingStatus"], (stored) => {
  const settings = stored.tikpocSettings || {};
  const binding = TikPocOptionsCore.popupBinding(settings, stored.tikpocBindingStatus);
  const state = document.querySelector("#state");
  state.textContent = settings.enabled ? "运行中" : "未启用";
  state.classList.toggle("off", !settings.enabled);
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
  document.querySelector("#followback").textContent =
    settings.enabled && settings.browserFollowbackEnabled !== false ? "已启用" : "已停用";
  document.querySelector("#dm").textContent =
    settings.enabled && settings.browserDmEnabled !== false ? "已启用" : "已停用";
  const connection = settings.lastConnectionTest;
  document.querySelector("#connection").textContent = connection
    ? `${connection.ok ? "正常" : "失败"} ${new Date(connection.testedAt).toLocaleString()}`
    : "未测试";
});

document.querySelector("#open-options").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
