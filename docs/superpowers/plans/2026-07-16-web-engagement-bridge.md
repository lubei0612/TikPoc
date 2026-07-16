# TikTok Web Engagement Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an event-driven computer-side follow-back and direct-message service that does not interrupt the Android trace workers.

**Architecture:** TikTok Business Messaging Webhooks feed a durable SQLite web-event queue consumed by an independent AI/API worker. A small Manifest V3 Chrome extension observes TikTok Web activity rows and performs semantic inline follow-back clicks, then reports sanitized results to the same dashboard.

**Tech Stack:** Python 3.12 standard library, SQLite, PyYAML, pytest, Chrome Manifest V3, JavaScript `MutationObserver`, Node built-in test runner.

---

## File Map

- `src/tikpoc/webhooks.py`: TikTok signature verification and inbound event parsing.
- `src/tikpoc/business_messaging.py`: token store and official message API client.
- `src/tikpoc/web_accounts.py`: validated nonsecret YAML account registry.
- `src/tikpoc/web_worker.py`: independent AI reply event processor.
- `src/tikpoc/db.py`: web event queue and conversation history persistence.
- `src/tikpoc/dashboard.py`: TikTok webhook and Chrome event HTTP endpoints.
- `src/tikpoc/cli.py`: `web-worker` command and dashboard account-registry option.
- `chrome-event-bridge/`: production Chrome follower bridge.
- `config/web-accounts.example.yaml`: configuration example.
- `tests/`: Python unit and component coverage.

## Task 1: Webhook Verification And Parsing

- [ ] Write failing tests for valid, invalid, malformed, and stale signatures.
- [ ] Run the focused tests and confirm they fail because `tikpoc.webhooks` is absent.
- [ ] Implement exact-body HMAC-SHA256 verification with constant-time comparison.
- [ ] Write failing tests for `im_receive_msg`, `im_receive_msg_eu`, and ignored events.
- [ ] Implement immutable parsed inbound-message records.
- [ ] Run focused tests until green.

## Task 2: Durable Web Events And Conversation History

- [ ] Write failing database tests for event deduplication, account-scoped claims,
      retries, and ordered conversation history.
- [ ] Add additive SQLite migrations for `web_events`, `web_conversations`, and
      `web_messages`.
- [ ] Implement transactional queue and history methods.
- [ ] Run focused and full database tests.

## Task 3: Business Account Registry And Token Store

- [ ] Write failing tests for YAML validation and business-ID lookup.
- [ ] Implement the registry with explicit duplicate and missing-field errors.
- [ ] Write failing tests for token expiry and atomic chmod-0600 persistence.
- [ ] Implement the token file store without logging secret values.
- [ ] Add `config/web-accounts.example.yaml`.

## Task 4: Official Message API Client

- [ ] Write failing tests that inspect typing, mark-read, text-send, and refresh
      requests through an injected opener.
- [ ] Implement the API client using exact JSON request bodies and `Access-Token`.
- [ ] Retry once after an authentication failure by refreshing the token.
- [ ] Treat missing message IDs as uncertain failures.

## Task 5: Independent AI Message Worker

- [ ] Write failing tests for inbound persistence, bounded history, one reply, and
      idempotent completion.
- [ ] Extend `AiReplyClient` with a history-aware reply method and configurable
      private-channel hint.
- [ ] Implement `WebEventWorker` without Appium dependencies.
- [ ] Add the `tikpoc web-worker` CLI command.

## Task 6: Dashboard Webhook And Browser Endpoints

- [ ] Write failing HTTP tests for signature rejection, accepted DMs, duplicates,
      unknown accounts, and Chrome follower reports.
- [ ] Add registry and signing settings to `DashboardServer`.
- [ ] Implement `/api/tiktok-business/webhook` and `/api/browser-events`.
- [ ] Keep `/api/device-events` behavior unchanged.

## Task 7: Chrome Follower Bridge

- [ ] Write Node tests for follower-row classification, profile-link extraction,
      and allowed follow-button labels.
- [ ] Implement pure parser helpers.
- [ ] Add Manifest V3 content script, service worker, and options page.
- [ ] Use DOM mutation events, local deduplication, semantic button lookup, and
      post-click state verification.

## Task 8: Verification And Runbook

- [ ] Run all Python tests.
- [ ] Run Ruff checks.
- [ ] Run Node extension tests.
- [ ] Start the dashboard on an unused port and post signed synthetic webhook and
      browser events end to end.
- [ ] Document account setup, Business Messaging approval, token bootstrap,
      extension loading, and manual two-account smoke testing.
