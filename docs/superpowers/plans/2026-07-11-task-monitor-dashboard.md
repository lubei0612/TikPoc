# Task Monitor Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loopback-only browser dashboard that monitors SQLite task progress and sends durable pause, resume, and stop requests to the single-device worker.

**Architecture:** Extend the SQLite repository with dashboard queries and a single worker-control row. Serve a static compact UI and JSON API from Python's standard HTTP server, while health probes use bounded local HTTP and ADB subprocess calls.

**Tech Stack:** Python 3.14, SQLite, standard-library HTTP server, HTML/CSS/JavaScript, pytest.

---

### Task 1: Dashboard Repository Queries

**Files:**
- Modify: `src/tikpoc/db.py`
- Create: `tests/test_dashboard_repository.py`

- [ ] Write failing tests for task totals, recent tasks, current task, worker-control transitions, and runtime events.
- [ ] Run `uv run pytest tests/test_dashboard_repository.py -v` and verify missing methods fail.
- [ ] Add schema tables `worker_control` and `runtime_events`, plus typed query methods.
- [ ] Run the focused test and full suite.
- [ ] Commit with `git commit -m "feat: add dashboard repository queries"`.

### Task 2: Loopback Status API

**Files:**
- Create: `src/tikpoc/dashboard.py`
- Create: `tests/test_dashboard_api.py`

- [ ] Write failing HTTP handler tests for `/api/status`, `/api/recent`, control commands, `404`, and invalid transitions.
- [ ] Run the focused tests and verify import/behavior failures.
- [ ] Implement JSON serialization, bounded health probes, and loopback HTTP serving.
- [ ] Run focused and full tests.
- [ ] Commit with `git commit -m "feat: add task monitor api"`.

### Task 3: Compact Browser UI

**Files:**
- Create: `src/tikpoc/static/dashboard.html`
- Create: `src/tikpoc/static/dashboard.css`
- Create: `src/tikpoc/static/dashboard.js`
- Modify: `src/tikpoc/dashboard.py`
- Create: `tests/test_dashboard_static.py`

- [ ] Write failing tests for static routes and required progress/control DOM elements.
- [ ] Run the focused tests and verify static assets are missing.
- [ ] Implement the 440-520 px operational layout, semantic states, two-second refresh, stale indicator, and control buttons.
- [ ] Run focused and full tests.
- [ ] Commit with `git commit -m "feat: add task monitor interface"`.

### Task 4: Worker Control Integration And Live Verification

**Files:**
- Modify: `src/tikpoc/worker.py`
- Modify: `src/tikpoc/cli.py`
- Create: `tests/test_worker_control.py`
- Create: `docs/dashboard-runbook.md`

- [ ] Write failing tests that pause prevents a claim, resume allows a claim, and stop exits before another claim.
- [ ] Run focused tests and verify current worker ignores control state.
- [ ] Integrate control checks and add `tikpoc dashboard --db data/tasks.db --port 8765`.
- [ ] Run all tests, start the dashboard, verify `/api/status`, and open `http://127.0.0.1:8765`.
- [ ] Continue the existing real task batch and verify the dashboard updates.
- [ ] Commit with `git commit -m "feat: integrate dashboard worker controls"`.
