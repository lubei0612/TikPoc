(function startFollowerBridge() {
  const core = globalThis.TikPocFollowerCore;
  const binding = globalThis.TikPocBindingCore;
  const optionsCore = globalThis.TikPocOptionsCore;
  if (!core || !binding || !optionsCore) {
    return;
  }

  const SETTINGS_KEY = "tikpocSettings";
  const PROCESSED_KEY = "tikpocProcessedFollowers";
  const BASELINE_KEY = "tikpocFollowerBaselines";
  const HEALTH_TICK = "TIKPOC_HEALTH_TICK";
  const MAX_PROCESSED = 1000;
  const RETRY_AFTER_MS = 30 * 60 * 1000;
  const MAX_ATTEMPTS = 2;
  const ACTIVITY_LABELS = new Set(["activity", "活动", "活動"]);
  let scanTimer = null;
  let scanning = false;
  let activityOpenedAt = 0;
  let lastScanAtMs = 0;
  let lastSuccessAtMs = 0;
  let scanState = "not_started";
  const ownerId = globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `tab-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const inFlight = new Set();

  function storageGet(keys) {
    return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
  }

  function storageSet(values) {
    return new Promise((resolve) => chrome.storage.local.set(values, resolve));
  }

  function sendMessage(message) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          reject(chrome.runtime.lastError);
          return;
        }
        if (!response || !response.ok) {
          reject(new Error(response?.error || "Bridge request failed"));
          return;
        }
        resolve(response.result);
      });
    });
  }

  function visible(element) {
    return element instanceof Element && element.getClientRects().length > 0;
  }

  function elementLabel(element) {
    return core.normalizeText(
      element.getAttribute("aria-label") || element.textContent || "",
    );
  }

  function findNotificationRow(link) {
    let current = link.parentElement;
    for (let depth = 0; current && depth < 8; depth += 1) {
      const text = core.normalizeText(current.textContent || "");
      if (text.length <= 700 && core.isFollowerNotification(text)) {
        return current;
      }
      if (text.length > 1600 && depth > 2) {
        break;
      }
      current = current.parentElement;
    }
    return null;
  }

  function rowButtons(row) {
    return Array.from(row.querySelectorAll("button, [role='button']")).filter(
      (element) => visible(element) && elementLabel(element),
    );
  }

  function candidateFromLink(link) {
    if (!visible(link)) {
      return null;
    }
    const row = findNotificationRow(link);
    if (!row) {
      return null;
    }
    const buttons = rowButtons(row);
    const candidate = core.classifyCandidate({
      rowText: row.textContent || "",
      profileUrl: link.href,
      buttonLabels: buttons.map(elementLabel),
    });
    if (!candidate) {
      return null;
    }
    return {
      ...candidate,
      eventId: core.extractFollowerEventId(row),
      row,
      button: candidate.buttonIndex >= 0 ? buttons[candidate.buttonIndex] : null,
    };
  }

  async function report(settings, eventType, dedupKey, payload) {
    return sendMessage({
      type: "TIKPOC_REPORT_EVENT",
      dashboardUrl: settings.dashboardUrl,
      event: {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        observed_username: settings.observedUsername,
        binding_state: settings.bindingState,
        event_type: eventType,
        dedup_key: dedupKey,
        payload,
      },
    });
  }

  async function saveProcessed(processed, key, value) {
    processed[key] = value;
    const entries = Object.entries(processed)
      .sort((left, right) => Number(right[1].updatedAt || 0) - Number(left[1].updatedAt || 0))
      .slice(0, MAX_PROCESSED);
    await storageSet({ [PROCESSED_KEY]: Object.fromEntries(entries) });
  }

  async function reportHealth() {
    const stored = await storageGet([SETTINGS_KEY]);
    const settings = stored[SETTINGS_KEY] || {};
    if (
      !settings.enabled ||
      !optionsCore.canObserveBinding(settings) ||
      !settings.deviceId ||
      !settings.dashboardUrl
    ) {
      return;
    }
    const bindingResult = binding.evaluateBinding(
      document,
      settings.expectedTikTokUsername,
    );
    await storageSet({
      tikpocBindingStatus: optionsCore.bindingObservation(
        settings.accountId,
        bindingResult,
      ),
    });
    const signedIn = Boolean(document.querySelector(
      "[data-e2e*='avatar'], [data-e2e*='profile-icon'], nav a[href^='/@']",
    ));
    await sendMessage({
      type: "TIKPOC_BROWSER_HEALTH",
      dashboardUrl: settings.dashboardUrl,
      body: {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        page_role: "activity",
        path: String(globalThis.location && globalThis.location.pathname || ""),
        signed_in: signedIn,
        observed_username: bindingResult.observedUsername,
        binding_state: bindingResult.state,
        timestamp_ms: Date.now(),
        last_scan_at_ms: lastScanAtMs,
        last_success_at_ms: lastSuccessAtMs,
        scan_state: scanState,
      },
    });
  }

  async function handleCandidate(candidate, settings, processed) {
    const key = core.buildFollowerDedupKey(
      settings.accountId,
      candidate.username,
      candidate.eventId,
    );
    if (
      inFlight.has(key) ||
      !core.shouldAttemptRecord(
        processed[key],
        Date.now(),
        RETRY_AFTER_MS,
        MAX_ATTEMPTS,
      )
    ) {
      return;
    }
    inFlight.add(key);
    const payload = {
      username: candidate.username,
      profile_url: candidate.profileUrl,
      event_id: candidate.eventId || "",
    };
    let actionIdentity = null;
    let actionState = null;
    try {
      if (candidate.state === "completed") {
        await report(settings, "followback_completed", key, {
          ...payload,
          clicked: false,
        });
        await saveProcessed(processed, key, {
          status: "completed",
          attempts: Number(processed[key]?.attempts || 0),
          updatedAt: Date.now(),
        });
        return;
      }
      if (candidate.state !== "actionable" || !candidate.button) {
        await report(settings, "followback_unresolved", key, payload);
        await saveProcessed(processed, key, {
          status: "unresolved",
          attempts: MAX_ATTEMPTS,
          updatedAt: Date.now(),
        });
        return;
      }

      await report(settings, "new_follower", key, payload);
      const claimIdentity = {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        observed_username: settings.observedUsername,
        binding_state: settings.bindingState,
        action_type: "followback",
        action_key: key,
        owner_id: ownerId,
      };
      const claim = await sendMessage({
        type: "TIKPOC_ACTION_CLAIM",
        dashboardUrl: settings.dashboardUrl,
        body: {
          ...claimIdentity,
          timestamp_ms: Date.now(),
          lease_seconds: 30,
        },
      });
      if (!claim.claimed) {
        return;
      }
      actionIdentity = claimIdentity;
      const attempts = Number(processed[key]?.attempts || 0) + 1;
      await saveProcessed(processed, key, {
        status: "attempted",
        attempts,
        updatedAt: Date.now(),
      });
      candidate.button.click();
      await new Promise((resolve) => setTimeout(resolve, 1800));
      const state = candidate.button.isConnected
        ? core.followButtonState(elementLabel(candidate.button))
        : "completed";
      const completed = state === "completed";
      actionState = completed ? "completed" : "uncertain";
      await report(
        settings,
        completed ? "followback_completed" : "followback_unresolved",
        key,
        { ...payload, clicked: true },
      );
      await saveProcessed(processed, key, {
        status: completed ? "completed" : "unresolved",
        attempts,
        updatedAt: Date.now(),
      });
    } catch (_error) {
      actionState = actionIdentity ? "uncertain" : null;
      const attempts = Number(processed[key]?.attempts || 0) + 1;
      await saveProcessed(processed, key, {
        status: "unresolved",
        attempts,
        updatedAt: Date.now(),
      });
    } finally {
      if (actionIdentity && actionState) {
        try {
          await sendMessage({
            type: "TIKPOC_ACTION_RESULT",
            dashboardUrl: settings.dashboardUrl,
            body: { ...actionIdentity, state: actionState },
          });
        } catch (_error) {
          // The server lease remains busy until expiry when result delivery fails.
        }
      }
      inFlight.delete(key);
    }
  }

  function maybeOpenActivity(settings) {
    const panelVisible = activityPanelVisible();
    if (panelVisible) {
      if (!activityOpenedAt) {
        activityOpenedAt = Date.now();
      }
      return;
    }
    if (!core.shouldOpenActivity(
      settings.autoOpenActivity,
      panelVisible,
      activityOpenedAt,
    )) {
      return;
    }
    const controls = Array.from(
      document.querySelectorAll(
        "nav a, nav button, nav [role='link'], nav [role='button'], " +
          "aside a, aside button, aside [role='link'], aside [role='button'], " +
          "[data-e2e*='activity']",
      ),
    );
    const activity = controls.find((element) => {
      const text = core.normalizeText(element.textContent || "").toLowerCase();
      const firstToken = text.replace(/[0-9]+/g, "").trim();
      return visible(element) && ACTIVITY_LABELS.has(firstToken);
    });
    if (activity && activity.getAttribute("aria-expanded") === "true") {
      activityOpenedAt = Date.now();
      return;
    }
    if (activity) {
      activityOpenedAt = Date.now();
      activity.click();
      setTimeout(scheduleScan, 2200);
    }
  }

  function activityPanelVisible() {
    const panels = Array.from(document.querySelectorAll("[role='dialog']"));
    return panels.some((panel) => {
      if (!visible(panel)) {
        return false;
      }
      const headings = Array.from(
        panel.querySelectorAll("h1, h2, h3, [role='heading']"),
      );
      return headings.some((heading) => {
        const label = core.normalizeText(heading.textContent || "").toLowerCase();
        return ACTIVITY_LABELS.has(label);
      });
    });
  }

  function trimProcessed(processed) {
    return Object.fromEntries(
      Object.entries(processed)
        .sort(
          (left, right) =>
            Number(right[1].updatedAt || 0) - Number(left[1].updatedAt || 0),
        )
        .slice(0, MAX_PROCESSED),
    );
  }

  async function establishBaseline(settings, candidates, processed, baselines) {
    const now = Date.now();
    for (const candidate of candidates) {
      const key = core.buildFollowerDedupKey(
        settings.accountId,
        candidate.username,
        candidate.eventId,
      );
      processed[key] = {
        status: "baseline",
        attempts: 0,
        updatedAt: now,
      };
    }
    baselines[settings.accountId] = { version: 2, establishedAt: now };
    await storageSet({
      [PROCESSED_KEY]: trimProcessed(processed),
      [BASELINE_KEY]: baselines,
    });
  }

  async function scan() {
    if (scanning) {
      return;
    }
    scanning = true;
    try {
      const stored = await storageGet([SETTINGS_KEY, PROCESSED_KEY, BASELINE_KEY]);
      const settings = stored[SETTINGS_KEY] || {};
      if (!optionsCore.canObserveBinding(settings)) {
        return;
      }
      lastScanAtMs = Date.now();
      scanState = "scanning";
      const bindingResult = binding.evaluateBinding(
        document,
        settings.expectedTikTokUsername,
      );
      await storageSet({
        tikpocBindingStatus: optionsCore.bindingObservation(
          settings.accountId,
          bindingResult,
        ),
      });
      if (!settings.deviceId || !settings.dashboardUrl) {
        return;
      }
      if (bindingResult.state !== "ready") {
        return;
      }
      const boundSettings = {
        ...settings,
        bindingState: bindingResult.state,
        observedUsername: bindingResult.observedUsername,
      };
      maybeOpenActivity(boundSettings);
      const processed = stored[PROCESSED_KEY] || {};
      const baselines = stored[BASELINE_KEY] || {};
      const links = Array.from(document.querySelectorAll("a[href*='/@']"))
        .filter(visible)
        .slice(-600);
      const candidates = links.map(candidateFromLink).filter(Boolean);
      const scanPhase = core.followerScanPhase({
        baselineReady: core.followerBaselineReady(baselines[settings.accountId]),
        followbackEnabled: core.browserFeatureEnabled(
          settings,
          "browserFollowbackEnabled",
        ),
      });
      if (scanPhase === "baseline") {
        if (core.shouldEstablishFollowerBaseline({
          candidateCount: candidates.length,
          activityOpenedAt,
          now: Date.now(),
        })) {
          await establishBaseline(boundSettings, candidates, processed, baselines);
        }
        return;
      }
      if (scanPhase !== "action") {
        return;
      }
      for (const candidate of candidates) {
        await handleCandidate(candidate, boundSettings, processed);
      }
    } catch (error) {
      scanState = "error";
      throw error;
    } finally {
      if (scanState === "scanning") {
        lastSuccessAtMs = Date.now();
        scanState = "idle";
      }
      scanning = false;
    }
  }

  const requestScan = core.createCoalescingRunner(scan);

  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => requestScan().catch(() => {}), 250);
  }

  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (optionsCore.shouldScheduleForStorageChanges(changes)) {
      scheduleScan();
    }
  });
  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === HEALTH_TICK) {
      reportHealth().then(scheduleScan, scheduleScan);
    }
    return false;
  });
  core.installContinuousTriggers(document, globalThis, scheduleScan);
  core.installWatchdog(setInterval, scheduleScan);
  reportHealth().catch(() => {});
  scheduleScan();
})();
