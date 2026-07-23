# TikTok Developer Public Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a public IKUN Product Publisher website with visible Terms of Service, Privacy Policy, and TikTok OAuth callback URLs that can be entered in TikTok for Developers.

**Architecture:** A dependency-light static site lives in `tiktok-developer-site/` and is deployed directly by Vercel. Shared CSS and JavaScript support four public routes, while a Node test validates required links, policy text, OAuth callback behavior, and Vercel clean-route configuration.

**Tech Stack:** Semantic HTML, CSS, browser JavaScript, Node test runner, Vercel CLI.

---

### Task 1: Public Site Contract Tests

**Files:**
- Create: `tiktok-developer-site/tests/site.test.js`
- Create: `tiktok-developer-site/package.json`

- [ ] **Step 1: Write the failing static-site contract test**

Create a Node test that reads the future site files and asserts:

```js
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
});
```

Create `package.json` with:

```json
{
  "name": "ikun-product-publisher-site",
  "private": true,
  "scripts": { "test": "node --test tests/*.test.js" }
}
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd tiktok-developer-site && npm test
```

Expected: failure because the public HTML and Vercel files do not exist.

### Task 2: Website, Policy, and Callback Pages

**Files:**
- Create: `tiktok-developer-site/index.html`
- Create: `tiktok-developer-site/privacy.html`
- Create: `tiktok-developer-site/terms.html`
- Create: `tiktok-developer-site/oauth/callback.html`
- Create: `tiktok-developer-site/styles.css`
- Create: `tiktok-developer-site/app.js`
- Create: `tiktok-developer-site/vercel.json`

- [ ] **Step 1: Implement the shared public shell**

All pages use UTF-8, responsive viewport metadata, `styles.css`, and a visible
header/footer containing links to `/`, `/privacy`, and `/terms`. The homepage
describes the actual catalog-review-publish workflow, explicit publish approval,
account disconnect support, contact route, and current controlled beta status.

- [ ] **Step 2: Implement the Privacy Policy**

The policy states the categories and purpose of data processing: TikTok basic
profile identity, OAuth access and refresh tokens, selected product images,
user-reviewed captions, publishing jobs, and redacted publishing results. It
states that tokens remain server-side, are not sold, can be revoked by account
disconnect, and are deleted after account removal subject to operational logs.

- [ ] **Step 3: Implement the Terms of Service**

The terms define user-reviewed publishing, account ownership, permitted catalog
content, factual caption responsibility, explicit approval before publishing,
service availability, account disconnection, and contact details without adding
supplier contacts or private channel destinations.

- [ ] **Step 4: Implement the OAuth callback page**

`app.js` reads only `code`, `state`, `error`, and `error_description` query
parameters, renders a success or error result, and removes query parameters from
the visible address using `history.replaceState`. It does not log or persist the
authorization code.

- [ ] **Step 5: Configure Vercel routes and security headers**

Create:

```json
{
  "cleanUrls": true,
  "trailingSlash": false,
  "rewrites": [
    { "source": "/privacy", "destination": "/privacy.html" },
    { "source": "/terms", "destination": "/terms.html" },
    { "source": "/oauth/callback", "destination": "/oauth/callback.html" }
  ],
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
        { "key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()" }
      ]
    }
  ]
}
```

- [ ] **Step 6: Run tests and verify GREEN**

Run:

```bash
cd tiktok-developer-site && npm test
```

Expected: all static-site contract tests pass.

- [ ] **Step 7: Commit the website**

```bash
git add tiktok-developer-site
git commit -m "feat: add TikTok developer public site"
```

### Task 3: Vercel Production Deployment

**Files:**
- Modify: `.gitignore` only if Vercel creates an unignored `.vercel/` directory
- Modify: `docs/web-engagement-runbook.md`

- [ ] **Step 1: Confirm Vercel authentication**

Run:

```bash
npx --yes vercel whoami
```

Expected: the authenticated Vercel account name. If authentication is absent,
run `npx --yes vercel login` and complete the Vercel browser/email flow.

- [ ] **Step 2: Deploy production**

Run:

```bash
cd tiktok-developer-site
npx --yes vercel --prod --yes
```

Record the returned HTTPS production origin without recording Vercel tokens.

- [ ] **Step 3: Verify all public routes**

Run HTTPS requests for `/`, `/privacy`, `/terms`, and `/oauth/callback`, require
HTTP 200, verify the expected page titles, and confirm the three security headers.

- [ ] **Step 4: Document TikTok Developer values**

Add the deployed public origin and the exact website, privacy, terms, and OAuth
callback paths to the runbook. Do not include Client Secret, OAuth tokens, or the
authorization code.

- [ ] **Step 5: Commit deployment documentation**

```bash
git add .gitignore docs/web-engagement-runbook.md
git commit -m "docs: record TikTok developer public URLs"
```

### Task 4: Final Verification

- [ ] **Step 1: Run local verification**

```bash
cd tiktok-developer-site && npm test
cd .. && git diff --check
```

- [ ] **Step 2: Run production verification**

Require all four production URLs to return HTTPS 200 and confirm that Privacy
Policy and Terms of Service are directly linked from the homepage without login.

- [ ] **Step 3: Report the exact TikTok fields**

Return the actual HTTPS production origin discovered from the Vercel deployment,
then list its `/`, `/terms`, `/privacy`, and `/oauth/callback` values with
`Platform: Web`. Do not substitute a preview origin or an unverified custom
domain.

TikTok submission remains in Draft until Login Kit, Content Posting API, scopes,
the real OAuth exchange, and the end-to-end demo video are complete.
