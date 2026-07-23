(function exposePopupCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocPopupCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createPopupCore() {
  function monitoringButton(settings, pending = false) {
    if (pending) {
      return { label: "处理中...", action: "pending", disabled: true };
    }
    if (settings && settings.monitoringStarted) {
      return { label: "停止监控", action: "stop", disabled: false };
    }
    return { label: "开始监控", action: "start", disabled: false };
  }

  return { monitoringButton };
});
