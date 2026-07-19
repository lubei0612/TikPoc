(function exposeBindingCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocBindingCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createBindingCore() {
  const ACCOUNT_LINK_SELECTOR = [
    "nav a[href*='/@']",
    "header a[href*='/@']",
    "[data-e2e*='profile-icon'] a[href*='/@']",
    "a[data-e2e*='profile'][href*='/@']",
  ].join(", ");

  function normalizeUsername(value) {
    const normalized = String(value || "")
      .normalize("NFKC")
      .trim()
      .replace(/^@/, "")
      .toLowerCase();
    return /^[a-z0-9._]+$/.test(normalized) ? normalized : "";
  }

  function usernameFromHref(value) {
    try {
      const url = new URL(String(value || ""), "https://www.tiktok.com/");
      if (!new Set(["tiktok.com", "www.tiktok.com"]).has(url.hostname.toLowerCase())) {
        return "";
      }
      const match = url.pathname.match(/^\/@([^/?#]+)/);
      return normalizeUsername(match ? decodeURIComponent(match[1]) : "");
    } catch (_error) {
      return "";
    }
  }

  function visibleAccountUsernames(documentValue) {
    if (!documentValue || typeof documentValue.querySelectorAll !== "function") {
      return [];
    }
    const usernames = Array.from(documentValue.querySelectorAll(ACCOUNT_LINK_SELECTOR))
      .filter((element) => !element.getClientRects || element.getClientRects().length > 0)
      .map((element) => usernameFromHref(element.href || element.getAttribute && element.getAttribute("href")))
      .filter(Boolean);
    return [...new Set(usernames)];
  }

  function evaluateBinding(documentValue, expectedValue) {
    const body = documentValue && documentValue.body;
    const visibleText = body && typeof body.innerText === "string"
      ? body.innerText
      : body && body.textContent;
    const text = String(visibleText || "")
      .normalize("NFKC")
      .replace(/\s+/g, " ")
      .trim();
    if (/verify to continue|security check|captcha|请完成验证|安全验证|驗證/.test(text.toLowerCase())) {
      return { state: "verification_required", observedUsername: "" };
    }
    const usernames = visibleAccountUsernames(documentValue);
    const observedUsername = usernames.length === 1 ? usernames[0] : "";
    const expectedUsername = normalizeUsername(expectedValue);
    if (!expectedUsername) {
      return { state: "unverified", observedUsername };
    }
    if (usernames.length === 0) {
      const signedOut = /\blog in\b|\bsign up\b|登录|登入/.test(text.toLowerCase());
      return { state: signedOut ? "signed_out" : "unverified", observedUsername: "" };
    }
    if (usernames.length !== 1) {
      return { state: "unverified", observedUsername: "" };
    }
    return observedUsername === expectedUsername
      ? { state: "ready", observedUsername }
      : { state: "mismatch", observedUsername };
  }

  return {
    evaluateBinding,
    normalizeUsername,
    visibleAccountUsernames,
  };
});
