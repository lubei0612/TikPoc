import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AnalyticsView, evaluatePromotion } from "./views/AnalyticsView";
import { InboxView } from "./views/InboxView";
import type { OperationsSnapshot } from "./api";

const leadPayload = (selected: object | null = null) => ({
  configured: true,
  accounts: [
    {
      account_id: "account-01",
      device_id: "phone-01",
      enabled: true,
      ai_enabled: true,
      followback_enabled: true,
      followback_circuit_state: "closed",
      followback_circuit_reason: "",
      followback_cooldown_until_ms: 0,
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
      invitation_seen: true,
      contact_captured: true,
      last_message_preview: "Can you send the details?",
      last_message_at_ms: 8_000,
      last_message_direction: "inbound",
      reply_wait_ms: 4_000,
      last_message_age_ms: 4_000,
    },
  ],
  selected,
  funnel: {
    followers: 17,
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
  browser_health: [
    { account_id: "account-01", device_id: "phone-01", browser_profile_label: "客服一号", expected_tiktok_username: "shop_one", observed_username: "shop_one", page_role: "activity", binding_state: "ready", status: "ready", observed_at_ms: 8_000, detail: "" },
    { account_id: "account-01", device_id: "phone-01", browser_profile_label: "客服一号", expected_tiktok_username: "shop_one", observed_username: "shop_one", page_role: "messages", binding_state: "ready", status: "ready", observed_at_ms: 8_000, detail: "" },
  ],
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

const operationsPayload: OperationsSnapshot = {
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

  const composer = await screen.findByRole("textbox", { name: "人工回复" });
  expect(composer).toBeDisabled();
  expect(screen.getByText("私域渠道已配置")).toBeVisible();
  expect(screen.queryByText(/WhatsApp/i)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "人工接管" }));
  expect(await screen.findByRole("textbox", { name: "人工回复" })).toBeEnabled();
  expect(globalThis.fetch).toHaveBeenCalledWith(
    expect.stringContaining("inbound_fingerprint=inbound-2"),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

it("keeps AI and follow-back controls isolated by account and page health", async () => {
  const payload = leadPayload();
  payload.accounts.push({
    ...payload.accounts[0],
    account_id: "account-02",
    device_id: "phone-02",
  });
  payload.browser_health = [
    ...payload.browser_health.map((row) =>
      row.page_role === "messages" ? { ...row, binding_state: "mismatch", status: "mismatch" } : row,
    ),
    ...payload.browser_health.map((row) => ({
      ...row,
      account_id: "account-02",
      device_id: "phone-02",
      browser_profile_label: "客服二号",
    })),
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (init?.method === "POST") return jsonResponse({ enabled: true });
    return jsonResponse(payload);
  });

  render(<InboxView />);

  expect(await screen.findByRole("checkbox", { name: "account-01 AI 自动回复" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "account-01 自动回关" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "account-02 AI 自动回复" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "account-02 自动回关" })).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "account-01 自动回关" }));
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/accounts/account-01/followback-enable",
    expect.objectContaining({ method: "POST" }),
  ));
});

it("shows follow-back cooldown without disabling AI replies", async () => {
  const payload = leadPayload();
  payload.accounts[0] = {
    ...payload.accounts[0],
    followback_circuit_state: "cooldown",
    followback_circuit_reason: "platform_follow_reverted",
    followback_cooldown_until_ms: Date.now() + 60_000,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse(payload));

  render(<InboxView />);

  expect(await screen.findByText(/回关冷却中/)).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "account-01 AI 自动回复" })).toBeEnabled();
  expect(screen.getByRole("checkbox", { name: "account-01 自动回关" })).toBeDisabled();
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
  const composer = await screen.findByRole("textbox", { name: "人工回复" });
  fireEvent.change(composer, { target: { value: "Personal follow-up" } });
  fireEvent.click(screen.getByRole("button", { name: "创建发送计划" }));
  expect(await screen.findByText("不可变发送计划已创建，等待浏览器发送。")) .toBeVisible();

  fireEvent.change(screen.getByRole("spinbutton", { name: "成交金额" }), { target: { value: "123.45" } });
  fireEvent.click(screen.getByRole("button", { name: "记录成交" }));
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

  const evidence = await screen.findByRole("table", { name: "获客证据" });
  expect(within(evidence).getByText("实测完成数")).toBeVisible();
  expect(within(evidence).getByText("完整覆盖率")).toBeVisible();
  expect(within(evidence).getByText("实测确认访问数")).toBeVisible();
  expect(within(evidence).getByText("预计日容量")).toBeVisible();
  expect(within(evidence).getByText("成交数")).toBeVisible();
  expect(within(evidence).getByText("确认收入")).toBeVisible();
  expect(within(evidence).getByText("每千个完整覆盖目标收入")).toBeVisible();
  expect(screen.getByText("未达推广门槛")).toBeVisible();
  expect(within(evidence).getByText("652 个目标达到 2/2")).toBeVisible();
  const funnel = screen.getByRole("table", { name: "线索漏斗" });
  for (const label of ["新增关注", "收到私信", "合格线索", "私域邀请", "已留联系方式", "人工接管"]) {
    expect(within(funnel).getByText(label)).toBeVisible();
  }
  expect(within(funnel).getByText("17")).toBeVisible();
  expect(within(evidence).getByText("USD 125.00")).toBeVisible();
  expect(within(evidence).getByText("USD 191.72")).toBeVisible();
  expect(screen.getByRole("table", { name: "设备容量" })).toBeVisible();
});

it("keeps invitation and contact evidence visible in a later human stage with labeled timing", async () => {
  const payload = leadPayload();
  payload.conversations[0].stage = "human_required";
  payload.conversations[0].human_required = true;
  payload.conversations.push({
    ...payload.conversations[0],
    conversation_id: "conversation-closed",
    participant_username: "buyer_closed",
    stage: "closed",
    human_required: false,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse(payload));

  render(<InboxView />);

  expect(await screen.findAllByText("已发送私域邀请")).toHaveLength(2);
  expect(screen.getAllByText("已获取联系方式")).toHaveLength(2);
  expect(screen.getAllByText("等待回复 4秒")).toHaveLength(2);
  expect(screen.getAllByText("最后消息 4秒前 · 收到")).toHaveLength(2);
  expect(screen.queryByText(/--/)).not.toBeInTheDocument();
});

it("shows not recorded only when follower measurement is absent", async () => {
  const payload = leadPayload();
  delete (payload.funnel as { followers?: number }).followers;
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).startsWith("/api/operations")) return jsonResponse(operationsPayload);
    return jsonResponse(payload);
  });

  render(<AnalyticsView roundId="round-1" />);

  const followersRow = (await screen.findByText("新增关注")).closest("tr");
  expect(followersRow).not.toBeNull();
  expect(within(followersRow as HTMLTableRowElement).getByText("暂无记录")).toBeVisible();
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
    invitation_seen: false,
    contact_captured: false,
    last_message_preview: "Second buyer",
    last_message_at_ms: 9_000,
    last_message_direction: "inbound",
    reply_wait_ms: 3_000,
    last_message_age_ms: 3_000,
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
  const composer = await screen.findByRole("textbox", { name: "人工回复" });
  fireEvent.change(composer, { target: { value: "Do not plan this" } });
  expect(screen.getByRole("button", { name: "创建发送计划" })).toBeDisabled();
});

it("does not promote zero timing samples or incomplete coverage", () => {
  const zeroSamples = { ...operationsPayload, devices: operationsPayload.devices.map((device) => ({ ...device, mean_ms: 0, p90_ms: 0 })) };
  expect(evaluatePromotion(zeroSamples)).toEqual({ promoted: false, reason: "时延数据不足" });

  const incomplete = { ...operationsPayload, coverage: { ...operationsPayload.coverage, fully_covered: 999, fully_completed: 999 } };
  expect(evaluatePromotion(incomplete)).toEqual({ promoted: false, reason: "覆盖门槛未通过" });
});

it("keeps pending lead actions and late notices scoped to their conversation", async () => {
  let resolveManual!: (response: Response) => void;
  const pendingManual = new Promise<Response>((resolve) => { resolveManual = resolve; });
  const list = leadPayload();
  list.conversations.push({ ...list.conversations[0], conversation_id: "conversation-02", participant_username: "buyer_02" });
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/manual-reply-plan") && init?.method === "POST") return pendingManual;
    if (url.includes("conversation_id=conversation-02")) return jsonResponse({ ...list, selected: { ...selectedLead(true), conversation_id: "conversation-02" } });
    if (url.includes("conversation_id=conversation-01")) return jsonResponse({ ...list, selected: selectedLead(true) });
    return jsonResponse(list);
  });

  render(<InboxView />);
  fireEvent.click(await screen.findByRole("button", { name: /buyer_01/ }));
  const firstComposer = await screen.findByRole("textbox", { name: "人工回复" });
  fireEvent.change(firstComposer, { target: { value: "Pending on first" } });
  fireEvent.click(screen.getByRole("button", { name: "创建发送计划" }));
  fireEvent.click(screen.getByRole("button", { name: /buyer_02/ }));

  expect(await screen.findByRole("textbox", { name: "人工回复" })).toBeEnabled();
  await act(async () => {
    resolveManual(await jsonResponse({ plan_id: 91, inbound_fingerprint: "inbound-2", reply_text: "Pending on first", state: "planned" }));
    await pendingManual;
  });
  expect(screen.queryByText("不可变发送计划已创建，等待浏览器发送。")).not.toBeInTheDocument();
});
