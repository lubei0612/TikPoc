(function exposeDmCore(root, factory) {
  let cryptoProvider = root.crypto;
  if ((!cryptoProvider || !cryptoProvider.subtle) && typeof require === "function") {
    cryptoProvider = require("node:crypto").webcrypto;
  }
  const api = factory(cryptoProvider);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocDmCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDmCore(cryptoProvider) {
  const MESSAGE_FIELDS = [
    "accountId",
    "conversationId",
    "sender",
    "messageId",
    "timestamp",
    "text",
  ];

  function normalizeText(value) {
    return String(value || "")
      .normalize("NFKC")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizedUsername(value) {
    const username = normalizeText(value).replace(/^@/, "").toLowerCase();
    return /^[a-z0-9._]+$/.test(username) ? username : "";
  }

  function conversationKey(value, username) {
    try {
      const url = new URL(String(value || ""));
      const hostname = url.hostname.toLowerCase();
      const isTikTok =
        url.protocol === "https:" &&
        (hostname === "tiktok.com" || hostname === "www.tiktok.com");
      const pathname = url.pathname.replace(/\/+$/, "") || "/";
      if (isTikTok && (pathname === "/messages" || pathname.startsWith("/messages/"))) {
        return `https://www.tiktok.com${pathname}`;
      }
    } catch (_error) {
      // Fall through to the participant identity when no stable URL is visible.
    }
    const fallback = normalizedUsername(username);
    return fallback ? `user:${fallback}` : null;
  }

  function isActionableInbound(message) {
    return Boolean(
      message &&
        normalizeText(message.direction).toLowerCase() === "inbound" &&
        message.latest !== false &&
        normalizeText(message.text),
    );
  }

  function normalizedMessageFields(message) {
    return MESSAGE_FIELDS.map((field) => normalizeText(message && message[field]));
  }

  async function fingerprintMessage(message) {
    if (!cryptoProvider || !cryptoProvider.subtle) {
      throw new Error("Web Crypto SHA-256 is unavailable");
    }
    const encoded = new TextEncoder().encode(JSON.stringify(normalizedMessageFields(message)));
    const digest = await cryptoProvider.subtle.digest("SHA-256", encoded);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function sameInbound(left, right) {
    if (!left || !right) {
      return false;
    }
    const leftFields = normalizedMessageFields(left);
    const rightFields = normalizedMessageFields(right);
    return leftFields.every((value, index) => value === rightFields[index]);
  }

  function buttonLabel(button) {
    if (!button) {
      return "";
    }
    if (button.label !== undefined) {
      return normalizeText(button.label).toLowerCase();
    }
    const ariaLabel =
      typeof button.getAttribute === "function" ? button.getAttribute("aria-label") : "";
    return normalizeText(ariaLabel || button.textContent).toLowerCase();
  }

  function findSemanticButton(buttons, labels) {
    const accepted = new Set((labels || []).map((label) => normalizeText(label).toLowerCase()));
    if (!accepted.size) {
      return null;
    }
    const matches = Array.from(buttons || []).filter(
      (button) => button && button.visible !== false && accepted.has(buttonLabel(button)),
    );
    return matches.length === 1 ? matches[0] : null;
  }

  function hasMatchingOutbound(expectedText, messages) {
    const expected = normalizeText(expectedText);
    return Boolean(
      expected &&
        Array.from(messages || []).some(
          (message) =>
            normalizeText(message && message.direction).toLowerCase() === "outbound" &&
            normalizeText(message && message.text) === expected,
        ),
    );
  }

  return {
    normalizeText,
    conversationKey,
    isActionableInbound,
    fingerprintMessage,
    sameInbound,
    findSemanticButton,
    hasMatchingOutbound,
  };
});
