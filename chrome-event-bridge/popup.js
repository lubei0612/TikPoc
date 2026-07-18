chrome.storage.local.get(["tikpocSettings"], (stored) => {
  const settings = stored.tikpocSettings || {};
  const state = document.querySelector("#state");
  state.textContent = settings.enabled ? "运行中" : "未启用";
  state.classList.toggle("off", !settings.enabled);
  document.querySelector("#account").textContent = settings.accountId || "-";
  document.querySelector("#device").textContent = settings.deviceId || "-";
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
