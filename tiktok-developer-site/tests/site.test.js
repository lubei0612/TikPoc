const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("public pages expose visible policy and OAuth routes", () => {
  const home = read("index.html");
  const privacy = read("privacy.html");
  const terms = read("terms.html");
  const callback = read("oauth/callback.html");
  assert.match(home, /IKUN Product Publisher/);
  assert.match(home, /href="\/privacy"/);
  assert.match(home, /href="\/terms"/);
  assert.match(privacy, /OAuth access and refresh tokens/);
  assert.match(privacy, /disconnect/i);
  assert.match(terms, /user-reviewed publishing/i);
  assert.match(callback, /OAuth authorization result/);
});

test("vercel exposes clean public routes", () => {
  const config = JSON.parse(read("vercel.json"));
  assert.deepEqual(config.rewrites, [
    { source: "/privacy", destination: "/privacy.html" },
    { source: "/terms", destination: "/terms.html" },
    { source: "/oauth/callback", destination: "/oauth/callback.html" },
  ]);
  const headers = config.headers[0].headers;
  assert.deepEqual(headers, [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=()",
    },
  ]);
});

test("OAuth callback handles a minimal allowlist and clears its query", () => {
  const script = read("app.js");
  assert.match(script, /get\("code"\)/);
  assert.match(script, /get\("state"\)/);
  assert.match(script, /get\("error"\)/);
  assert.match(script, /get\("error_description"\)/);
  assert.match(script, /history\.replaceState/);
  assert.doesNotMatch(script, /console\.(log|debug|info)/);
  assert.doesNotMatch(script, /localStorage|sessionStorage/);
});
