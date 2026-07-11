# Task Monitor Dashboard

Start the loopback dashboard:

```bash
uv run tikpoc dashboard --db data/tasks.db --port 8765
```

Open `http://127.0.0.1:8765`. The page refreshes every two seconds. Closing the
browser does not stop the dashboard or worker.

Start the single-emulator worker in another terminal:

```bash
uv run tikpoc run --db data/tasks.db --udid emulator-5554
```

Pause lets the current task finish and prevents the next claim. Resume continues
the queue. Stop lets the current task finish and exits before another claim. The
worker restarts TikTok once per hour while it remains active.
