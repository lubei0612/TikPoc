from tikpoc import runner


def test_event_driven_worker_waits_when_task_queue_is_temporarily_empty() -> None:
    assert runner.should_wait_when_idle(event_driven=True, control="running") is True


def test_batch_worker_exits_when_task_queue_is_empty() -> None:
    assert runner.should_wait_when_idle(event_driven=False, control="running") is False


def test_create_driver_sets_a_hard_http_command_timeout(monkeypatch) -> None:
    captured = {}

    class FakeDriver:
        def update_settings(self, settings) -> None:
            captured["settings"] = settings

    def fake_remote(command_executor, *, options, client_config):
        captured["command_executor"] = command_executor
        captured["client_config"] = client_config
        return FakeDriver()

    monkeypatch.setattr(runner.webdriver, "Remote", fake_remote)

    runner.create_driver("http://127.0.0.1:4723", "phone-01", command_timeout=17)

    assert captured["command_executor"] == "http://127.0.0.1:4723"
    assert captured["client_config"].timeout == 17
    assert captured["settings"] == {"waitForIdleTimeout": 0}
