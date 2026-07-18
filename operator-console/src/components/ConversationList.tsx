import { MessageSquareText } from "lucide-react";

import type { LeadConversation } from "../api";
import { localizeValue } from "../localization";

const formatDuration = (durationMs: number) => {
  const seconds = Math.floor(Math.max(0, durationMs) / 1_000);
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分钟`;
  return `${Math.floor(minutes / 60)}小时`;
};

interface ConversationListProps {
  conversations: LeadConversation[];
  selectedId: string | null;
  onSelect: (conversation: LeadConversation) => void;
}

export function ConversationList({ conversations, selectedId, onSelect }: ConversationListProps) {
  return (
    <div className="conversation-list" aria-label="线索会话">
      {conversations.length === 0 && <div className="empty-inbox">暂无线索会话。</div>}
      {conversations.map((conversation) => {
        const selected = conversation.conversation_id === selectedId;
        return (
          <button
            aria-label={`打开 ${conversation.participant_username} 在 ${conversation.account_id} 的会话`}
            aria-pressed={selected}
            className="conversation-row"
            key={`${conversation.account_id}:${conversation.conversation_id}`}
            onClick={() => onSelect(conversation)}
          >
            <span className="conversation-mark"><MessageSquareText size={15} /></span>
            <span className="conversation-copy">
              <span><strong>{conversation.participant_username}</strong><small>{conversation.account_id}</small></span>
              <span className="conversation-preview">{conversation.last_message_preview || "暂无消息预览"}</span>
              <span className="conversation-signals">
                <small>{conversation.invitation_seen ? "已发送私域邀请" : "尚未邀请私域"}</small>
                <small>{conversation.contact_captured ? "已获取联系方式" : "尚未获取联系方式"}</small>
                <small>{conversation.reply_wait_ms === null ? "回复等待已结束" : `等待回复 ${formatDuration(conversation.reply_wait_ms)}`}</small>
                <small>{conversation.last_message_age_ms === null ? "暂无消息" : `最后消息 ${formatDuration(conversation.last_message_age_ms)}前${conversation.last_message_direction ? ` · ${localizeValue(conversation.last_message_direction)}` : ""}`}</small>
              </span>
            </span>
            <span className={`stage-tag stage-${conversation.stage}`}>{localizeValue(conversation.stage)}</span>
            <span className={`status-label ${conversation.human_required ? "status-degraded" : "status-healthy"}`}><span />{conversation.human_required ? "人工" : "AI"}</span>
          </button>
        );
      })}
    </div>
  );
}
