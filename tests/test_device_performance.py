from tikpoc.device_performance import (
    DevicePerformanceSnapshot,
    MeasuredAppiumDriver,
)


class SteppingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


class FakeExecutor:
    def execute(self, command: str, params: dict[str, object]):
        values = {
            "getPageSource": {"value": "<hierarchy/>"},
            "findElements": {"value": ["node"]},
            "executeScript": {"value": "ok"},
        }
        return values[command]


class FakeDriver:
    def __init__(self) -> None:
        self.command_executor = FakeExecutor()

    @property
    def page_source(self) -> str:
        return str(self.command_executor.execute("getPageSource", {})["value"])

    def find_elements(self, by: str, value: str) -> list[str]:
        return list(
            self.command_executor.execute(
                "findElements", {"using": by, "value": value}
            )["value"]
        )

    def execute_script(self, script: str, params: dict[str, object]) -> str:
        return str(
            self.command_executor.execute(
                "executeScript", {"script": script, "args": [params]}
            )["value"]
        )


def test_measured_driver_preserves_results_and_records_commands() -> None:
    driver = MeasuredAppiumDriver(FakeDriver(), clock=SteppingClock())

    assert driver.page_source == "<hierarchy/>"
    assert driver.find_elements("xpath", "//*") == ["node"]
    assert driver.execute_script("mobile: deepLink", {"url": "TARGET"}) == "ok"

    snapshot = driver.performance_snapshot()
    assert snapshot.command_count == 3
    assert snapshot.page_source_reads == 1
    assert snapshot.element_queries == 1
    assert snapshot.execute_script_calls == 1
    assert snapshot.command_duration_ms == 300


def test_command_snapshot_subtraction_returns_nonnegative_delta() -> None:
    before = DevicePerformanceSnapshot(2, 200, 1, 1, 0)
    after = DevicePerformanceSnapshot(5, 650, 2, 2, 1)

    assert after - before == DevicePerformanceSnapshot(3, 450, 1, 1, 1)


def test_failed_command_is_measured_and_original_error_is_preserved() -> None:
    class FailingExecutor(FakeExecutor):
        def execute(self, command: str, params: dict[str, object]):
            raise RuntimeError("rpc failed")

    raw = FakeDriver()
    raw.command_executor = FailingExecutor()
    driver = MeasuredAppiumDriver(raw, clock=SteppingClock())

    try:
        _ = driver.page_source
    except RuntimeError as error:
        assert str(error) == "rpc failed"
    else:
        raise AssertionError("expected the original command error")

    assert driver.performance_snapshot() == DevicePerformanceSnapshot(
        command_count=1,
        command_duration_ms=100,
        page_source_reads=1,
        element_queries=0,
        execute_script_calls=0,
    )
