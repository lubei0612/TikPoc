(function exposeAutoConnectCore(root, factory) {
  const bindingCore = root.TikPocBindingCore ||
    (typeof require === "function" ? require("./binding-core.js") : null);
  const optionsCore = root.TikPocOptionsCore ||
    (typeof require === "function" ? require("./options-core.js") : null);
  const api = factory(bindingCore, optionsCore);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocAutoConnectCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createAutoConnectCore(
  bindingCore,
  optionsCore,
) {
  const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8766";

  function resolveAutoBinding({ observedUsername, bindings, settings }) {
    if (settings && settings.bindingMode === "manual") {
      return { state: "manual", binding: null };
    }
    const observed = bindingCore.normalizeUsername(observedUsername);
    if (!observed) {
      return { state: "no_visible_identity", binding: null };
    }
    const matches = (Array.isArray(bindings) ? bindings : []).filter((candidate) =>
      candidate &&
      candidate.enabled === true &&
      candidate.binding_ready === true &&
      bindingCore.normalizeUsername(candidate.expected_tiktok_username) === observed
    );
    if (matches.length === 0) {
      return { state: "no_match", binding: null };
    }
    if (matches.length !== 1) {
      return { state: "ambiguous", binding: null };
    }
    return { state: "matched", binding: matches[0] };
  }

  function needsBindingUpdate(settings, binding) {
    const current = settings || {};
    return current.bindingMode !== "auto" ||
      current.accountId !== binding.account_id ||
      current.deviceId !== binding.device_id ||
      bindingCore.normalizeUsername(current.expectedTikTokUsername) !==
        bindingCore.normalizeUsername(binding.expected_tiktok_username) ||
      current.browserProfileLabel !== binding.browser_profile_label;
  }

  function autoBindingStorageUpdate(
    stored,
    binding,
    observedUsername,
    observedAt = Date.now(),
  ) {
    const current = stored.tikpocSettings || {};
    const changingAccount = Boolean(
      current.accountId && current.accountId !== binding.account_id,
    );
    const reset = changingAccount
      ? optionsCore.resetForAccount(stored, current.accountId)
      : {};
    const settings = {
      ...current,
      accountId: binding.account_id,
      deviceId: binding.device_id,
      expectedTikTokUsername: binding.expected_tiktok_username,
      browserProfileLabel: binding.browser_profile_label,
      dashboardUrl: current.dashboardUrl || DEFAULT_DASHBOARD_URL,
      enabled: current.enabled !== false,
      browserFollowbackEnabled: current.browserFollowbackEnabled === true,
      browserDmEnabled: current.browserDmEnabled === true,
      autoOpenActivity: current.autoOpenActivity === true,
      bindingMode: "auto",
    };
    return {
      ...reset,
      tikpocSettings: settings,
      tikpocBindingStatus: {
        accountId: binding.account_id,
        state: "ready",
        observedUsername: bindingCore.normalizeUsername(observedUsername),
        observedAt,
        autoConnectState: "matched",
      },
    };
  }

  return {
    autoBindingStorageUpdate,
    needsBindingUpdate,
    resolveAutoBinding,
  };
});
