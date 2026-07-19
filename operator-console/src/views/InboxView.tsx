import { RefreshCw, SlidersHorizontal } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createManualReplyPlan,
  getLeads,
  recordSale,
  setAccountEnabled,
  takeOverLead,
  type LeadConversation,
  type LeadInboxSnapshot,
  type SelectedLead,
} from "../api";
import { ConversationDrawer } from "../components/ConversationDrawer";
import { ConversationList } from "../components/ConversationList";
import { localizeError, localizeValue } from "../localization";

const errorText = (reason: unknown) => localizeError(reason, "线索操作失败");
type ScopedText = { key: string; text: string };
type ScopedAction = { key: string; name: string };

export function InboxView() {
  const [snapshot, setSnapshot] = useState<LeadInboxSnapshot | null>(null);
  const [selectedConversation, setSelectedConversation] = useState<LeadConversation | null>(null);
  const [selected, setSelected] = useState<SelectedLead | null>(null);
  const [fingerprint, setFingerprint] = useState("");
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<ScopedAction | null>(null);
  const [error, setError] = useState<ScopedText | null>(null);
  const [notice, setNotice] = useState<ScopedText | null>(null);
  const selectionController = useRef<AbortController | null>(null);
  const selectionGeneration = useRef(0);
  const currentSelectionKey = useRef("");

  const loadList = useCallback(async (signal?: AbortSignal) => {
    const next = await getLeads(undefined, signal);
    setSnapshot(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadList(controller.signal).catch((reason) => setError({ key: "", text: errorText(reason) })).finally(() => setLoading(false));
    return () => controller.abort();
  }, [loadList]);

  useEffect(() => () => {
    selectionGeneration.current += 1;
    selectionController.current?.abort();
  }, []);

  const fetchSelected = useCallback(async (conversation: LeadConversation, knownFingerprint = "", signal?: AbortSignal) => {
    let inboundFingerprint = knownFingerprint;
    let next = await getLeads({ accountId: conversation.account_id, conversationId: conversation.conversation_id, inboundFingerprint }, signal);
    if (!inboundFingerprint) {
      inboundFingerprint = [...(next.selected?.messages ?? [])].reverse().find((message) => message.direction === "inbound")?.message_id ?? "";
      if (inboundFingerprint) next = await getLeads({ accountId: conversation.account_id, conversationId: conversation.conversation_id, inboundFingerprint }, signal);
    }
    return { next, inboundFingerprint };
  }, []);

  const requestSelected = useCallback(async (conversation: LeadConversation, knownFingerprint = "") => {
    selectionController.current?.abort();
    const controller = new AbortController();
    selectionController.current = controller;
    const generation = ++selectionGeneration.current;
    try {
      const { next, inboundFingerprint } = await fetchSelected(conversation, knownFingerprint, controller.signal);
      const isCurrent = !controller.signal.aborted && generation === selectionGeneration.current
        && next.selected?.account_id === conversation.account_id
        && next.selected?.conversation_id === conversation.conversation_id;
      if (!isCurrent) return;
      setSnapshot(next);
      setSelected(next.selected);
      setFingerprint(inboundFingerprint);
    } catch (reason) {
      if (!controller.signal.aborted && generation === selectionGeneration.current) {
        setError({ key: `${conversation.account_id}:${conversation.conversation_id}`, text: errorText(reason) });
      }
    }
  }, [fetchSelected]);

  const choose = (conversation: LeadConversation) => {
    currentSelectionKey.current = `${conversation.account_id}:${conversation.conversation_id}`;
    setSelectedConversation(conversation);
    setSelected(null);
    setError(null);
    setNotice(null);
    void requestSelected(conversation);
  };

  const runAction = async (name: string, command: () => Promise<unknown>, success: string) => {
    if (!selectedConversation) return;
    const commandSelectionKey = `${selectedConversation.account_id}:${selectedConversation.conversation_id}`;
    setAction({ key: commandSelectionKey, name });
    setError((current) => current?.key === commandSelectionKey ? null : current);
    setNotice((current) => current?.key === commandSelectionKey ? null : current);
    try {
      await command();
      setNotice({ key: commandSelectionKey, text: success });
      if (currentSelectionKey.current === commandSelectionKey) await requestSelected(selectedConversation, fingerprint);
    } catch (reason) {
      setError({ key: commandSelectionKey, text: errorText(reason) });
    } finally {
      setAction((current) => current?.key === commandSelectionKey && current.name === name ? null : current);
    }
  };

  const toggleAccount = async (accountId: string, setting: "ai" | "followback", enabled: boolean) => {
    const accountKey = `account:${accountId}:${setting}`;
    setAction({ key: accountKey, name: setting });
    setError(null);
    try {
      await setAccountEnabled(accountId, setting, enabled, crypto.randomUUID());
      await loadList();
    } catch (reason) {
      setError({ key: "", text: errorText(reason) });
    } finally {
      setAction((current) => current?.key === accountKey ? null : current);
    }
  };

  if (loading) return <div className="workspace-state"><span className="loading-line" />正在加载线索收件箱</div>;
  if (!snapshot) return <div className="workspace-state error-state" role="alert">{error?.text || "线索收件箱暂不可用"}</div>;

  const account = selectedConversation ? snapshot.accounts.find((item) => item.account_id === selectedConversation.account_id) : undefined;
  const selectedKey = selectedConversation ? `${selectedConversation.account_id}:${selectedConversation.conversation_id}` : "";
  const selectedAction = action?.key === selectedKey ? action.name : null;
  const selectedError = error?.key === selectedKey ? error.text : null;
  const selectedNotice = notice?.key === selectedKey ? notice.text : null;
  const browserState = (accountId: string, pageRole: "activity" | "messages") =>
    snapshot.browser_health?.find((row) => row.account_id === accountId && row.page_role === pageRole)?.binding_state ?? "unbound";
  return (
    <main className="inbox-workspace">
      <section className="inbox-main">
        <header className="workspace-title">
          <div><span className="section-index">线索</span><h1>线索转化</h1><p>私域就绪、人工接管与成交状态。</p></div>
          <button className="icon-only" aria-label="刷新线索收件箱" title="刷新线索收件箱" disabled={action !== null} onClick={() => loadList().catch((reason) => setError({ key: "", text: errorText(reason) }))}><RefreshCw size={15} /></button>
        </header>
        <div className="account-control-strip">
          <span><SlidersHorizontal size={14} />账号自动化</span>
          {snapshot.accounts.map((item) => {
            const activityState = browserState(item.account_id, "activity");
            const messagesState = browserState(item.account_id, "messages");
            return <div className="account-control" key={item.account_id}>
              <strong>{item.account_id}</strong>
              <small>{item.private_channel_configured ? "私域已就绪" : "私域未配置"}</small>
              <label><span>AI 回复<small>{localizeValue(messagesState)}</small></span><input aria-label={`${item.account_id} AI 自动回复`} type="checkbox" checked={item.ai_enabled} disabled={!item.enabled || messagesState !== "ready" || action?.key === `account:${item.account_id}:ai`} onChange={(event) => toggleAccount(item.account_id, "ai", event.target.checked)} /></label>
              <label><span>自动回关<small>{localizeValue(activityState)}</small></span><input aria-label={`${item.account_id} 自动回关`} type="checkbox" checked={item.followback_enabled} disabled={!item.enabled || activityState !== "ready" || action?.key === `account:${item.account_id}:followback`} onChange={(event) => toggleAccount(item.account_id, "followback", event.target.checked)} /></label>
            </div>;
          })}
        </div>
        {error?.key === "" && !selectedConversation && <div className="action-error" role="alert">{error.text}</div>}
        <ConversationList conversations={snapshot.conversations} selectedId={selectedConversation?.conversation_id ?? null} onSelect={choose} />
      </section>
      {selectedConversation && !selected && <aside className="conversation-drawer drawer-loading"><span className="loading-line" />正在加载会话</aside>}
      {selectedConversation && selected && <ConversationDrawer account={account} conversation={selectedConversation} lead={selected} action={selectedAction} error={selectedError} notice={selectedNotice} canCreatePlan={Boolean(fingerprint)} onClose={() => { currentSelectionKey.current = ""; selectionGeneration.current += 1; selectionController.current?.abort(); setSelectedConversation(null); setSelected(null); }} onTakeover={() => runAction("takeover", () => takeOverLead(selectedConversation.account_id, selectedConversation.conversation_id, crypto.randomUUID()), "已确认人工接管。")} onManualPlan={(text) => { if (!fingerprint) { setError({ key: selectedKey, text: "有限消息记录中没有收到的消息。" }); return; } void runAction("manual", () => createManualReplyPlan(selectedConversation.account_id, selectedConversation.conversation_id, crypto.randomUUID(), fingerprint, text), "不可变发送计划已创建，等待浏览器发送。"); }} onSale={(amount, currency, status) => runAction("sale", () => recordSale(selectedConversation.account_id, selectedConversation.conversation_id, { commandId: crypto.randomUUID(), amountMinor: Math.round(Number(amount) * 100), currency, status, occurredAtMs: Date.now() }), "服务端已记录成交。")} />}
    </main>
  );
}
