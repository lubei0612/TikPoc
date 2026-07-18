export type RoundState = "pending" | "running" | "paused" | "stopped" | "completed";
export type CommandAction = "start" | "pause" | "stop";

export interface Assignment {
  assignment_id: number;
  identity_key: string;
  phase: string;
  attempt_count: number;
  last_error_code: string | null;
  control_state: string;
}

export interface Diagnostic {
  result: string;
  attempted_at_ms: number;
  ui_summary: string;
  screenshot_id: string | null;
}

export interface Device {
  device_id: string;
  account_id: string | null;
  health: string;
  health_error_code: string | null;
  health_updated_at_ms: number | null;
  control_state: string;
  current_assignment: Assignment | null;
  mean_ms: number;
  p90_ms: number;
  latest_diagnostic: Diagnostic | null;
}

export interface Quota {
  device_id: string;
  outcome: string;
  limit: number;
  reserved: number;
  confirmed: number;
  uncertain: number;
  remaining: number;
  resets_at_ms: number;
}

export interface CoverageSummary {
  targets: number;
  required_devices: number;
  confirmed_visits: number;
  completed_assignments: number;
  fully_covered: number;
  fully_completed: number;
  coverage_rate: number;
  completion_rate: number;
}

export interface MobileTrace {
  identity_key: string;
  username: string;
  confirmed_devices: number;
  completed_devices: number;
  required_devices: number;
  fully_covered: boolean;
  fully_completed: boolean;
  last_visit_confirmed_at_ms: number;
}

export interface BrowserHealth {
  account_id: string;
  page_role: "activity" | "messages";
  device_id: string;
  status: string;
  observed_at_ms: number;
  detail: string;
}

export interface OperationsSnapshot {
  round: {
    round_id: string;
    pool_id: string;
    state: RoundState;
    starts_at_ms: number;
    target_count: number;
  };
  devices: Device[];
  quotas: Quota[];
  coverage: CoverageSummary;
  recent_mobile_traces: MobileTrace[];
  browser_health: BrowserHealth[];
}

export interface CoverageAssignment {
  assignment_id: number;
  device_id: string;
  phase: string;
  planned_outcome: string | null;
  visit_confirmed: boolean;
  completed: boolean;
  duration_ms: number | null;
  attempt_count: number;
  next_attempt_at_ms: number;
  last_error_code: string | null;
  control_state: string;
}

export interface CoverageTarget {
  identity_key: string;
  username: string;
  devices: CoverageAssignment[];
}

export interface CoverageSnapshot {
  round_id: string;
  items: CoverageTarget[];
  total: number;
  offset: number;
  limit: number;
}

export interface RoundListItem {
  round_id: string;
  pool_id: string;
  state: RoundState;
  starts_at_ms: number;
  created_at_ms: number;
  target_count: number;
  device_count: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new ApiError(payload.error || `Request failed (${response.status})`, response.status);
  }
  return payload;
}

export async function getOperations(roundId: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ round_id: roundId });
  return parseJson<OperationsSnapshot>(
    await fetch(`/api/operations?${query}`, { signal }),
  );
}

export async function getCoverage(roundId: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ round_id: roundId, offset: "0", limit: "100" });
  return parseJson<CoverageSnapshot>(
    await fetch(`/api/coverage?${query}`, { signal }),
  );
}

export async function getRounds(signal?: AbortSignal) {
  return parseJson<{ items: RoundListItem[] }>(
    await fetch("/api/rounds?offset=0&limit=100", { signal }),
  );
}

export type OperatorCommand =
  | {
      action: CommandAction;
      commandId: string;
      scope: "device" | "fleet" | "round";
      scopeId: string;
    }
  | {
      action: "retry";
      commandId: string;
      assignmentId: number;
    };

export async function postCommand(command: OperatorCommand): Promise<Record<string, unknown>> {
  const url = `/api/commands/${command.action}`;
  const body =
    command.action === "retry"
      ? { command_id: command.commandId, assignment_id: command.assignmentId }
      : {
          command_id: command.commandId,
          scope: command.scope,
          scope_id: command.scopeId,
        };
  const request = () =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

  try {
    return await parseJson(await request());
  } catch (error) {
    // The command id remains stable when a transport interruption is retried.
    if (error instanceof TypeError) return parseJson(await request());
    throw error;
  }
}
