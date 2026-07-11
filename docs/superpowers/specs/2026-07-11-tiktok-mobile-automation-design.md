# TikTok Mobile Automation Design

## 1. Goal

Build a single-operator, CSV-driven Android automation service for authorized TikTok test accounts. The first release runs on one Android Studio emulator on an Apple M1 Mac and can later move to an ADB-accessible cloud phone without rewriting task and rule logic.

The service visits target profiles, reads profile metrics, evaluates configurable rules, optionally opens a random eligible post, applies hourly interaction quotas, records outcomes, and continues from durable state after an app or process restart.

## 2. Initial Scope

### Included

- One Android emulator and one TikTok session.
- CSV import with validation and duplicate handling.
- Profile navigation by username or profile URL.
- Reading following, follower, and visible post counts.
- Rule: following count is greater than follower count and visible post count is greater than three.
- Random selection among visible eligible posts.
- Separately configurable hourly limits and probabilities for like, favorite, and share test actions.
- Per-target results, screenshots on failure, structured logs, and SQLite persistence.
- App health checks, recovery actions, and a scheduled TikTok restart every hour.
- Resume from the last nonterminal task after process or emulator restart.

### Excluded From The First Release

- Multiple devices or accounts running concurrently.
- Account creation, login automation, CAPTCHA handling, or identity verification.
- Commenting, following, direct messaging, posting, or live-stream automation.
- Proxy rotation, device fingerprint manipulation, or platform-detection bypass.
- Web dashboard. Configuration and status are initially command-line based.

## 3. Development Environment

- Host: Apple M1, 16 GB RAM, macOS 26.5.
- Emulator: Android Studio AVD, Pixel 7 or Pixel 8 profile.
- System image: Android 14 ARM64 image with Google Play.
- AVD allocation: 4 CPU cores, 4 GB RAM, at least 32 GB storage.
- Automation: Appium 2 with the official UiAutomator2 driver.
- Language: Python 3.12 or the newest locally supported Python 3.x version compatible with all pinned dependencies.
- State store: SQLite.
- Configuration: YAML.
- Test runner: pytest.

Android Studio Emulator is the baseline because its ADB and UiAutomator2 behavior is standard. MuMu on macOS is a secondary compatibility environment only if the TikTok package cannot be installed or operated on the official ARM64 AVD.

## 4. Input Contract

The initial CSV format is UTF-8 with a header row:

```csv
target_id,username,profile_url,enabled,notes
001,sample_user,https://www.tiktok.com/@sample_user,true,first batch
```

Rules:

- `target_id` is required and unique within the file.
- At least one of `username` or `profile_url` is required.
- `enabled` defaults to `true` when omitted.
- Blank rows are ignored.
- Duplicate `target_id` values fail import with line-numbered errors.
- Duplicate normalized usernames are imported once and reported as skipped duplicates.
- A row already completed for the same import batch is not repeated unless an explicit retry command is used.

## 5. Configuration Contract

Configuration is stored in `config/settings.yaml`. Secrets and account credentials are not stored in this file.

```yaml
device:
  udid: emulator-5554
  app_package: com.zhiliaoapp.musically
  app_activity: null

rules:
  following_greater_than_followers: true
  minimum_post_count_exclusive: 3

interaction:
  like:
    enabled: false
    probability: 0.0
    hourly_limit: 0
  favorite:
    enabled: false
    probability: 0.0
    hourly_limit: 0
  share:
    enabled: false
    probability: 0.0
    hourly_limit: 0

runtime:
  task_timeout_seconds: 120
  restart_app_every_minutes: 60
  max_task_retries: 2
  screenshot_on_failure: true
```

Interaction defaults are disabled. Probabilities must be in `[0.0, 1.0]`; hourly limits must be nonnegative integers. Invalid configuration prevents startup.

## 6. Architecture

### CLI

Provides commands to validate/import a CSV, start or stop the worker, inspect status, and retry failed tasks. It contains no TikTok UI logic.

### Import Service

Parses CSV through a structured CSV library, normalizes identifiers, reports row-level validation errors, creates an immutable import batch, and inserts pending tasks into SQLite.

### Task Repository

Owns SQLite access and transactional task claims. A task has these states:

`pending -> running -> completed | skipped | retry_wait | failed`

At startup, stale `running` tasks are returned to `retry_wait`. Each transition records a timestamp and reason.

### TikTok Driver

Encapsulates Appium selectors and gestures. Its public operations are limited to:

- ensure the app is ready;
- open a profile;
- read normalized profile metrics;
- list visible post candidates;
- open a selected post;
- inspect and perform a configured test action;
- return to a known navigation state;
- restart the app.

Selectors prefer accessibility identifiers, resource IDs, and visible text. Coordinate taps are isolated in a fallback adapter and calibrated against screen dimensions.

### Rule Engine

Consumes normalized profile metrics and configuration, returning a decision with explicit reasons. It does not access Appium or SQLite and is fully unit-testable.

### Quota Manager

Maintains separate fixed one-hour UTC quota windows for each interaction type. A test action runs only when it is enabled, its quota has capacity, and a seeded random draw is below its configured probability. The quota reservation and action result are persisted so restarts cannot reset limits or double-count an uncertain action. A reserved action with an unknown result continues to consume quota until its window expires.

### Worker

Claims one task, executes the state machine, records observations and results, then moves to the next task. Only one worker process may own the configured device. A filesystem or SQLite lease prevents accidental concurrent workers.

### Watchdog

Tracks task progress, Appium health, current package, and repeated identical screen signatures. It performs escalating recovery:

1. Retry the current UI lookup.
2. Press Back and return to a known state.
3. Force-stop and relaunch TikTok.
4. Recreate the Appium session.
5. Mark the task retryable and continue.

The scheduled hourly restart waits for the current atomic UI action to finish, persists the checkpoint, restarts TikTok, verifies the home/profile navigation baseline, and resumes the task. It does not restart the entire emulator by default.

## 7. Task Data Flow

1. Claim the oldest eligible pending or retry task.
2. Persist `running` state and attempt number.
3. Open the target profile and wait for a stable profile screen.
4. Read raw visible labels and normalize abbreviated counts such as `1.2K`.
5. Persist the raw labels, parsed metrics, and parser confidence.
6. Evaluate the configured profile rule.
7. If the rule fails, record `skipped` with the exact reason.
8. If the rule passes, collect visible post candidates and randomly choose one using a recorded random seed.
9. Open the post and evaluate each enabled test action independently through the quota manager.
10. Persist each action decision and observable result.
11. Return to a known state and mark the task `completed`.
12. On recoverable failure, checkpoint and retry up to the configured maximum; otherwise mark `failed` and continue.

## 8. UI And Parsing Reliability

TikTok UI variants are expected. The driver therefore separates semantic screen operations from version-specific selector sets. A selector set includes the TikTok version, locale, and expected screen markers.

Count parsing supports plain integers and locale-aware suffixes such as `K`, `M`, and common localized equivalents. The database stores both raw and normalized values. If any required metric is missing or ambiguous, the rule is not evaluated as true. The task enters `retry_wait` with `metrics_unreadable`; after the configured retry count is exhausted, it becomes `failed` rather than guessing or treating the rule as false.

Opening a profile succeeds only when at least two independent markers agree, such as the displayed username plus profile statistic controls. Action success is based on a visible post-action state change where available, not only on the click completing.

## 9. Persistence Model

SQLite contains at least:

- `import_batches`: source file, checksum, import time, counts.
- `targets`: normalized identity and source data.
- `tasks`: state, attempts, timestamps, checkpoint, error code.
- `profile_observations`: raw labels, parsed counts, rule decision.
- `action_attempts`: type, quota window, random draw, pre-state, result, post-state.
- `quota_windows`: interaction type, hour start, reserved count, confirmed count.
- `runtime_events`: watchdog, restart, Appium, and device events.

Database writes use transactions. Schema changes use numbered migrations.

## 10. Error Handling

Errors are classified rather than stored only as free text:

- `target_not_found`
- `profile_unavailable`
- `metrics_unreadable`
- `no_eligible_posts`
- `ui_element_missing`
- `navigation_timeout`
- `app_not_responding`
- `appium_session_lost`
- `device_offline`
- `network_unavailable`
- `unexpected_screen`

Every terminal failure stores its category, concise message, TikTok version, current package/activity, and optional screenshot/page-source artifact. Sensitive account data is excluded from logs.

## 11. Testing Strategy

### Unit Tests

- CSV validation, normalization, and duplicate rules.
- Count parsing across integer, abbreviated, and malformed values.
- Rule decisions at boundary values.
- Probability validation and deterministic seeded decisions.
- Hourly quota rollover, reservation, and crash recovery.
- Task-state transition validity.

### Component Tests

- SQLite migrations and restart recovery.
- Worker behavior with a fake TikTok driver.
- Watchdog recovery escalation with injected failures.
- Selector-set loading and screen classification using captured, sanitized page sources.

### Emulator Smoke Tests

- ADB detects the AVD.
- Appium doctor passes and creates a UiAutomator2 session.
- TikTok installs, launches, and retains a manually established login.
- Inspector can retrieve the relevant profile and post page trees.
- A target profile can be opened and its three required metrics parsed.
- App restart preserves the checkpoint and resumes processing.

### Endurance Test

Run a noninteractive or fully authorized test configuration for at least four hours. Verify no task duplication, quota reset, database corruption, unbounded memory growth, or permanent stall occurs. Then run an eight-hour test before considering cloud-phone migration.

## 12. Delivery Stages

1. Install Android Studio, SDK tools, Appium, and create the ARM64 AVD.
2. Manually install and validate TikTok, login, networking, and page-tree visibility.
3. Scaffold the Python project and SQLite migrations.
4. Implement CSV import, repository, rule engine, and quota manager with tests.
5. Implement profile navigation and metric extraction.
6. Implement post selection and disabled-by-default test action adapters.
7. Add watchdog, hourly app restart, checkpoints, and recovery.
8. Run smoke, four-hour, and eight-hour endurance tests.
9. Add an ADB device configuration abstraction and validate against one cloud phone.

## 13. Acceptance Criteria

- A valid CSV imports deterministically and invalid rows produce actionable errors.
- On a stable authorized test profile, the system records correct following, follower, and post counts.
- The rule boundary is exact: `following > followers` and `posts > 3`.
- A qualifying task records the selected post and every interaction decision, including quota or probability skips.
- No configured hourly limit can be exceeded across application or worker restarts.
- A scheduled hourly TikTok restart does not lose or duplicate the current task.
- A stuck screen triggers recovery and cannot block later tasks indefinitely.
- Process restart resumes incomplete work from SQLite.
- The same core worker can target a future ADB cloud phone by changing device configuration rather than business logic.
