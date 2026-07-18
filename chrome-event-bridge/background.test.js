const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("browser event transport sends JSON without setting Origin", async () => {
  let messageListener;
  let request;
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            messageListener = listener;
          },
        },
        onInstalled: { addListener() {} },
        openOptionsPage() {},
      },
    },
    async fetch(url, options) {
      request = { url, options };
      return {
        ok: true,
        async json() {
          return { accepted: true };
        },
      };
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "background.js"), "utf8");
  vm.runInNewContext(source, context);

  const response = await new Promise((resolve) => {
    const asynchronous = messageListener(
      {
        type: "TIKPOC_REPORT_EVENT",
        dashboardUrl: "http://127.0.0.1:8766",
        event: { account_id: "account-01" },
      },
      {},
      resolve,
    );
    assert.equal(asynchronous, true);
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.accepted, true);
  assert.equal(request.url, "http://127.0.0.1:8766/api/browser-events");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(Object.hasOwn(request.options.headers, "Origin"), false);
});
