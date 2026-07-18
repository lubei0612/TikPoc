import { MessageSquareText } from "lucide-react";

import type { LeadConversation } from "../api";

const formatDuration = (durationMs: number) => {
  const seconds = Math.floor(Math.max(0, durationMs) / 1_000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
};

interface ConversationListProps {
  conversations: LeadConversation[];
  selectedId: string | null;
  onSelect: (conversation: LeadConversation) => void;
}

export function ConversationList({ conversations, selectedId, onSelect }: ConversationListProps) {
  return (
    <div className="conversation-list" aria-label="Lead conversations">
      {conversations.length === 0 && <div className="empty-inbox">No lead conversations recorded.</div>}
      {conversations.map((conversation) => {
        const selected = conversation.conversation_id === selectedId;
        return (
          <button
            aria-label={`Open ${conversation.participant_username} on ${conversation.account_id}`}
            aria-pressed={selected}
            className="conversation-row"
            key={`${conversation.account_id}:${conversation.conversation_id}`}
            onClick={() => onSelect(conversation)}
          >
            <span className="conversation-mark"><MessageSquareText size={15} /></span>
            <span className="conversation-copy">
              <span><strong>{conversation.participant_username}</strong><small>{conversation.account_id}</small></span>
              <span className="conversation-preview">{conversation.last_message_preview || "No message preview"}</span>
              <span className="conversation-signals">
                <small>{conversation.invitation_seen ? "Invitation seen" : "No invitation"}</small>
                <small>{conversation.contact_captured ? "Contact captured" : "No contact captured"}</small>
                <small>{conversation.reply_wait_ms === null ? "Reply wait complete" : `Reply wait ${formatDuration(conversation.reply_wait_ms)}`}</small>
                <small>{conversation.last_message_age_ms === null ? "No messages" : `Last message ${formatDuration(conversation.last_message_age_ms)} ago${conversation.last_message_direction ? ` · ${conversation.last_message_direction}` : ""}`}</small>
              </span>
            </span>
            <span className={`stage-tag stage-${conversation.stage}`}>{conversation.stage.replaceAll("_", " ")}</span>
            <span className={`status-label ${conversation.human_required ? "status-degraded" : "status-healthy"}`}><span />{conversation.human_required ? "Human" : "AI"}</span>
          </button>
        );
      })}
    </div>
  );
}
