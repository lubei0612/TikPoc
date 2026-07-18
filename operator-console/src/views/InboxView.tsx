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

const errorText = (reason: unknown) => reason instanceof Error ? reason.message : "Lead operation failed";

export function InboxView() {
  const [snapshot, setSnapshot] = useState<LeadInboxSnapshot | null>(null);
  const [selectedConversation, setSelectedConversation] = useState<LeadConversation | null>(null);
  const [selected, setSelected] = useState<SelectedLead | null>(null);
  const [fingerprint, setFingerprint] = useState("");
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const selectionController = useRef<AbortController | null>(null);
  const selectionGeneration = useRef(0);

  const loadList = useCallback(async (signal?: AbortSignal) => {
    const next = await getLeads(undefined, signal);
    setSnapshot(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadList(controller.signal).catch((reason) => setError(errorText(reason))).finally(() => setLoading(false));
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
      if (!controller.signal.aborted && generation === selectionGeneration.current) setError(errorText(reason));
    }
  }, [fetchSelected]);

  const choose = (conversation: LeadConversation) => {
    setSelectedConversation(conversation);
    setSelected(null);
    setError(null);
    setNotice(null);
    void requestSelected(conversation);
  };

  const runAction = async (name: string, command: () => Promise<unknown>, success: string) => {
    if (!selectedConversation) return;
    setAction(name);
    setError(null);
    setNotice(null);
    try {
      await command();
      setNotice(success);
      await requestSelected(selectedConversation, fingerprint);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setAction(null);
    }
  };

  const toggleAccount = async (accountId: string, enabled: boolean) => {
    setAction(`account:${accountId}`);
    setError(null);
    try {
      await setAccountEnabled(accountId, "ai", enabled, crypto.randomUUID());
      await loadList();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setAction(null);
    }
  };

  if (loading) return <div className="workspace-state"><span className="loading-line" />Loading inbox</div>;
  if (!snapshot) return <div className="workspace-state error-state" role="alert">{error || "Inbox unavailable"}</div>;

  const account = selectedConversation ? snapshot.accounts.find((item) => item.account_id === selectedConversation.account_id) : undefined;
  return (
    <main className="inbox-workspace">
      <section className="inbox-main">
        <header className="workspace-title">
          <div><span className="section-index">INBOX</span><h1>Lead conversion</h1><p>Private-channel readiness, takeover and closing state.</p></div>
          <button className="icon-only" aria-label="Refresh inbox" title="Refresh inbox" disabled={action !== null} onClick={() => loadList().catch((reason) => setError(errorText(reason)))}><RefreshCw size={15} /></button>
        </header>
        <div className="account-control-strip">
          <span><SlidersHorizontal size={14} />Account AI</span>
          {snapshot.accounts.map((item) => <label key={item.account_id}><span>{item.account_id}<small>{item.private_channel_configured ? "Private ready" : "Private missing"}</small></span><input type="checkbox" checked={item.ai_enabled} disabled={!item.enabled || action !== null} onChange={(event) => toggleAccount(item.account_id, event.target.checked)} /></label>)}
        </div>
        {error && !selectedConversation && <div className="action-error" role="alert">{error}</div>}
        <ConversationList conversations={snapshot.conversations} selectedId={selectedConversation?.conversation_id ?? null} onSelect={choose} />
      </section>
      {selectedConversation && !selected && <aside className="conversation-drawer drawer-loading"><span className="loading-line" />Loading conversation</aside>}
      {selectedConversation && selected && <ConversationDrawer account={account} conversation={selectedConversation} lead={selected} action={action} error={error} notice={notice} canCreatePlan={Boolean(fingerprint)} onClose={() => { selectionGeneration.current += 1; selectionController.current?.abort(); setSelectedConversation(null); setSelected(null); }} onTakeover={() => runAction("takeover", () => takeOverLead(selectedConversation.account_id, selectedConversation.conversation_id, crypto.randomUUID()), "Human takeover confirmed.")} onManualPlan={(text) => runAction("manual", () => createManualReplyPlan(selectedConversation.account_id, selectedConversation.conversation_id, crypto.randomUUID(), fingerprint, text), "Immutable send plan created; delivery is pending.")} onSale={(amount, currency, status) => runAction("sale", () => recordSale(selectedConversation.account_id, selectedConversation.conversation_id, { commandId: crypto.randomUUID(), amountMinor: Math.round(Number(amount) * 100), currency, status, occurredAtMs: Date.now() }), "Sale recorded by the server.")} />}
    </main>
  );
}
