(function exposeDmContent(root, factory) {
  let core = root.TikPocDmCore;
  if (!core && typeof require === "function") {
    core = require("./dm-core.js");
  }
  const api = factory(core);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    return;
  }
  root.TikPocDmContent = api;
  api.startBrowserBridge();
})(typeof globalThis !== "undefined" ? globalThis : this, function createDmContent(core) {
  const SETTINGS_KEY = "tikpocSettings";
  const BASELINES_KEY = "tikpocDmBaselines";
  const PROCESSED_KEY = "tikpocDmProcessed";
  const HEALTH_TICK = "TIKPOC_HEALTH_TICK";
  const MAX_PROCESSED = 1000;

  function pageRole(locationValue) {
    const path = String(locationValue && locationValue.pathname || "");
    return path === "/messages" || path.startsWith("/messages/") ? "messages" : "other";
  }

  function visible(element) {
    if (!element || element.hidden === true) {
      return false;
    }
    if (typeof element.getAttribute === "function" && element.getAttribute("aria-hidden") === "true") {
      return false;
    }
    if (typeof element.closest === "function" && element.closest("[hidden], [aria-hidden='true']")) {
      return false;
    }
    if (typeof element.getClientRects === "function" && element.getClientRects().length === 0) {
      return false;
    }
    const view = element.ownerDocument && element.ownerDocument.defaultView;
    const style = view && typeof view.getComputedStyle === "function"
      ? view.getComputedStyle(element)
      : null;
    return !style || (style.display !== "none" && style.visibility !== "hidden");
  }

  function elementLabel(element) {
    if (!element) {
      return "";
    }
    return core.normalizeText(
      typeof element.getAttribute === "function" && element.getAttribute("aria-label") ||
      element.textContent ||
      "",
    );
  }

  function firstAttribute(element, names) {
    for (const name of names) {
      const value = element && typeof element.getAttribute === "function"
        ? element.getAttribute(name)
        : "";
      if (core.normalizeText(value)) {
        return core.normalizeText(value);
      }
    }
    return "";
  }

  function conversationRows(documentValue = document) {
    const selectors = [
      "[data-e2e*='conversation']",
      "[data-e2e*='chat-item']",
      "[role='listitem'] a[href*='/messages/']",
    ];
    const candidates = Array.from(documentValue.querySelectorAll(selectors.join(", ")));
    const rows = [];
    const seen = new Set();
    for (const candidate of candidates) {
      const row = candidate.matches && candidate.matches("a[href*='/messages/']")
        ? candidate.closest("[role='listitem']") || candidate
        : candidate;
      if (visible(row) && !seen.has(row)) {
        seen.add(row);
        rows.push(row);
      }
    }
    return rows;
  }

  function rowSnapshot(row) {
    const link = row.matches && row.matches("a[href*='/messages/']")
      ? row
      : row.querySelector("a[href*='/messages/']");
    const stableId = firstAttribute(row, [
      "data-conversation-id",
      "data-chat-id",
      "data-thread-id",
      "data-id",
    ]);
    const href = link && link.href || "";
    const username = firstAttribute(row, ["data-username", "data-user-name"]);
    const key = core.conversationKey(href, username) || stableId || href;
    const messageId = firstAttribute(row, ["data-message-id", "data-last-message-id"]);
    const unread = Boolean(
      row.matches && row.matches("[data-unread='true']") ||
      row.querySelector("[aria-label*='unread' i], [data-e2e*='unread'], [data-unread='true']"),
    );
    const signature = core.normalizeText(
      [stableId, messageId, unread ? "unread" : "read", elementLabel(row)].join("|"),
    );
    return { key, signature, unread };
  }

  async function openConversation(row) {
    const target = row.matches && row.matches("a[href*='/messages/'], button, [role='button']")
      ? row
      : row.querySelector("a[href*='/messages/'], button, [role='button']");
    if (!target || !visible(target)) {
      return false;
    }
    target.click();
    await new Promise((resolve) => setTimeout(resolve, 150));
    return true;
  }

  function bubbleDirection(bubble) {
    const signal = firstAttribute(bubble, [
      "data-direction",
      "data-message-direction",
      "data-e2e",
      "aria-label",
    ]).toLowerCase();
    if (/outbound|outgoing|sent|self/.test(signal)) {
      return "outbound";
    }
    if (/inbound|incoming|received|other/.test(signal)) {
      return "inbound";
    }
    return "unknown";
  }

  function messageFromBubble(bubble, participant) {
    const textNode = bubble.querySelector("[data-e2e*='message-text'], [data-e2e*='chat-text']");
    const timeNode = bubble.querySelector("time, [data-e2e*='time']");
    const timestamp = firstAttribute(timeNode, ["datetime", "data-timestamp"]) ||
      firstAttribute(bubble, ["data-timestamp", "data-time"]) ||
      elementLabel(timeNode);
    let timestampMs = Number(timestamp);
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      timestampMs = Date.parse(timestamp);
    } else if (timestampMs > 0 && timestampMs < 10_000_000_000) {
      timestampMs *= 1000;
    }
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      timestampMs = Date.now();
    }
    const direction = bubbleDirection(bubble);
    return {
      sender: direction === "inbound"
        ? firstAttribute(bubble, ["data-sender", "data-username"]) || participant
        : "self",
      messageId: firstAttribute(bubble, ["data-message-id", "data-id"]),
      timestamp,
      timestampMs,
      text: core.normalizeText(textNode && textNode.textContent || bubble.textContent || ""),
      direction,
    };
  }

  function readActiveConversation(documentValue = document, accountId = "") {
    const scope = documentValue.querySelector("main, [role='main']") || documentValue;
    const participantNode = scope.querySelector(
      "[data-e2e*='chat-header'][data-username], [data-e2e*='chat-header'] a[href*='/@'], " +
      "[role='heading'][data-username], [role='heading'] a[href*='/@']",
    );
    let participant = firstAttribute(participantNode, ["data-username", "data-user-name"]);
    if (!participant && participantNode && participantNode.href) {
      const match = new URL(participantNode.href, globalThis.location && globalThis.location.href).pathname.match(/^\/@([^/]+)/);
      participant = match ? decodeURIComponent(match[1]) : "";
    }
    participant = participant || elementLabel(participantNode);
    const bubbles = Array.from(scope.querySelectorAll(
      "[data-e2e*='message-item'], [data-e2e*='chat-message'], [data-message-id]",
    )).filter(visible);
    const messages = bubbles.map((bubble) => messageFromBubble(bubble, participant));
    const latest = messages.at(-1);
    if (!latest) {
      return null;
    }
    const href = globalThis.location && globalThis.location.href || "";
    const conversationId = core.conversationKey(href, participant);
    if (!conversationId) {
      return null;
    }
    return {
      accountId,
      conversationId,
      ...latest,
      latest: true,
      messages,
    };
  }

  function findComposer(documentValue = document) {
    const candidates = Array.from(documentValue.querySelectorAll(
      "[data-e2e*='message-input'][contenteditable='true'], " +
      "[data-e2e*='chat-input'][contenteditable='true'], " +
      "textarea[aria-label], [role='textbox'][contenteditable='true']",
    ));
    const visibleCandidates = candidates.filter(visible);
    return visibleCandidates.length === 1 ? visibleCandidates[0] : null;
  }

  function setComposerText(composer, text) {
    const expected = core.normalizeText(text);
    if (!composer || !expected) {
      return false;
    }
    composer.focus();
    const view = composer.ownerDocument && composer.ownerDocument.defaultView || globalThis;
    if (composer.isContentEditable || composer.getAttribute && composer.getAttribute("contenteditable") === "true") {
      composer.textContent = expected;
    } else {
      const prototype = Object.getPrototypeOf(composer);
      const descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, "value");
      if (descriptor && descriptor.set) {
        descriptor.set.call(composer, expected);
      } else {
        composer.value = expected;
      }
    }
    composer.dispatchEvent(new view.Event("input", { bubbles: true, composed: true }));
    composer.dispatchEvent(new view.Event("change", { bubbles: true }));
    return core.normalizeText(composer.value !== undefined ? composer.value : composer.textContent) === expected;
  }

  function findSendButton(documentValue = document) {
    const buttons = documentValue.querySelectorAll(
      "button[aria-label], [role='button'][aria-label], [data-e2e*='send']",
    );
    return core.findSemanticButton(buttons, ["send", "发送", "發送"]);
  }

  async function waitForOutbound(expectedText, options = {}) {
    const read = options.read || (() => readActiveConversation());
    const timeoutMs = Number(options.timeoutMs || 5000);
    const intervalMs = Number(options.intervalMs || 100);
    const deadline = Date.now() + timeoutMs;
    do {
      const active = read();
      if (active && core.hasMatchingOutbound(expectedText, active.messages)) {
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    } while (Date.now() < deadline);
    return false;
  }

  function buildHealthPayload(settings, locationValue, signedIn, timestampMs = Date.now()) {
    return {
      account_id: settings.accountId,
      device_id: settings.deviceId,
      page_role: pageRole(locationValue),
      path: String(locationValue && locationValue.pathname || ""),
      signed_in: Boolean(signedIn),
      timestamp_ms: Number(timestampMs),
    };
  }

  function trimProcessed(processed) {
    return Object.fromEntries(
      Object.entries(processed)
        .sort((left, right) => Number(right[1].updatedAt || 0) - Number(left[1].updatedAt || 0))
        .slice(0, MAX_PROCESSED),
    );
  }

  function createSerializedWorkflow({ storage, transport, adapter, now = Date.now, ownerId }) {
    let queue = Promise.resolve();

    function scan(settings) {
      const run = queue.then(() => scanOnce(settings), () => scanOnce(settings));
      queue = run.catch(() => {});
      return run;
    }

    async function scanOnce(settings) {
      const rows = adapter.conversationRows();
      const snapshots = rows
        .map((row) => ({ row, snapshot: adapter.rowSnapshot(row) }))
        .filter(({ snapshot }) => snapshot && snapshot.key);
      const baselines = await storage.get(BASELINES_KEY) || {};
      const baseline = baselines[settings.accountId];
      if (!baseline) {
        baselines[settings.accountId] = Object.fromEntries(
          snapshots.map(({ snapshot }) => [snapshot.key, snapshot.signature]),
        );
        await storage.set(BASELINES_KEY, baselines);
        return "baseline";
      }
      const candidate = snapshots.find(({ snapshot }) =>
        snapshot.unread || baseline[snapshot.key] !== snapshot.signature,
      );
      if (!candidate) {
        return "idle";
      }
      const opened = await adapter.openConversation(candidate.row);
      if (opened === false) {
        return "navigation_failed";
      }
      const inbound = adapter.readActiveConversation(settings.accountId);
      if (!core.isActionableInbound(inbound)) {
        baseline[candidate.snapshot.key] = candidate.snapshot.signature;
        await storage.set(BASELINES_KEY, baselines);
        return "ignored";
      }
      const activeKey = core.conversationKey(inbound.conversationId, inbound.sender);
      if (activeKey !== candidate.snapshot.key) {
        return "navigation_pending";
      }
      const fingerprint = await core.fingerprintMessage(inbound);
      const processed = await storage.get(PROCESSED_KEY) || {};
      if (processed[fingerprint]) {
        baseline[candidate.snapshot.key] = candidate.snapshot.signature;
        await storage.set(BASELINES_KEY, baselines);
        return "duplicate";
      }
      const plan = await transport("TIKPOC_DM_PLAN", {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        conversation_id: inbound.conversationId,
        fingerprint,
        participant_username: inbound.sender,
        text: inbound.text,
        timestamp_ms: Number(inbound.timestampMs || now()),
      });
      const current = adapter.readActiveConversation(settings.accountId);
      const currentFingerprint = current && core.isActionableInbound(current)
        ? await core.fingerprintMessage(current)
        : "";
      const unchanged = core.sameInbound(inbound, current) &&
        currentFingerprint === fingerprint &&
        plan.conversation_id === inbound.conversationId &&
        plan.inbound_fingerprint === fingerprint;
      if (!unchanged || !core.normalizeText(plan.reply_text)) {
        await transport("TIKPOC_DM_RESULT", {
          account_id: settings.accountId,
          device_id: settings.deviceId,
          plan_id: plan.plan_id,
          state: "superseded",
        });
        processed[fingerprint] = { state: "superseded", updatedAt: now() };
        await storage.set(PROCESSED_KEY, trimProcessed(processed));
        baseline[candidate.snapshot.key] = candidate.snapshot.signature;
        await storage.set(BASELINES_KEY, baselines);
        return "superseded";
      }
      const actionKey = `dm_send:${plan.plan_id}`;
      const identity = {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        action_type: "dm_send",
        action_key: actionKey,
        owner_id: ownerId,
      };
      const claim = await transport("TIKPOC_ACTION_CLAIM", {
        ...identity,
        timestamp_ms: now(),
        lease_seconds: 30,
      });
      if (!claim.claimed) {
        return "busy";
      }
      const composer = adapter.findComposer();
      const composed = composer && adapter.setComposerText(composer, plan.reply_text);
      const sendButton = composed && adapter.findSendButton();
      processed[fingerprint] = { state: "sending", updatedAt: now() };
      await storage.set(PROCESSED_KEY, trimProcessed(processed));
      if (sendButton) {
        sendButton.click();
      }
      const confirmed = Boolean(sendButton) && await adapter.waitForOutbound(plan.reply_text);
      const resultState = confirmed ? "sent" : "uncertain";
      await transport("TIKPOC_DM_RESULT", {
        account_id: settings.accountId,
        device_id: settings.deviceId,
        plan_id: plan.plan_id,
        state: resultState,
      });
      await transport("TIKPOC_ACTION_RESULT", {
        ...identity,
        state: confirmed ? "completed" : "uncertain",
      });
      processed[fingerprint] = { state: resultState, updatedAt: now() };
      await storage.set(PROCESSED_KEY, trimProcessed(processed));
      baseline[candidate.snapshot.key] = candidate.snapshot.signature;
      await storage.set(BASELINES_KEY, baselines);
      return resultState;
    }

    return { scan };
  }

  function startBrowserBridge() {
    if (!globalThis.chrome || !globalThis.document || pageRole(globalThis.location) !== "messages") {
      return;
    }
    function storageGet(key) {
      return new Promise((resolve) => chrome.storage.local.get([key], (value) => resolve(value[key])));
    }
    function storageSet(key, value) {
      return new Promise((resolve) => chrome.storage.local.set({ [key]: value }, resolve));
    }
    function transport(type, body, settings) {
      return new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type, dashboardUrl: settings.dashboardUrl, body },
          (response) => {
            if (chrome.runtime.lastError || !response || !response.ok) {
              reject(new Error(response && response.error || chrome.runtime.lastError && chrome.runtime.lastError.message || "Bridge request failed"));
              return;
            }
            resolve(response.result);
          },
        );
      });
    }
    const storage = { get: storageGet, set: storageSet };
    const adapter = {
      conversationRows,
      rowSnapshot,
      openConversation,
      readActiveConversation: (accountId) => readActiveConversation(document, accountId),
      findComposer,
      setComposerText,
      findSendButton,
      waitForOutbound: (text) => waitForOutbound(text),
    };
    let settings = null;
    const workflow = createSerializedWorkflow({
      storage,
      transport: (type, body) => transport(type, body, settings),
      adapter,
      ownerId: globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
        ? globalThis.crypto.randomUUID()
        : `tab-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    });
    let timer = null;
    async function loadSettings() {
      settings = await storageGet(SETTINGS_KEY) || {};
      return settings;
    }
    function ready(value) {
      return Boolean(
        value.enabled &&
        value.browserDmEnabled !== false &&
        value.accountId &&
        value.deviceId &&
        value.dashboardUrl
      );
    }
    async function health() {
      const current = await loadSettings();
      if (!ready(current)) {
        return;
      }
      const signedIn = Boolean(document.querySelector(
        "[data-e2e*='avatar'], [data-e2e*='profile-icon'], nav a[href^='/@']",
      ));
      await transport(
        "TIKPOC_BROWSER_HEALTH",
        buildHealthPayload(current, location, signedIn),
        current,
      );
    }
    async function run() {
      const current = await loadSettings();
      if (ready(current)) {
        await workflow.scan(current);
      }
    }
    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(() => run().catch(() => {}), 250);
    }
    new MutationObserver(schedule).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    chrome.storage.onChanged.addListener(schedule);
    chrome.runtime.onMessage.addListener((message) => {
      if (message && message.type === HEALTH_TICK) {
        health().catch(() => {});
      }
      return false;
    });
    health().catch(() => {});
    schedule();
  }

  return {
    buildHealthPayload,
    conversationRows,
    createSerializedWorkflow,
    elementLabel,
    findComposer,
    findSendButton,
    openConversation,
    pageRole,
    readActiveConversation,
    rowSnapshot,
    setComposerText,
    startBrowserBridge,
    visible,
    waitForOutbound,
  };
});
