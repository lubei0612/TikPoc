(function startFollowerBridge() {
  const core = globalThis.TikPocFollowerCore;
  if (!core) {
    return;
  }

  const SETTINGS_KEY = "tikpocSettings";
  const PROCESSED_KEY = "tikpocProcessedFollowers";
  const BASELINE_KEY = "tikpocFollowerBaselines";
  const MAX_PROCESSED = 1000;
  const RETRY_AFTER_MS = 30 * 60 * 1000;
  const MAX_ATTEMPTS = 2;
  const ACTIVITY_LABELS = new Set(["activity", "活动", "活動"]);
  let scanTimer = null;
  let scanning = false;
  let activityOpenedByBridge = false;
  let activityOpenedAt = 0;
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

  async function handleCandidate(candidate, settings, processed) {
    const key = core.buildFollowerDedupKey(settings.accountId, candidate.username);
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
    };
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
      const attempts = Number(processed[key]?.attempts || 0) + 1;
      await saveProcessed(processed, key, {
        status: "unresolved",
        attempts,
        updatedAt: Date.now(),
      });
    } finally {
      inFlight.delete(key);
    }
  }

  function maybeOpenActivity(settings) {
    if (!settings.autoOpenActivity || activityOpenedByBridge) {
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
      activityOpenedByBridge = true;
      activityOpenedAt = Date.now();
      return;
    }
    if (activity) {
      activityOpenedByBridge = true;
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
      const key = core.buildFollowerDedupKey(settings.accountId, candidate.username);
      processed[key] = {
        status: "baseline",
        attempts: 0,
        updatedAt: now,
      };
    }
    baselines[settings.accountId] = now;
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
      if (
        !settings.enabled ||
        !settings.accountId ||
        !settings.deviceId ||
        !settings.dashboardUrl
      ) {
        return;
      }
      maybeOpenActivity(settings);
      const processed = stored[PROCESSED_KEY] || {};
      const baselines = stored[BASELINE_KEY] || {};
      const links = Array.from(document.querySelectorAll("a[href*='/@']"))
        .filter(visible)
        .slice(-600);
      const candidates = links.map(candidateFromLink).filter(Boolean);
      if (!baselines[settings.accountId]) {
        const activitySettled =
          activityOpenedAt > 0 && Date.now() - activityOpenedAt >= 2000;
        if (candidates.length > 0 || activityPanelVisible() || activitySettled) {
          await establishBaseline(settings, candidates, processed, baselines);
        }
        return;
      }
      for (const candidate of candidates) {
        await handleCandidate(candidate, settings, processed);
      }
    } finally {
      scanning = false;
    }
  }

  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 250);
  }

  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  chrome.storage.onChanged.addListener(scheduleScan);
  scheduleScan();
})();
