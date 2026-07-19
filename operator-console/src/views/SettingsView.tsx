import { CheckCircle2, PlugZap, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  getSettings,
  saveAccountAutomationSettings,
  saveProviderSettings,
  testProviderSettings,
  type AccountAutomationSettings,
  type ProviderSettings,
} from "../api";
import { localizeError } from "../localization";

export function SettingsView() {
  const [provider, setProvider] = useState<ProviderSettings | null>(null);
  const [accounts, setAccounts] = useState<AccountAutomationSettings[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getSettings(controller.signal)
      .then((snapshot) => {
        setProvider(snapshot.provider);
        setAccounts(snapshot.accounts);
      })
      .catch((reason: unknown) => setError(localizeError(reason, "配置加载失败")));
    return () => controller.abort();
  }, []);

  const run = async (key: string, command: () => Promise<void>) => {
    setPending(key);
    setError("");
    setNotice("");
    try {
      await command();
    } catch (reason) {
      setError(localizeError(reason, "配置操作失败"));
    } finally {
      setPending(null);
    }
  };

  const saveProvider = () => {
    if (!provider) return;
    void run("provider-save", async () => {
      const saved = await saveProviderSettings({
        baseUrl: provider.base_url,
        apiKey,
        model: provider.model,
      });
      setProvider(saved);
      setApiKey("");
      setNotice("AI 配置已保存，新回复将使用当前配置。");
    });
  };

  const clearProviderKey = () => {
    if (!provider) return;
    void run("provider-clear", async () => {
      const saved = await saveProviderSettings({
        baseUrl: provider.base_url,
        apiKey: "",
        model: provider.model,
        clearKey: true,
      });
      setProvider(saved);
      setApiKey("");
      setNotice("API Key 已清除。");
    });
  };

  const testProvider = () => void run("provider-test", async () => {
    const result = await testProviderSettings();
    setNotice(result.ok
      ? `连接正常 · ${result.model} · ${result.elapsed_ms}ms`
      : `连接失败 · ${result.model || "模型未配置"} · ${result.elapsed_ms}ms`);
  });

  const updateAccount = (
    accountId: string,
    field: keyof AccountAutomationSettings,
    value: string,
  ) => setAccounts((current) => current.map((account) =>
    account.account_id === accountId ? { ...account, [field]: value } : account,
  ));

  const saveAccount = (account: AccountAutomationSettings) => void run(
    `account:${account.account_id}`,
    async () => {
      const saved = await saveAccountAutomationSettings(account.account_id, {
        whatsapp: account.whatsapp,
        telegram: account.telegram,
        offer_context: account.offer_context,
        faq_context: account.faq_context,
        reply_tone: account.reply_tone,
      });
      setAccounts((current) => current.map((item) => item.account_id === account.account_id
        ? { ...account, ...saved }
        : item));
      setNotice(`${account.account_id} 配置已保存。`);
    },
  );

  if (!provider && !error) {
    return <div className="workspace-state"><span className="loading-line" />正在加载自动化配置</div>;
  }
  if (!provider) return <div className="workspace-state error-state" role="alert">{error}</div>;

  return (
    <main className="settings-workspace">
      <header className="workspace-title">
        <div><span className="section-index">设置</span><h1>AI 与私域配置</h1><p>管理模型服务和各账号的销售回复上下文。</p></div>
      </header>

      {(notice || error) && <div className={error ? "settings-notice is-error" : "settings-notice"} role="status">{error || notice}</div>}

      <section className="settings-section" aria-labelledby="provider-heading">
        <header className="section-heading"><div><span className="section-index">01</span><h2 id="provider-heading">AI 服务</h2></div><span>{provider.key_configured ? <><CheckCircle2 size={13} /> API Key 已配置</> : "API Key 未配置"}</span></header>
        <div className="provider-form">
          <label><span>转发地址</span><input aria-label="转发地址" value={provider.base_url} onChange={(event) => setProvider({ ...provider, base_url: event.target.value })} /></label>
          <label><span>模型</span><input aria-label="模型" value={provider.model} onChange={(event) => setProvider({ ...provider, model: event.target.value })} /></label>
          <label><span>API Key</span><input aria-label="API Key" autoComplete="new-password" placeholder={provider.key_configured ? "留空则保留当前 Key" : "输入 API Key"} type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
          <div className="settings-actions">
            <button className="icon-command positive" disabled={pending !== null} onClick={saveProvider}><Save size={14} />保存 AI 配置</button>
            <button className="icon-command" disabled={pending !== null || !provider.key_configured} onClick={testProvider}><PlugZap size={14} />测试 AI 连接</button>
            <button className="icon-only" aria-label="清除 API Key" title="清除 API Key" disabled={pending !== null || !provider.key_configured} onClick={clearProviderKey}><Trash2 size={14} /></button>
          </div>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="accounts-heading">
        <header className="section-heading"><div><span className="section-index">02</span><h2 id="accounts-heading">账号自动化</h2></div><span>{accounts.length} 个账号</span></header>
        <div className="account-settings-grid">
          {accounts.map((account) => <fieldset className="account-settings" key={account.account_id} aria-label={`${account.account_id} 自动化配置`}>
            <legend><strong>{account.account_id}</strong><span>{account.browser_profile_label || "未命名 Profile"} · @{account.expected_tiktok_username || "未识别"}</span></legend>
            <div className="channel-fields">
              <label><span>WhatsApp</span><input aria-label="WhatsApp" value={account.whatsapp} onChange={(event) => updateAccount(account.account_id, "whatsapp", event.target.value)} /></label>
              <label><span>Telegram</span><input aria-label="Telegram" value={account.telegram} onChange={(event) => updateAccount(account.account_id, "telegram", event.target.value)} /></label>
            </div>
            <label><span>产品与服务信息</span><textarea aria-label="产品与服务信息" rows={3} value={account.offer_context} onChange={(event) => updateAccount(account.account_id, "offer_context", event.target.value)} /></label>
            <label><span>常见问题事实</span><textarea aria-label="常见问题事实" rows={3} value={account.faq_context} onChange={(event) => updateAccount(account.account_id, "faq_context", event.target.value)} /></label>
            <label><span>回复语气</span><input aria-label="回复语气" value={account.reply_tone} onChange={(event) => updateAccount(account.account_id, "reply_tone", event.target.value)} /></label>
            <button className="icon-command positive" disabled={pending !== null} onClick={() => saveAccount(account)}><Save size={14} />保存账号配置</button>
          </fieldset>)}
        </div>
      </section>
    </main>
  );
}
