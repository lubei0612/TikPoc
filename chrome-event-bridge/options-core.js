(function exposeOptionsCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocOptionsCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createOptionsCore() {
  function normalizeAccountId(value) {
    return String(value || "").trim().toLowerCase();
  }

  function bindingMode(settings) {
    return settings && settings.bindingMode === "manual" ? "manual" : "auto";
  }

  function bindingModeLabel(settings) {
    return bindingMode(settings) === "manual" ? "人工绑定" : "自动识别";
  }

  function defaultSettings(settings) {
    const current = settings || {};
    return {
      ...current,
      dashboardUrl: current.dashboardUrl || "http://127.0.0.1:8766",
      enabled: current.enabled !== false,
      browserFollowbackEnabled: current.browserFollowbackEnabled !== false,
      browserDmEnabled: current.browserDmEnabled !== false,
      autoOpenActivity: current.autoOpenActivity !== false,
      bindingMode: bindingMode(current),
      monitoringStarted: current.monitoringStarted === true,
    };
  }

  function settingsForBinding(settings, binding) {
    return {
      ...settings,
      accountId: binding.account_id,
      deviceId: binding.device_id,
      expectedTikTokUsername: binding.expected_tiktok_username,
      browserProfileLabel: binding.browser_profile_label,
    };
  }

  function mergeRuntimeSettings(settings, runtimeSettings) {
    return { ...settings, ...runtimeSettings };
  }

  function requiresRebindConfirmation(currentAccountId, nextAccountId) {
    const current = normalizeAccountId(currentAccountId);
    return Boolean(current && current !== normalizeAccountId(nextAccountId));
  }

  function bindingStateLabel(state) {
    return {
      ready: "已就绪",
      mismatch: "身份不符",
      signed_out: "已退出",
      verification_required: "需验证",
      unverified: "未验证",
    }[state] || "未验证";
  }

  function bindingObservation(accountId, result, observedAt = Date.now()) {
    return {
      accountId,
      state: result.state || "unverified",
      observedUsername: result.observedUsername || "",
      observedAt,
    };
  }

  function canObserveBinding(settings) {
    return Boolean(settings && normalizeAccountId(settings.accountId));
  }

  function shouldScheduleForStorageChanges(changes) {
    return Object.keys(changes || {}).some((key) => key !== "tikpocBindingStatus");
  }

  function omitAccountKey(records, accountId) {
    const expected = normalizeAccountId(accountId);
    return Object.fromEntries(
      Object.entries(records || {}).filter(([key]) => normalizeAccountId(key) !== expected),
    );
  }

  function resetForAccount(stored, accountId) {
    const expected = normalizeAccountId(accountId);
    const followerPrefix = `follower:${expected}:`;
    return {
      tikpocFollowerBaselines: omitAccountKey(stored.tikpocFollowerBaselines, accountId),
      tikpocProcessedFollowers: Object.fromEntries(
        Object.entries(stored.tikpocProcessedFollowers || {}).filter(
          ([key]) => !String(key).toLowerCase().startsWith(followerPrefix),
        ),
      ),
      tikpocDmBaselines: omitAccountKey(stored.tikpocDmBaselines, accountId),
      tikpocDmProcessed: Object.fromEntries(
        Object.entries(stored.tikpocDmProcessed || {}).filter(
          ([, record]) => normalizeAccountId(record && record.accountId) !== expected,
        ),
      ),
    };
  }

  function popupBinding(settings, status) {
    if (
      !status ||
      normalizeAccountId(status.accountId) !== normalizeAccountId(settings && settings.accountId)
    ) {
      return { state: "unverified", observedUsername: "" };
    }
    return {
      state: status.state || "unverified",
      observedUsername: status.observedUsername || "",
    };
  }

  return {
    bindingMode,
    bindingModeLabel,
    bindingObservation,
    bindingStateLabel,
    canObserveBinding,
    defaultSettings,
    mergeRuntimeSettings,
    popupBinding,
    requiresRebindConfirmation,
    resetForAccount,
    settingsForBinding,
    shouldScheduleForStorageChanges,
  };
});
