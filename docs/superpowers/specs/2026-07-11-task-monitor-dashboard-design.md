# Task Monitor Dashboard Design

## Goal

Provide a local browser dashboard that stays beside the Android emulator and shows the progress and health of the single-device TikTok profile-processing worker. Closing the browser must not stop the worker.

## Scope

- Serve only on `127.0.0.1:8765`.
- Read task progress from `data/tasks.db`.
- Refresh automatically every two seconds.
- Show total, pending, running, completed, skipped, retry-wait, and failed counts.
- Show completion percentage, average throughput, runtime, and estimated remaining time.
- Show the current target username, checkpoint, attempt count, and last update time.
- Show the ten most recently updated tasks.
- Show health for the SQLite database, Appium server, Android device, TikTok package, and configured emulator proxy.
- Provide pause, resume, and stop commands through a durable worker-control record.
- Keep the dashboard visually compact enough to sit beside the emulator.

## Excluded

- Internet exposure, authentication, remote access, or cloud hosting.
- Editing task results or deleting tasks.
- Starting multiple workers or devices.
- Displaying TikTok passwords, email addresses, tokens, proxy subscription URLs, or comment text.
- Android overlay permissions or UI embedded inside TikTok.

## Architecture

### Dashboard Server

A small Python HTTP service owns no worker process. It reads SQLite through repository methods and returns JSON endpoints plus static HTML/CSS/JavaScript. It binds only to loopback and uses no external frontend build system.

### Worker Control

SQLite contains one `worker_control` row with a requested state: `running`, `paused`, or `stopped`. The worker checks this state between tasks and before scheduled app restarts. Pause never interrupts an atomic UI operation; stop finishes the current operation, checkpoints it, and exits before claiming another task.

### Runtime Events

The worker records its current phase in `runtime_events`, including `worker_started`, `profile_opening`, `metrics_reading`, `rule_evaluating`, `post_opening`, `task_finished`, `app_restarting`, and `worker_stopped`. Dashboard health and recent activity use these records rather than scraping logs.

## HTTP Interface

- `GET /`: dashboard HTML.
- `GET /api/status`: aggregate counts, progress, current task, throughput, ETA, and health.
- `GET /api/recent?limit=10`: recently updated tasks.
- `POST /api/control/pause`: request pause.
- `POST /api/control/resume`: request resume.
- `POST /api/control/stop`: request graceful stop.

All other paths return `404`. POST endpoints accept no arbitrary SQL parameters or filesystem paths.

## Progress Semantics

- `total` is all tasks in the imported batch.
- `processed` is `completed + skipped + failed`.
- `active` is `running + retry_wait`.
- Completion percentage is `processed / total * 100`.
- Throughput is processed tasks divided by elapsed worker runtime.
- ETA is remaining claimable tasks divided by throughput and is omitted until at least one task is processed.
- A retry-wait task is not considered processed.

## Visual Layout

The page uses a restrained light operational interface:

- Header: worker state, elapsed time, and compact pause/resume/stop controls.
- Primary row: large completion percentage, progress bar, processed/total count, throughput, and ETA.
- Status grid: success, skipped, retry, failed, pending, and running counts.
- Current task band: username, phase, attempt, checkpoint, and last update.
- Health row: database, Appium, emulator, TikTok, and proxy indicators.
- Recent activity table: time, username, result, attempts, and error code.

Cards use no more than an 8 px radius. Colors are semantic: green for completed/healthy, neutral gray for pending, amber for retry/paused, and red for failed/offline. The layout fits a window around 440-520 px wide without horizontal scrolling.

## Error Handling

- If SQLite is unavailable, return `503` JSON and show a persistent database error state.
- If Appium or ADB health checks time out, mark the component offline without blocking the response.
- If no worker has started, show `Idle` rather than fabricating runtime or ETA.
- Invalid control transitions return `409` with the current state.
- Browser fetch failures retain the last good data and show a stale-data indicator.

## Testing

- Repository tests for aggregate counts, recent tasks, worker-control transitions, and runtime events.
- API tests for status JSON, recent results, control commands, invalid routes, and unavailable databases.
- Frontend smoke test for progress rendering, automatic refresh, stale state, and button enablement.
- Live test against the existing 326-task database while the worker is stopped and while it processes a small batch.

## Acceptance Criteria

- The dashboard opens at `http://127.0.0.1:8765` and shows the current SQLite counts within two seconds.
- It remains responsive while Appium is busy.
- Pause prevents the worker from claiming another task after its current task finishes.
- Resume continues from SQLite without duplicating completed tasks.
- Stop exits gracefully after the current atomic operation.
- Browser closure does not stop the worker.
- No sensitive account, proxy, or comment data appears in the UI or API.
