const test = require("node:test");
const assert = require("node:assert/strict");

const binding = require("./binding-core.js");

function link(href, visible = true) {
  return {
    href,
    getClientRects() { return visible ? [{}] : []; },
  };
}

function page(links = [], text = "") {
  return {
    body: { textContent: text },
    querySelectorAll() { return links; },
  };
}

test("normalizes configured and visible TikTok usernames", () => {
  assert.equal(binding.normalizeUsername(" @Shop.One "), "shop.one");
  assert.equal(binding.normalizeUsername("not valid!"), "");
});

test("accepts duplicate visible links for the configured account", () => {
  const result = binding.evaluateBinding(
    page([
      link("https://www.tiktok.com/@Shop.One"),
      link("https://www.tiktok.com/@shop.one?lang=en"),
    ]),
    "@SHOP.ONE",
  );
  assert.deepEqual(result, { state: "ready", observedUsername: "shop.one" });
});

test("rejects ambiguous and mismatched visible account identities", () => {
  assert.deepEqual(
    binding.evaluateBinding(
      page([
        link("https://www.tiktok.com/@shop_one"),
        link("https://www.tiktok.com/@shop_two"),
      ]),
      "shop_one",
    ),
    { state: "unverified", observedUsername: "" },
  );
  assert.deepEqual(
    binding.evaluateBinding(page([link("https://www.tiktok.com/@shop_two")]), "shop_one"),
    { state: "mismatch", observedUsername: "shop_two" },
  );
});

test("distinguishes missing configuration, signed out, and verification pages", () => {
  assert.deepEqual(
    binding.evaluateBinding(page([link("https://www.tiktok.com/@shop_one")]), ""),
    { state: "unverified", observedUsername: "shop_one" },
  );
  assert.deepEqual(
    binding.evaluateBinding(page([], "Log in to TikTok"), "shop_one"),
    { state: "signed_out", observedUsername: "" },
  );
  assert.deepEqual(
    binding.evaluateBinding(page([], "Verify to continue security check"), "shop_one"),
    { state: "verification_required", observedUsername: "" },
  );
});

test("ignores hidden profile links", () => {
  assert.deepEqual(
    binding.evaluateBinding(page([link("https://www.tiktok.com/@shop_one", false)]), "shop_one"),
    { state: "unverified", observedUsername: "" },
  );
});
