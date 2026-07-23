(function exposeAutoConnect(root, factory) {
  const bindingCore = root.TikPocBindingCore ||
    (typeof require === "function" ? require("./binding-core.js") : null);
  const autoCore = root.TikPocAutoConnectCore ||
    (typeof require === "function" ? require("./auto-connect-core.js") : null);
  const api = factory(bindingCore, autoCore);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    return;
  }
  root.TikPocAutoConnect = api;
  api.startBrowserAutoConnect();
})(typeof globalThis !== "undefined" ? globalThis : this, function createAutoConnect(
  bindingCore,
  autoCore,
) {
  const SETTINGS_KEY = "tikpocSettings";
  const STATUS_KEY = "tikpocAutoConnectStatus";
  const STORAGE_KEYS = [
    SETTINGS_KEY,
    "tikpocFollowerBaselines",
    "tikpocProcessedFollowers",
    "tikpocDmBaselines",
    "tikpocDmProcessed",
    "tikpocBindingStatus",
    STATUS_KEY,
  ];
  const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8766";

  function createAutoConnector({ documentValue, storage, getBindings, now = Date.now }) {
    let queue = Promise.resolve();

    function connect() {
      const run = queue.then(connectOnce, connectOnce);
      queue = run.catch(() => {});
      return run;
    }

    async function status(state, observedUsername = "") {
      await storage.set({
        [STATUS_KEY]: {
          state,
          observedUsername,
          observedAt: now(),
        },
      });
      return state;
    }

    async function connectOnce() {
      const stored = await storage.get(STORAGE_KEYS);
      const settings = stored[SETTINGS_KEY] || {};
      if (settings.bindingMode === "manual") {
        return status("manual");
      }
      const identity = bindingCore.evaluateBinding(
        documentValue,
        settings.expectedTikTokUsername || "tikpoc_auto_probe",
      );
      if (!identity.observedUsername) {
        return status(identity.state, "");
      }
      const dashboardUrl = settings.dashboardUrl || DEFAULT_DASHBOARD_URL;
      const bindings = await getBindings(dashboardUrl);
      const resolution = autoCore.resolveAutoBinding({
        observedUsername: identity.observedUsername,
        bindings,
        settings,
      });
      if (resolution.state !== "matched") {
        return status(resolution.state, identity.observedUsername);
      }
      if (autoCore.needsBindingUpdate(settings, resolution.binding)) {
        await storage.set({
          ...autoCore.autoBindingStorageUpdate(
            stored,
            resolution.binding,
            identity.observedUsername,
            now(),
          ),
          [STATUS_KEY]: {
            state: "matched",
            observedUsername: identity.observedUsername,
            observedAt: now(),
          },
        });
      } else {
        await status("matched", identity.observedUsername);
      }
      return "matched";
    }

    return { connect };
  }

  function startBrowserAutoConnect() {
    if (!globalThis.chrome || !globalThis.document || !bindingCore || !autoCore) {
      return;
    }
    const storage = {
      get(keys) {
        return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
      },
      set(update) {
        return new Promise((resolve) => chrome.storage.local.set(update, resolve));
      },
    };
    const getBindings = (dashboardUrl) => new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { type: "TIKPOC_GET_BINDINGS", dashboardUrl },
        (response) => {
          if (chrome.runtime.lastError || !response || !response.ok) {
            reject(new Error(
              response && response.error ||
              chrome.runtime.lastError && chrome.runtime.lastError.message ||
              "Browser binding request failed",
            ));
            return;
          }
          resolve(Array.isArray(response.result && response.result.accounts)
            ? response.result.accounts
            : []);
        },
      );
    });
    const connector = createAutoConnector({
      documentValue: document,
      storage,
      getBindings,
    });
    let timer = null;
    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(() => connector.connect().catch(() => {}), 250);
    }
    new MutationObserver(schedule).observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
    chrome.storage.onChanged.addListener((changes) => {
      if (changes && changes[SETTINGS_KEY]) {
        schedule();
      }
    });
    schedule();
  }

  return {
    createAutoConnector,
    startBrowserAutoConnect,
  };
});
