import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      resets_at_ms: 8_000,
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

it("loads the operations snapshot and coverage for the selected round", async () => {
  const fetchMock = mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  expect((await screen.findAllByText("phone-01"))[0]).toBeVisible();
  expect(screen.getAllByText("buyer_1")[0]).toBeVisible();
  expect(screen.getByText("50%")).toBeVisible();
  expect(within(screen.getByTestId("mobile-traces")).getAllByText("2/2")[0]).toBeVisible();
  expect(within(screen.getByTestId("browser-health")).getByText("account-01")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/operations?round_id=round-1",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
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
  fireEvent.click(await screen.findByRole("button", { name: "Pause round" }));

  expect((await screen.findAllByText("Paused"))[0]).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/commands/pause",
    expect.objectContaining({ method: "POST" }),
  );
});

it("retries a deferred assignment and refreshes confirmed data", async () => {
  const fetchMock = mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  const coverage = await screen.findByTestId("coverage-matrix");
  fireEvent.click(within(coverage).getByRole("button", { name: "Retry phone-02 for buyer_1" }));

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
  fireEvent.click(await screen.findByRole("button", { name: "Pause round" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("round is busy");
  expect(screen.getAllByText("phone-01")[0]).toBeVisible();
  expect(screen.getAllByText("Running")[0]).toBeVisible();
});

it("marks device bands and the coverage scroller for responsive layouts", async () => {
  mockInitialLoad();
  render(<OperationsView roundId="round-1" />);

  expect(await screen.findByTestId("device-table")).toHaveClass("responsive-kv-bands");
  expect(screen.getByTestId("coverage-scroller")).toHaveClass("coverage-scroll");
  expect(screen.getByTestId("coverage-target-header")).toHaveClass("sticky-target");
});
