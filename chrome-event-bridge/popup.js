chrome.storage.local.get(["tikpocSettings"], (stored) => {
  const settings = stored.tikpocSettings || {};
  const state = document.querySelector("#state");
  state.textContent = settings.enabled ? "运行中" : "未启用";
  state.classList.toggle("off", !settings.enabled);
  document.querySelector("#account").textContent = settings.accountId || "-";
  document.querySelector("#device").textContent = settings.deviceId || "-";
});

document.querySelector("#open-options").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
