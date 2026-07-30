# Live Audience Quality Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Preserve every collected public LIVE event while submitting only deterministic A/B audience identities to TikPoc live-interrupt batches.

**Architecture:** The external `followers` collector owns event aggregation and a pure quality classifier. Its analytical CSV keeps A/B/C rows, while its atomic TikPoc JSONL export contains only A/B rows with evidence metadata. TikPoc defensively validates new quality fields while retaining compatibility with legacy files that omit them.

**Tech Stack:** Python 3, SQLite, JSONL, `unittest`/pytest, TikPoc's existing priority importer.

---

### Task 1: Deterministic collector classification

**Files:**
- Modify: `/Users/chenyuqi/Desktop/followers/live_audience_collector.py`
- Test: `/Users/chenyuqi/Desktop/followers/tests/test_live_audience.py`

- [x] **Step 1: Write failing classifier tests**

Add tests importing `classify_audience_row` and asserting:

```python
self.assertEqual(classify_audience_row({"event_types": "comment;join", "rooms": "one"}), ("A", ("comment",)))
self.assertEqual(classify_audience_row({"event_types": "join;like", "rooms": "one"}), ("B", ("like", "multiple_event_types")))
self.assertEqual(classify_audience_row({"event_types": "join", "rooms": "one;two"}), ("B", ("multiple_rooms",)))
self.assertEqual(classify_audience_row({"event_types": "join", "rooms": "one"}), ("C", ("join_only",)))
```

- [x] **Step 2: Run the focused test and observe the missing import failure**

Run:

```bash
cd /Users/chenyuqi/Desktop/followers
.venv/bin/pytest tests/test_live_audience.py -q
```

Expected: collection fails because `classify_audience_row` is absent.

- [x] **Step 3: Add the pure classifier**

Implement a deterministic function using sets of semicolon-delimited events and rooms. A-level reasons are sorted from `comment`, `follow`, `gift`, `share`; B-level reasons are `like`, `multiple_event_types`, and `multiple_rooms`; otherwise return C with `join_only`.

- [x] **Step 4: Run focused tests**

Run the command from Step 2. Expected: PASS.

### Task 2: Raw CSV retention and A/B-only atomic JSONL

**Files:**
- Modify: `/Users/chenyuqi/Desktop/followers/live_audience_collector.py`
- Test: `/Users/chenyuqi/Desktop/followers/tests/test_live_audience.py`

- [x] **Step 1: Write failing export tests**

Create one A row, one B row, one single-room join-only C row, and one A row without a username. Assert that:

```python
self.assertEqual(store.export_tikpoc_jsonl(output, source_id="live-1"), 2)
self.assertEqual([row["lead_level"] for row in jsonl_rows], ["A", "B"])
self.assertEqual(jsonl_rows[0]["qualification_reasons"], ["comment"])
self.assertEqual({row["lead_level"] for row in csv_rows}, {"A", "B", "C"})
```

Also assert the temporary export path is absent after success.

- [x] **Step 2: Run the focused tests and observe join-only export failure**

Run:

```bash
cd /Users/chenyuqi/Desktop/followers
.venv/bin/pytest tests/test_live_audience.py -q
```

Expected: FAIL because the exporter still writes C-level rows and CSV lacks quality fields.

- [x] **Step 3: Implement classified exports**

Have `rows()` remain raw. Add an internal classified-row projection used by both exporters. `export_csv` appends `lead_level` and a semicolon-delimited `qualification_reasons`. `export_tikpoc_jsonl` filters to A/B plus a nonempty username and writes the JSON-array reasons before atomically replacing the destination.

- [x] **Step 4: Add completion counts**

At collector shutdown, derive A/B/C counts and print one line in this exact shape:

```text
QUALITY A=<count> B=<count> C=<count> eligible=<count>
```

- [x] **Step 5: Run the complete followers suite**

Run:

```bash
cd /Users/chenyuqi/Desktop/followers
.venv/bin/pytest -q
```

Expected: all tests pass.

### Task 3: Defensive TikPoc handoff validation

**Files:**
- Modify: `src/tikpoc/priority_importer.py`
- Test: `tests/test_priority_importer.py`

- [x] **Step 1: Write failing contract tests**

Add tests showing that `source_type="live_audience"` with `lead_level="A"` and a nonempty string list of reasons is accepted; explicit level C is rejected; malformed or empty reasons are rejected; and a legacy row with no quality fields remains accepted.

- [x] **Step 2: Run focused TikPoc tests and observe failure**

Run:

```bash
uv run pytest tests/test_priority_importer.py -q
```

Expected: A-level collector row fails because `live_audience` and quality metadata are not part of the importer contract.

- [x] **Step 3: Implement minimal validation**

Extend supported source types with `live_audience`. When `lead_level` is present, require `A` or `B`; require `qualification_reasons` to be a nonempty array of nonempty strings from `comment`, `follow`, `gift`, `share`, `like`, `multiple_event_types`, and `multiple_rooms`. Reject reasons without a level and a level without reasons. Leave rows with neither field unchanged.

- [x] **Step 4: Run focused tests**

Run the command from Step 2. Expected: PASS.

- [x] **Step 5: Commit the tracked TikPoc implementation**

```bash
git add src/tikpoc/priority_importer.py tests/test_priority_importer.py
git commit -m "feat: validate live audience quality metadata"
```

Do not stage `src/tikpoc/runner.py` or `build/`.

### Task 4: Publish the handoff contract and verify end to end

**Files:**
- Modify: `/Users/chenyuqi/Desktop/followers/TIKPOC_HANDOFF.md`
- Modify: `docs/priority-live-batch-cli.md`
- Modify: `docs/tikpoc-business-logic.md`

- [x] **Step 1: Document A/B/C and metadata**

Document the exact classifier, A/B-only handoff, raw C retention, JSON field types, and the fact that APK navigation and interaction rules are unchanged.

- [x] **Step 2: Export a fixture from a temporary collector database**

Create A, B, and C identities with `AudienceStore`, export JSONL, and parse it with `read_priority_targets`. Expected: two accepted targets and no C-level row in the JSONL.

- [x] **Step 3: Run verification**

```bash
cd /Users/chenyuqi/Desktop/followers && .venv/bin/pytest -q
cd /Users/chenyuqi/.config/superpowers/worktrees/tik/web-lead-conversion
uv run pytest tests/test_priority_importer.py tests/test_priority_cli.py tests/test_live_batch_service.py -q
uv run pytest -q
uv tool run ruff check src/tikpoc/priority_importer.py tests/test_priority_importer.py
uv tool run ruff format --check src/tikpoc/priority_importer.py tests/test_priority_importer.py
git diff --check
```

Expected: every test and touched-file check passes. Repo-wide Ruff findings outside touched files remain reported separately.

- [x] **Step 4: Commit tracked documentation**

```bash
git add docs/priority-live-batch-cli.md docs/tikpoc-business-logic.md
git commit -m "docs: publish qualified live audience handoff"
```

- [x] **Step 5: Record the external collector checksum and push**

Record the SHA-256 of `live_audience_collector.py` and its test file in the runbook so the deployed collector version is auditable, then push `feat/web-lead-conversion` to its configured remote. Never include `.env`, proxies, credentials, SQLite files, or audience exports.
