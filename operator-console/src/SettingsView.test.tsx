import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "./App";
import { SettingsView } from "./views/SettingsView";

const settingsPayload = {
  provider: {
    base_url: "https://provider.example/v1",
    model: "model-a",
    key_configured: true,
  },
  accounts: [
    {
      account_id: "account-01",
      browser_profile_label: "Profile One",
      expected_tiktok_username: "shop_one",
      whatsapp: "CONTACT_A",
      telegram: "CHANNEL_A",
      offer_context: "Synthetic offer",
      faq_context: "Synthetic FAQ",
      reply_tone: "Brief",
      brand_name: "Sample Brand",
      welcome_after_followback: true,
      welcome_language: "English",
    },
    {
      account_id: "account-02",
      browser_profile_label: "Profile Two",
      expected_tiktok_username: "shop_two",
      whatsapp: "",
      telegram: "",
      offer_context: "",
      faq_context: "",
      reply_tone: "",
      brand_name: "",
      welcome_after_followback: false,
      welcome_language: "English",
    },
  ],
};

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) } as Response);
}

it("saves provider settings without retaining the API key in the input", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (init?.method === "POST") return jsonResponse(settingsPayload.provider);
    return jsonResponse(settingsPayload);
  });

  render(<SettingsView />);
  const key = await screen.findByLabelText("API Key");
  fireEvent.change(key, { target: { value: "synthetic-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 AI 配置" }));

  await waitFor(() => expect(key).toHaveValue(""));
  expect(screen.getByText("API Key 已配置")).toBeVisible();
  expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/settings/provider",
    expect.objectContaining({
      method: "POST",
      body: expect.stringContaining('"api_key":"synthetic-secret"'),
    }),
  );
});

it("tests the saved provider and reports only model and latency", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input).endsWith("/test") && init?.method === "POST") {
      return jsonResponse({ ok: true, model: "model-a", elapsed_ms: 37 });
    }
    return jsonResponse(settingsPayload);
  });

  render(<SettingsView />);
  fireEvent.click(await screen.findByRole("button", { name: "测试 AI 连接" }));

  expect(await screen.findByText("连接正常 · model-a · 37ms")).toBeVisible();
  expect(screen.queryByText(/synthetic-secret/)).not.toBeInTheDocument();
});

it("saves one account without changing the other account form", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (init?.method === "POST") {
      return jsonResponse({ ...settingsPayload.accounts[1], whatsapp: "CONTACT_B" });
    }
    return jsonResponse(settingsPayload);
  });

  render(<SettingsView />);
  const second = await screen.findByRole("group", { name: "account-02 自动化配置" });
  fireEvent.change(within(second).getByLabelText("品牌名称"), { target: { value: "Second Brand" } });
  fireEvent.change(within(second).getByLabelText("默认欢迎语言"), { target: { value: "French" } });
  fireEvent.click(within(second).getByRole("checkbox", { name: "回关后发送欢迎私信" }));
  fireEvent.change(within(second).getByLabelText("WhatsApp"), { target: { value: "CONTACT_B" } });
  fireEvent.click(within(second).getByRole("button", { name: "保存账号配置" }));

  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/settings/accounts/account-02",
    expect.objectContaining({ method: "POST", body: expect.stringContaining("CONTACT_B") }),
  ));
  const request = vi.mocked(globalThis.fetch).mock.calls.find(([input, init]) =>
    String(input).endsWith("/account-02") && init?.method === "POST",
  );
  expect(JSON.parse(String(request?.[1]?.body))).toEqual(expect.objectContaining({
    brand_name: "Second Brand",
    welcome_after_followback: true,
    welcome_language: "French",
  }));
  const first = screen.getByRole("group", { name: "account-01 自动化配置" });
  expect(within(first).getByLabelText("WhatsApp")).toHaveValue("CONTACT_A");
  expect(within(first).getByLabelText("品牌名称")).toHaveValue("Sample Brand");
});

it("opens the settings route from the top navigation", async () => {
  window.history.replaceState(null, "", "/settings");
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).startsWith("/api/settings")) return jsonResponse(settingsPayload);
    return jsonResponse({ items: [] });
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "AI 与私域配置" })).toBeVisible();
  expect(screen.getByRole("button", { name: "自动化设置" })).toHaveAttribute("aria-current", "page");
});
