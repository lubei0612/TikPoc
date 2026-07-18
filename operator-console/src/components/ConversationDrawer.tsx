import { BadgeDollarSign, Hand, LockKeyhole, Send, X } from "lucide-react";
import { useState } from "react";

import type { LeadAccount, LeadConversation, SelectedLead } from "../api";
import { localizeValue } from "../localization";

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
    <aside className="conversation-drawer" aria-label={`与 ${props.conversation.participant_username} 的会话`}>
      <header className="drawer-header">
        <div><strong>{props.conversation.participant_username}</strong><span>{props.conversation.account_id} · {localizeValue(props.lead.stage)}</span></div>
        <button className="icon-only" aria-label="关闭会话" title="关闭会话" onClick={props.onClose}><X size={16} /></button>
      </header>

      <div className="readiness-strip">
        <span className={props.account?.private_channel_configured ? "ready" : "not-ready"}><LockKeyhole size={13} />{props.account?.private_channel_configured ? "私域渠道已配置" : "私域渠道未配置"}</span>
        <span>{props.lead.human_required ? "人工接管中" : "AI 处理中"}</span>
      </div>

      <div className="message-history" aria-label="有限消息记录">
        {props.lead.messages.map((message) => (
          <div className={`message-line message-${message.direction}`} key={message.message_id}>
            <span>{localizeValue(message.direction)}</span><p>{message.text || `[${message.message_type}]`}</p>
          </div>
        ))}
      </div>

      {props.lead.draft && (
        <section className="draft-band">
          <div><LockKeyhole size={13} /><strong>不可变待发送草稿</strong><span className={`status-label status-${props.lead.draft.state}`}><span />{localizeValue(props.lead.draft.state)}</span></div>
          <p>{props.lead.draft.reply_text || "草稿生成中。"}</p>
          <small>计划 #{props.lead.draft.plan_id}；发送状态由服务端回报。</small>
        </section>
      )}

      <section className="drawer-action-band">
        <header><Hand size={14} /><strong>人工处理</strong></header>
        {!props.lead.human_required && <button className="action-button warning" disabled={busy} onClick={props.onTakeover}><Hand size={14} />{props.action === "takeover" ? "接管中..." : "人工接管"}</button>}
        <label htmlFor="manual-reply">人工回复</label>
        <textarea id="manual-reply" disabled={!props.lead.human_required || busy} onChange={(event) => setReply(event.target.value)} value={reply} />
        <button className="action-button positive" disabled={!props.lead.human_required || busy || !reply.trim() || !props.canCreatePlan} onClick={() => props.onManualPlan(reply.trim())}><Send size={14} />{props.action === "manual" ? "创建中..." : "创建发送计划"}</button>
        <small>此操作会创建不可变计划；浏览器回报发送结果前保持等待状态。</small>
        {!props.canCreatePlan && <small className="cell-error">有限消息记录中没有收到的消息。</small>}
      </section>

      <section className="drawer-action-band sale-band">
        <header><BadgeDollarSign size={14} /><strong>成交结果</strong></header>
        <div className="sale-fields">
          <label>金额<input aria-label="成交金额" inputMode="decimal" min="0.01" step="0.01" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
          <label>币种<input aria-label="成交币种" maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></label>
          <label>状态<select aria-label="成交状态" value={saleStatus} onChange={(event) => setSaleStatus(event.target.value)}><option value="confirmed">已确认</option><option value="pending">待确认</option><option value="refunded">已退款</option><option value="cancelled">已取消</option></select></label>
        </div>
        <button className="action-button" disabled={busy || Number(amount) <= 0 || currency.length !== 3} onClick={() => props.onSale(amount, currency, saleStatus)}><BadgeDollarSign size={14} />{props.action === "sale" ? "记录中..." : "记录成交"}</button>
      </section>
      {props.notice && <div className="action-notice" role="status">{props.notice}</div>}
      {props.error && <div className="action-error" role="alert">{props.error}</div>}
    </aside>
  );
}
