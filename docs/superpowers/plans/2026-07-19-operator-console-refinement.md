# Operator Console Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the Chinese operations console around rolling pacing, device health, coverage, and capacity without clipping or wasted space.

**Architecture:** Keep existing React views and API boundaries. Replace the operations composition and quota contract in focused components, then verify responsive layout with Playwright screenshots.

**Tech Stack:** React, TypeScript, Vite, CSS, Vitest, Playwright.

---

### Task 1: Rolling Quota Contract

**Files:** `operator-console/src/api.ts`, `operator-console/src/components/QuotaTable.tsx`, `operator-console/src/OperationsView.test.tsx`

- [ ] Add failing tests for rolling usage, readiness, next due time, candidate weight, and Chinese labels.
- [ ] Update the TypeScript contract and render a compact six-column pacing table.
- [ ] Run Vitest and commit `feat: show rolling action pacing`.

### Task 2: Dense Operations Composition

**Files:** `operator-console/src/views/OperationsView.tsx`, `operator-console/src/styles.css`, `operator-console/src/components/DeviceTable.tsx`, `operator-console/src/OperationsView.test.tsx`

- [ ] Add tests for capacity KPIs and stable section order.
- [ ] Recompose KPI, command, device, pacing/account, coverage, and evidence bands.
- [ ] Remove overlay positioning and constrain every table with stable responsive tracks.
- [ ] Run Vitest/build and commit `refactor: clarify operations workspace`.

### Task 3: Desktop, Mobile, And Long-Screenshot QA

**Files:** `tests/e2e/operator-console.spec.ts`, `operator-console/src/styles.css`, `src/tikpoc/static/console/*`

- [ ] Extend fixtures with rolling quota fields and assert no viewport overflow or element overlap at 1440x1000, 1920x1080, and 390x844.
- [ ] Capture a full-page desktop screenshot and inspect all section boundaries and table rows.
- [ ] Fix every clipping/overlap issue, rebuild embedded assets, run Vitest/build/E2E, and commit `fix: polish console responsive layout`.

