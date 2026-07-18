import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AnalyticsView } from "./views/AnalyticsView";
import { InboxView } from "./views/InboxView";

const leadPayload = (selected: object | null = null) => ({
  configured: true,
  accounts: [
    {
      account_id: "account-01",
      device_id: "phone-01",
      enabled: true,
      ai_enabled: true,
      followback_enabled: true,
      private_channel_configured: true,
      model_configured: true,
    },
  ],
  conversations: [
    {
      account_id: "account-01",
      conversation_id: "conversation-01",
      participant_username: "buyer_01",
      stage: selected ? "human_required" : "qualified",
      human_required: Boolean(selected),
      last_message_preview: "Can you send the details?",
      last_message_at_ms: 8_000,
    },
  ],
  selected,
  funnel: {
    dm_inbound: 12,
    engaged: 8,
    qualified: 4,
    invited: 3,
    contact_captured: 2,
    human_required: selected ? 1 : 0,
  },
  sales: {
    by_status: { confirmed: 1 },
    confirmed_revenue_minor: { USD: 12_500 },
    sales: 1,
  },
});

const selectedLead = (humanRequired = false) => ({
  account_id: "account-01",
  conversation_id: "conversation-01",
  stage: humanRequired ? "human_required" : "qualified",
  human_required: humanRequired,
  messages: [
    { message_id: "outbound-1", direction: "outbound", message_type: "TEXT", text: "Hello", timestamp_ms: 7_000 },
    { message_id: "inbound-2", direction: "inbound", message_type: "TEXT", text: "Can you send the details?", timestamp_ms: 8_000 },
  ],
  draft: {
    plan_id: 19,
    inbound_fingerprint: "inbound-2",
    reply_text: "I can share the next step.",
    state: "planned",
  },
});

const operationsPayload = {
  round: { round_id: "round-1", pool_id: "pool-1", state: "running", starts_at_ms: 1_000, target_count: 1000 },
  devices: [
    { device_id: "phone-01", account_id: "account-01", health: "healthy", health_error_code: null, health_updated_at_ms: 4_000, control_state: "running", current_assignment: null, mean_ms: 5_200, p90_ms: 7_400, latest_diagnostic: null },
    { device_id: "phone-02", account_id: "account-02", health: "healthy", health_error_code: null, health_updated_at_ms: 4_000, control_state: "running", current_assignment: null, mean_ms: 6_800, p90_ms: 9_100, latest_diagnostic: null },
  ],
  quotas: [],
  coverage: { targets: 1000, required_devices: 2, confirmed_visits: 1304, completed_assignments: 1304, fully_covered: 652, fully_completed: 652, coverage_rate: 0.652, completion_rate: 0.652 },
  recent_mobile_traces: [],
  browser_health: [],
};

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) } as Response);
}

it("takes over a conversation before enabling the manual composer", async () => {
  let detailCalls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/takeover") && init?.method === "POST") {
      return jsonResponse({ account_id: "account-01", conversation_id: "conversation-01", stage: "human_required", human_required: true });
    }
    if (url.includes("account_id=account-01")) {
      detailCalls += 1;
      return jsonResponse(leadPayload(selectedLead(detailCalls >= 3)));
    }
    return jsonResponse(leadPayload());
  });

  render(<InboxView />);
  fireEvent.click(await screen.findByRole("button", { name: /buyer_01/ }));

  const composer = await screen.findByRole("textbox", { name: "Manual reply" });
  expect(composer).toBeDisabled();
  expect(screen.getByText("Private channel configured")).toBeVisible();
  expect(screen.queryByText(/WhatsApp/i)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Take over" }));
  expect(await screen.findByRole("textbox", { name: "Manual reply" })).toBeEnabled();
  expect(globalThis.fetch).toHaveBeenCalledWith(
    expect.stringContaining("inbound_fingerprint=inbound-2"),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

it("creates an immutable manual plan and records sale amounts in minor units", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/manual-reply-plan") && init?.method === "POST") {
      return jsonResponse({ plan_id: 44, inbound_fingerprint: "inbound-2", reply_text: "Personal follow-up", state: "planned" });
    }
    if (url.endsWith("/sale") && init?.method === "POST") {
      return jsonResponse({ amount_minor: 12345, currency: "USD", status: "confirmed" });
    }
    if (url.includes("account_id=account-01")) return jsonResponse(leadPayload(selectedLead(true)));
    return jsonResponse(leadPayload(selectedLead(true)));
  });

  render(<InboxView />);
  fireEvent.click(await screen.findByRole("button", { name: /buyer_01/ }));
  const composer = await screen.findByRole("textbox", { name: "Manual reply" });
  fireEvent.change(composer, { target: { value: "Personal follow-up" } });
  fireEvent.click(screen.getByRole("button", { name: "Create send plan" }));
  expect(await screen.findByText("Immutable send plan created; delivery is pending.")) .toBeVisible();

  fireEvent.change(screen.getByRole("spinbutton", { name: "Sale amount" }), { target: { value: "123.45" } });
  fireEvent.click(screen.getByRole("button", { name: "Record sale" }));
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/leads/account-01/conversation-01/sale",
    expect.objectContaining({ body: expect.stringContaining('"amount_minor":12345') }),
  ));
});

it("labels measured capacity separately from projection and uses dynamic coverage", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).startsWith("/api/operations")) return jsonResponse(operationsPayload);
    return jsonResponse(leadPayload());
  });

  render(<AnalyticsView roundId="round-1" />);

  expect(await screen.findByText("Measured completions")).toBeVisible();
  expect(screen.getByText("Projected daily capacity")).toBeVisible();
  expect(screen.getByText("Not promoted")).toBeVisible();
  expect(screen.getByText("652 targets at 2/2")).toBeVisible();
  expect(within(screen.getByTestId("funnel-table")).getByText("Qualified leads")).toBeVisible();
  expect(screen.getByText("USD 125.00")).toBeVisible();
});

it("keeps the newest conversation when an older detail response arrives late", async () => {
  let resolveOld!: (response: Response) => void;
  const oldDetail = new Promise<Response>((resolve) => { resolveOld = resolve; });
  const list = leadPayload();
  list.conversations.push({
    account_id: "account-01",
    conversation_id: "conversation-02",
    participant_username: "buyer_02",
    stage: "qualified",
    human_required: false,
    last_message_preview: "Second buyer",
    last_message_at_ms: 9_000,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("conversation_id=conversation-01")) return oldDetail;
    if (url.includes("conversation_id=conversation-02")) {
      return jsonResponse({ ...list, selected: { ...selectedLead(), conversation_id: "conversation-02", messages: [{ message_id: "buyer-02-inbound", direction: "inbound", message_type: "TEXT", text: "Current thread", timestamp_ms: 9_000 }] } });
    }
    return jsonResponse(list);
  });

  render(<InboxView />);
  fireEvent.click(await screen.findByRole("button", { name: /buyer_01/ }));
  fireEvent.click(screen.getByRole("button", { name: /buyer_02/ }));
  expect(await screen.findByText("Current thread")).toBeVisible();

  resolveOld(await jsonResponse({ ...list, selected: { ...selectedLead(), messages: [{ message_id: "old", direction: "inbound", message_type: "TEXT", text: "Stale thread", timestamp_ms: 8_000 }] } }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText("Stale thread")).not.toBeInTheDocument();
  expect(screen.getByText("Current thread")).toBeVisible();
});

it("does not create a manual plan when bounded history has no inbound message", async () => {
  const outboundOnly = { ...selectedLead(true), messages: [{ message_id: "outbound-only", direction: "outbound", message_type: "TEXT", text: "Waiting", timestamp_ms: 8_000 }], draft: null };
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("account_id=account-01") ? jsonResponse(leadPayload(outboundOnly)) : jsonResponse(leadPayload()));

  render(<InboxView />);
  fireEvent.click(await screen.findByRole("button", { name: /buyer_01/ }));
  const composer = await screen.findByRole("textbox", { name: "Manual reply" });
  fireEvent.change(composer, { target: { value: "Do not plan this" } });
  expect(screen.getByRole("button", { name: "Create send plan" })).toBeDisabled();
});
