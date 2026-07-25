import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePerformanceSnapshot:
    command_count: int = 0
    command_duration_ms: int = 0
    page_source_reads: int = 0
    element_queries: int = 0
    execute_script_calls: int = 0
    helper_command_count: int = 0
    helper_processing_ms: int = 0
    host_round_trip_ms: int = 0
    tree_age_ms: int = 0
    event_wait_ms: int = 0
    fallback_count: int = 0
    fallback_reason: str = ""

    def __sub__(
        self, previous: "DevicePerformanceSnapshot"
    ) -> "DevicePerformanceSnapshot":
        return type(self)(
            command_count=max(0, self.command_count - previous.command_count),
            command_duration_ms=max(
                0, self.command_duration_ms - previous.command_duration_ms
            ),
            page_source_reads=max(
                0, self.page_source_reads - previous.page_source_reads
            ),
            element_queries=max(0, self.element_queries - previous.element_queries),
            execute_script_calls=max(
                0, self.execute_script_calls - previous.execute_script_calls
            ),
            helper_command_count=max(
                0, self.helper_command_count - previous.helper_command_count
            ),
            helper_processing_ms=max(
                0, self.helper_processing_ms - previous.helper_processing_ms
            ),
            host_round_trip_ms=max(
                0, self.host_round_trip_ms - previous.host_round_trip_ms
            ),
            tree_age_ms=max(0, self.tree_age_ms - previous.tree_age_ms),
            event_wait_ms=max(0, self.event_wait_ms - previous.event_wait_ms),
            fallback_count=max(0, self.fallback_count - previous.fallback_count),
            fallback_reason=(
                self.fallback_reason
                if self.fallback_count > previous.fallback_count
                else ""
            ),
        )


class _MeasuredCommandExecutor:
    def __init__(
        self,
        executor: object,
        *,
        clock: Callable[[], float],
    ) -> None:
        self._executor = executor
        self._clock = clock
        self._snapshot = DevicePerformanceSnapshot()

    def execute(self, command: str, params: dict[str, object]):
        started_at = self._clock()
        try:
            return self._executor.execute(command, params)
        finally:
            elapsed_ms = max(0, round((self._clock() - started_at) * 1_000))
            current = self._snapshot
            self._snapshot = DevicePerformanceSnapshot(
                command_count=current.command_count + 1,
                command_duration_ms=current.command_duration_ms + elapsed_ms,
                page_source_reads=current.page_source_reads
                + int(command == "getPageSource"),
                element_queries=current.element_queries
                + int(command in {"findElement", "findElements"}),
                execute_script_calls=current.execute_script_calls
                + int(command in {"executeScript", "executeAsyncScript"}),
            )

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        return self._snapshot

    def __getattr__(self, name: str):
        return getattr(self._executor, name)


class MeasuredAppiumDriver:
    """Transparent Appium driver proxy backed by command-executor measurements."""

    def __init__(
        self,
        driver: object,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._driver = driver
        executor = driver.command_executor
        if isinstance(executor, _MeasuredCommandExecutor):
            self._executor = executor
        else:
            self._executor = _MeasuredCommandExecutor(executor, clock=clock)
            driver.command_executor = self._executor

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        return self._executor.performance_snapshot()

    def __getattr__(self, name: str):
        return getattr(self._driver, name)
