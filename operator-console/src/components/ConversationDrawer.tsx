import { BadgeDollarSign, Hand, LockKeyhole, Send, X } from "lucide-react";
import { useState } from "react";

import type { LeadAccount, LeadConversation, SelectedLead } from "../api";

interface ConversationDrawerProps {
  account: LeadAccount | undefined;
  conversation: LeadConversation;
  lead: SelectedLead;
  action: string | null;
  error: string | null;
  notice: string | null;
  canCreatePlan: boolean;
  onClose: () => void;
  onTakeover: () => void;
  onManualPlan: (text: string) => void;
  onSale: (amount: string, currency: string, status: string) => void;
}

export function ConversationDrawer(props: ConversationDrawerProps) {
  const [reply, setReply] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [saleStatus, setSaleStatus] = useState("confirmed");
  const busy = props.action !== null;

  return (
    <aside className="conversation-drawer" aria-label={`Conversation with ${props.conversation.participant_username}`}>
      <header className="drawer-header">
        <div><strong>{props.conversation.participant_username}</strong><span>{props.conversation.account_id} · {props.lead.stage.replaceAll("_", " ")}</span></div>
        <button className="icon-only" aria-label="Close conversation" title="Close conversation" onClick={props.onClose}><X size={16} /></button>
      </header>

      <div className="readiness-strip">
        <span className={props.account?.private_channel_configured ? "ready" : "not-ready"}><LockKeyhole size={13} />{props.account?.private_channel_configured ? "Private channel configured" : "Private channel missing"}</span>
        <span>{props.lead.human_required ? "Human takeover active" : "AI handling"}</span>
      </div>

      <div className="message-history" aria-label="Bounded message history">
        {props.lead.messages.map((message) => (
          <div className={`message-line message-${message.direction}`} key={message.message_id}>
            <span>{message.direction}</span><p>{message.text || `[${message.message_type}]`}</p>
          </div>
        ))}
      </div>

      {props.lead.draft && (
        <section className="draft-band">
          <div><LockKeyhole size={13} /><strong>Immutable pending draft</strong><span className={`status-label status-${props.lead.draft.state}`}><span />{props.lead.draft.state}</span></div>
          <p>{props.lead.draft.reply_text || "Draft generation pending."}</p>
          <small>Plan #{props.lead.draft.plan_id}; delivery state is reported by the server.</small>
        </section>
      )}

      <section className="drawer-action-band">
        <header><Hand size={14} /><strong>Human workflow</strong></header>
        {!props.lead.human_required && <button className="action-button warning" disabled={busy} onClick={props.onTakeover}><Hand size={14} />{props.action === "takeover" ? "Taking over..." : "Take over"}</button>}
        <label htmlFor="manual-reply">Manual reply</label>
        <textarea id="manual-reply" disabled={!props.lead.human_required || busy} onChange={(event) => setReply(event.target.value)} value={reply} />
        <button className="action-button positive" disabled={!props.lead.human_required || busy || !reply.trim() || !props.canCreatePlan} onClick={() => props.onManualPlan(reply.trim())}><Send size={14} />{props.action === "manual" ? "Creating..." : "Create send plan"}</button>
        <small>This creates an immutable plan. Delivery remains pending until the browser reports a send result.</small>
        {!props.canCreatePlan && <small className="cell-error">No inbound message is available in bounded history.</small>}
      </section>

      <section className="drawer-action-band sale-band">
        <header><BadgeDollarSign size={14} /><strong>Sale outcome</strong></header>
        <div className="sale-fields">
          <label>Amount<input aria-label="Sale amount" inputMode="decimal" min="0.01" step="0.01" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
          <label>Currency<input aria-label="Sale currency" maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></label>
          <label>Status<select aria-label="Sale status" value={saleStatus} onChange={(event) => setSaleStatus(event.target.value)}><option value="confirmed">Confirmed</option><option value="pending">Pending</option><option value="refunded">Refunded</option><option value="cancelled">Cancelled</option></select></label>
        </div>
        <button className="action-button" disabled={busy || Number(amount) <= 0 || currency.length !== 3} onClick={() => props.onSale(amount, currency, saleStatus)}><BadgeDollarSign size={14} />{props.action === "sale" ? "Recording..." : "Record sale"}</button>
      </section>
      {props.notice && <div className="action-notice" role="status">{props.notice}</div>}
      {props.error && <div className="action-error" role="alert">{props.error}</div>}
    </aside>
  );
}
