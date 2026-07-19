import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "./App";
import { CommandBar } from "./components/CommandBar";
import { DeviceTable } from "./components/DeviceTable";
import { OperationsView } from "./views/OperationsView";

const operationSnapshot = (state = "running") => ({
  round: {
    round_id: "round-1",
    pool_id: "pool-1",
    state,
    starts_at_ms: 1_000,
    target_count: 2,
  },
  devices: [
    {
      device_id: "phone-01",
      account_id: "account-01",
      health: "healthy",
      health_error_code: null,
      health_updated_at_ms: 4_000,
      control_state: "running",
      current_assignment: null,
      mean_ms: 5_200,
      p90_ms: 7_400,
      latest_diagnostic: null,
    },
    {
      device_id: "phone-02",
      account_id: "account-02",
      health: "degraded",
      health_error_code: "selector_missing",
      health_updated_at_ms: 4_100,
      control_state: "running",
      current_assignment: {
        assignment_id: 42,
        identity_key: "sec:buyer-2",
        phase: "action_reconciling",
        attempt_count: 2,
        last_error_code: "selector_missing",
        control_state: "running",
      },
      mean_ms: 6_100,
      p90_ms: 8_800,
      latest_diagnostic: {
        result: "uncertain",
        attempted_at_ms: 4_050,
        ui_summary: "Visible selector missing",
        screenshot_id: "shot-1",
      },
    },
  ],
  quotas: [
    {
      device_id: "phone-01",
      outcome: "like",
      limit: 100,
      reserved: 20,
      confirmed: 18,
      uncertain: 2,
      remaining: 80,
      rolling_window_started_at_ms: 1_000,
      token_ready: true,
      next_due_at_ms: 8_000,
      candidate_weight: 100,
    },
  ],
  coverage: {
    targets: 2,
    required_devices: 2,
    confirmed_visits: 3,
    completed_assignments: 2,
    fully_covered: 1,
    fully_completed: 1,
    coverage_rate: 0.5,
    completion_rate: 0.5,
  },
  recent_mobile_traces: [
    {
      identity_key: "sec:buyer-1",
      username: "buyer_1",
      confirmed_devices: 2,
      completed_devices: 2,
      required_devices: 2,
      fully_covered: true,
      fully_completed: true,
      last_visit_confirmed_at_ms: 4_200,
    },
  ],
  browser_health: [
    {
      account_id: "account-01",
      page_role: "messages",
      device_id: "phone-01",
      status: "healthy",
      observed_at_ms: 4_300,
      detail: "ready",
    },
  ],
});

const coverageSnapshot = {
  round_id: "round-1",
  items: [
    {
      identity_key: "sec:buyer-1",
      username: "buyer_1",
      devices: [
        {
          assignment_id: 41,
          device_id: "phone-01",
          phase: "completed",
          planned_outcome: "like",
          visit_confirmed: true,
          completed: true,
          duration_ms: 5_000,
          attempt_count: 1,
          next_attempt_at_ms: 0,
          last_error_code: null,
          control_state: "running",
        },
        {
          assignment_id: 42,
          device_id: "phone-02",
          phase: "deferred",
          planned_outcome: "trace_only",
          visit_confirmed: false,
          completed: false,
          duration_ms: null,
          attempt_count: 2,
          next_attempt_at_ms: 9_000,
          last_error_code: "selector_missing",
          control_state: "running",
        },
      ],
    },
  ],
  total: 1,
  offset: 0,
  limit: 100,
};

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

function mockInitialLoad() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    return jsonResponse(operationSnapshot());
  });
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

it("confirms fleet and round commands before dispatch", () => {
  const onCommand = vi.fn();
  render(<CommandBar errors={{}} onCommand={onCommand} pendingKeys={new Set()} roundState="running" />);

  fireEvent.click(screen.getByRole("button", { name: "停止设备组" }));

  expect(onCommand).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "确认停止设备组" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "确认停止设备组" }));
  expect(onCommand).toHaveBeenCalledWith("stop", "fleet");
});

it("confirms a device stop before dispatch", () => {
  const onCommand = vi.fn();
  render(<DeviceTable devices={[operationSnapshot().devices[0]]} errors={{}} onCommand={onCommand} pendingKeys={new Set()} />);

  fireEvent.click(screen.getByRole("button", { name: "停止 phone-01" }));

  expect(onCommand).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认停止 phone-01" }));
  expect(onCommand).toHaveBeenCalledWith("stop", "phone-01");
});

it("loads the operations snapshot and coverage for the selected round", async () => {
  const fetchMock = mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  expect((await screen.findAllByText("phone-01"))[0]).toBeVisible();
  expect(screen.getAllByText("buyer_1")[0]).toBeVisible();
  expect(screen.getByText("50%")).toBeVisible();
  expect(screen.getByText("滚动一小时配额")).toBeVisible();
  expect(screen.getByText("可执行")).toBeVisible();
  expect(screen.getByText("权重 100")).toBeVisible();
  expect(within(screen.getByTestId("mobile-traces")).getAllByText("2/2")[0]).toBeVisible();
  expect(within(screen.getByTestId("browser-health")).getByText("account-01")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/operations?round_id=round-1",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

it("renders two localized browser readiness rows for each of 12 accounts", async () => {
  const snapshot = operationSnapshot();
  const states = ["unbound", "mismatch", "signed_out", "verification_required", "ready", "stale"];
  snapshot.browser_health = Array.from({ length: 12 }, (_, index) =>
    (["activity", "messages"] as const).map((pageRole) => ({
      account_id: `account-${String(index + 1).padStart(2, "0")}`,
      device_id: `phone-${String(index + 1).padStart(2, "0")}`,
      browser_profile_label: `客服 Profile ${index + 1}`,
      expected_tiktok_username: `shop_${index + 1}`,
      observed_username: index % states.length === 0 ? "" : `visible_${index + 1}`,
      page_role: pageRole,
      binding_state: states[index % states.length],
      status: states[index % states.length],
      observed_at_ms: 4_300 + index,
      detail: "/messages",
    })),
  ).flat();
  vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
    String(input).startsWith("/api/coverage")
      ? jsonResponse(coverageSnapshot)
      : jsonResponse(snapshot),
  );

  render(<OperationsView roundId="round-1" />);

  const table = await screen.findByTestId("browser-health");
  expect(within(table).getAllByRole("row")).toHaveLength(25);
  for (const label of ["未绑定", "身份不符", "已退出", "需验证", "已就绪", "心跳过期"]) {
    expect(within(table).getAllByText(label)).toHaveLength(4);
  }
  expect(within(table).getAllByText("客服 Profile 12")).toHaveLength(2);
  expect(within(table).getAllByText("@shop_12")).toHaveLength(2);
  expect(within(table).getAllByText("@visible_12")).toHaveLength(2);
});

it("shows dense capacity KPIs before devices and preserves the operations section order", async () => {
  mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  await screen.findByRole("heading", { name: "设备运行状态" });
  const kpis = screen.getByLabelText("轮次关键指标");
  expect(within(kpis).getByText("最慢平均耗时")).toBeVisible();
  expect(within(kpis).getByText("6.10秒")).toBeVisible();
  expect(within(kpis).getByText("最慢 P90")).toBeVisible();
  expect(within(kpis).getByText("8.80秒")).toBeVisible();
  expect(within(kpis).getByText("20小时预计容量")).toBeVisible();
  expect(within(kpis).getByText("11,803")).toBeVisible();
  expect(within(kpis).getByText("预测")).toBeVisible();

  const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
  expect(headings).toEqual(["设备运行状态", "滚动一小时配额", "目标覆盖", "运行证据"]);
});

it("clears the previous round while the selected round loads", async () => {
  const nextOperations = deferredResponse();
  const nextCoverage = deferredResponse();
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/operations?round_id=round-2") return nextOperations.promise;
    if (url === "/api/coverage?round_id=round-2&offset=0&limit=100") return nextCoverage.promise;
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    return jsonResponse(operationSnapshot());
  });

  const view = render(<OperationsView roundId="round-1" />);
  expect((await screen.findAllByText("phone-01"))[0]).toBeVisible();

  view.rerender(<OperationsView roundId="round-2" />);

  expect(await screen.findByText("正在加载运营数据")).toBeVisible();
  expect(screen.queryByText("phone-01")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "暂停轮次" })).not.toBeInTheDocument();

  const roundTwo = operationSnapshot();
  roundTwo.round.round_id = "round-2";
  roundTwo.devices[0].device_id = "phone-03";
  nextOperations.resolve(await jsonResponse(roundTwo));
  nextCoverage.resolve(await jsonResponse({ ...coverageSnapshot, round_id: "round-2" }));
  expect((await screen.findAllByText("phone-03"))[0]).toBeVisible();
});

it("ignores late responses from the previously selected round", async () => {
  const oldOperations = deferredResponse();
  const oldCoverage = deferredResponse();
  const healthChanged = vi.fn();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/operations?round_id=round-1") return oldOperations.promise;
    if (url === "/api/coverage?round_id=round-1&offset=0&limit=100") return oldCoverage.promise;
    if (url.startsWith("/api/coverage")) return jsonResponse({ ...coverageSnapshot, round_id: "round-2" });
    const current = operationSnapshot();
    current.round.round_id = "round-2";
    current.devices[0].device_id = "phone-current";
    return jsonResponse(current);
  });

  const view = render(<OperationsView onHealthChange={healthChanged} roundId="round-1" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  view.rerender(<OperationsView onHealthChange={healthChanged} roundId="round-2" />);
  expect((await screen.findAllByText("phone-current"))[0]).toBeVisible();

  oldOperations.resolve(await jsonResponse(operationSnapshot()));
  oldCoverage.resolve(await jsonResponse(coverageSnapshot));
  await waitFor(() => expect(healthChanged).toHaveBeenCalledTimes(1));
  expect(within(screen.getByTestId("device-table")).queryByText("phone-01")).not.toBeInTheDocument();
});

it("applies only the newest overlapping refresh", async () => {
  const refreshOneOperations = deferredResponse();
  const refreshOneCoverage = deferredResponse();
  const refreshTwoOperations = deferredResponse();
  const refreshTwoCoverage = deferredResponse();
  let operationsCalls = 0;
  let coverageCalls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/operations")) {
      operationsCalls += 1;
      if (operationsCalls === 2) return refreshOneOperations.promise;
      if (operationsCalls === 3) return refreshTwoOperations.promise;
      return jsonResponse(operationSnapshot());
    }
    coverageCalls += 1;
    if (coverageCalls === 2) return refreshOneCoverage.promise;
    if (coverageCalls === 3) return refreshTwoCoverage.promise;
    return jsonResponse(coverageSnapshot);
  });

  render(<OperationsView roundId="round-1" />);
  const refresh = await screen.findByRole("button", { name: "刷新运营数据" });
  fireEvent.click(refresh);
  fireEvent.click(refresh);

  const newest = operationSnapshot();
  newest.devices[0].device_id = "phone-newest";
  refreshTwoOperations.resolve(await jsonResponse(newest));
  refreshTwoCoverage.resolve(await jsonResponse(coverageSnapshot));
  expect((await screen.findAllByText("phone-newest"))[0]).toBeVisible();

  const stale = operationSnapshot();
  stale.devices[0].device_id = "phone-stale";
  refreshOneOperations.resolve(await jsonResponse(stale));
  refreshOneCoverage.resolve(await jsonResponse(coverageSnapshot));
  await waitFor(() => expect(screen.queryByText("phone-stale")).not.toBeInTheDocument());
  expect(screen.getAllByText("phone-newest")[0]).toBeVisible();
});

it("does not publish health after unmount", async () => {
  const operations = deferredResponse();
  const coverage = deferredResponse();
  const healthChanged = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
    String(input).startsWith("/api/coverage") ? coverage.promise : operations.promise,
  );

  const view = render(<OperationsView onHealthChange={healthChanged} roundId="round-1" />);
  view.unmount();
  operations.resolve(await jsonResponse(operationSnapshot()));
  coverage.resolve(await jsonResponse(coverageSnapshot));

  await Promise.resolve();
  expect(healthChanged).not.toHaveBeenCalled();
});

it("shows a round-scoped error when the initial load fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
    String(input).startsWith("/api/coverage")
      ? jsonResponse(coverageSnapshot)
      : jsonResponse({ error: "operations unavailable" }, false, 503),
  );

  render(<OperationsView roundId="round-1" />);

  expect(await screen.findByRole("alert")).toHaveTextContent("operations unavailable");
  expect(screen.queryByRole("button", { name: "暂停轮次" })).not.toBeInTheDocument();
});

it("pauses the round only after server confirmation", async () => {
  let operationsCalls = 0;
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    if (url === "/api/operations?round_id=round-1") {
      operationsCalls += 1;
      return jsonResponse(operationSnapshot(operationsCalls > 1 ? "paused" : "running"));
    }
    if (url === "/api/commands/pause") {
      const body = JSON.parse(String(init?.body));
      expect(body).toMatchObject({ scope: "round", scope_id: "round-1" });
      expect(body.command_id).toMatch(/^[0-9a-f-]+$/);
      return jsonResponse({ ...body, command: "pause", state: "paused" });
    }
    throw new Error(`Unexpected request ${url}`);
  });

  render(<OperationsView roundId="round-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "暂停轮次" }));

  expect((await screen.findAllByText("已暂停"))[0]).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/commands/pause",
    expect.objectContaining({ method: "POST" }),
  );
});

it("pauses phone-01 with device scope and refreshes after success", async () => {
  let operationsCalls = 0;
  const pending = deferredResponse();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    if (url === "/api/operations?round_id=round-1") {
      operationsCalls += 1;
      const snapshot = operationSnapshot();
      snapshot.devices[0].control_state = operationsCalls > 1 ? "paused" : "running";
      return jsonResponse(snapshot);
    }
    if (url === "/api/commands/pause") {
      expect(JSON.parse(String(init?.body))).toMatchObject({
        scope: "device",
        scope_id: "phone-01",
      });
      return pending.promise;
    }
    throw new Error(`Unexpected request ${url}`);
  });

  render(<OperationsView roundId="round-1" />);
  const pausePhoneOne = await screen.findByRole("button", { name: "暂停 phone-01" });
  fireEvent.click(pausePhoneOne);

  expect(pausePhoneOne).toBeDisabled();
  expect(screen.getByRole("button", { name: "启动 phone-01" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "暂停 phone-02" })).toBeEnabled();
  pending.resolve(await jsonResponse({ state: "paused" }));
  expect(await screen.findByText("已暂停")).toBeVisible();
  expect(operationsCalls).toBe(2);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/commands/pause",
    expect.objectContaining({ method: "POST" }),
  );
});

it("disables only the fleet or round command that is pending", async () => {
  const pending = deferredResponse();
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    if (url === "/api/commands/pause") return pending.promise;
    return jsonResponse(operationSnapshot());
  });

  render(<OperationsView roundId="round-1" />);
  const pauseRound = await screen.findByRole("button", { name: "暂停轮次" });
  fireEvent.click(pauseRound);

  expect(pauseRound).toBeDisabled();
  expect(screen.getByRole("button", { name: "启动轮次" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "暂停设备组" })).toBeEnabled();
  pending.resolve(await jsonResponse({ state: "paused" }));
});

it("retries a deferred assignment and refreshes confirmed data", async () => {
  const fetchMock = mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  const coverage = await screen.findByTestId("coverage-matrix");
  fireEvent.click(within(coverage).getByRole("button", { name: "重试 phone-02 对 buyer_1 的任务" }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/commands/retry",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"assignment_id":42'),
      }),
    ),
  );
});

it("disables only the matching coverage retry while it is pending", async () => {
  const pending = deferredResponse();
  const twoRetryRows = {
    ...coverageSnapshot,
    items: [
      ...coverageSnapshot.items,
      {
        identity_key: "sec:buyer-2",
        username: "buyer_2",
        devices: [
          {
            ...coverageSnapshot.items[0].devices[1],
            assignment_id: 43,
          },
        ],
      },
    ],
    total: 2,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(twoRetryRows);
    if (url === "/api/commands/retry") return pending.promise;
    return jsonResponse(operationSnapshot());
  });

  render(<OperationsView roundId="round-1" />);
  const firstRetry = await screen.findByRole("button", { name: "重试 phone-02 对 buyer_1 的任务" });
  fireEvent.click(firstRetry);

  expect(firstRetry).toBeDisabled();
  expect(screen.getByRole("button", { name: "重试 phone-02 对 buyer_2 的任务" })).toBeEnabled();
  pending.resolve(await jsonResponse({ state: "pending" }));
});

it("shows screenshot evidence as an accessible Lucide icon control", async () => {
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  fireEvent.click(await screen.findByRole("button", { name: "查看 phone-02 诊断信息" }));

  const screenshot = screen.getByRole("button", { name: "截图证据 shot-1" });
  expect(screenshot).toHaveAttribute("title", "截图证据 shot-1");
  expect(screenshot.querySelector("svg")).toHaveClass("lucide-image");
  expect(screen.queryByText("capture shot-1")).not.toBeInTheDocument();
  fireEvent.click(screenshot);
  expect(open).toHaveBeenCalledWith(
    "/api/diagnostic-screenshots/shot-1",
    "_blank",
    "noopener,noreferrer",
  );
});

it("derives topbar fleet health from operations device and browser data", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/rounds")) {
      return jsonResponse({
        items: [{
          round_id: "round-1",
          pool_id: "pool-1",
          state: "running",
          starts_at_ms: 1_000,
          created_at_ms: 500,
          target_count: 2,
          device_count: 2,
        }],
      });
    }
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    return jsonResponse(operationSnapshot());
  });

  render(<App />);

  expect(await screen.findByLabelText("设备组健康：2 台设备中 1 台健康；1 个浏览器观察器中 1 个健康")).toHaveTextContent("设备 1/2 · 浏览器 1/1");
});

it("keeps global health degraded without browser heartbeat evidence", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/rounds")) {
      return jsonResponse({
        items: [{
          round_id: "round-1",
          pool_id: "pool-1",
          state: "running",
          starts_at_ms: 1_000,
          created_at_ms: 500,
          target_count: 2,
          device_count: 2,
        }],
      });
    }
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    const snapshot = operationSnapshot();
    snapshot.devices.forEach((device) => { device.health = "healthy"; });
    snapshot.browser_health = [];
    return jsonResponse(snapshot);
  });

  render(<App />);

  const health = await screen.findByLabelText("设备组健康：2 台设备中 2 台健康；0 个浏览器观察器中 0 个健康");
  expect(health).toHaveClass("state-degraded");
});

it("labels empty device and browser health as not connected", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/rounds")) {
      return jsonResponse({
        items: [{
          round_id: "round-1",
          pool_id: "pool-1",
          state: "pending",
          starts_at_ms: 1_000,
          created_at_ms: 500,
          target_count: 0,
          device_count: 0,
        }],
      });
    }
    if (url.startsWith("/api/coverage")) {
      return jsonResponse({ ...coverageSnapshot, items: [], total: 0 });
    }
    const snapshot = operationSnapshot("pending");
    snapshot.devices = [];
    snapshot.quotas = [];
    snapshot.browser_health = [];
    return jsonResponse(snapshot);
  });

  render(<App />);

  const health = await screen.findByLabelText("设备组健康：未连接");
  expect(health).toHaveTextContent("未连接");
  expect(health).toHaveClass("state-degraded");
});

it("retains the last confirmed snapshot when a command fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.startsWith("/api/coverage")) return jsonResponse(coverageSnapshot);
    if (url === "/api/commands/pause") {
      return jsonResponse({ error: "round is busy" }, false, 409);
    }
    return jsonResponse(operationSnapshot());
  });

  render(<OperationsView roundId="round-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "暂停轮次" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("round is busy");
  expect(screen.getAllByText("phone-01")[0]).toBeVisible();
  expect(screen.getAllByText("运行中")[0]).toBeVisible();
});

it("marks device bands and the coverage scroller for responsive layouts", async () => {
  mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  expect(await screen.findByTestId("device-table")).toHaveClass("responsive-kv-bands");
  expect(screen.getByTestId("coverage-scroller")).toHaveClass("coverage-scroll");
  expect(screen.getByTestId("coverage-target-header")).toHaveClass("sticky-target");
});
