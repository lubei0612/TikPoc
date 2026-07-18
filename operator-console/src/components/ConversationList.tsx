import { MessageSquareText } from "lucide-react";

import type { LeadConversation } from "../api";

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
              <span className="conversation-signals"><small>{conversation.stage === "invited" ? "Invited" : "Invitation --"}</small><small>{["contact_captured", "closed"].includes(conversation.stage) ? "Contact captured" : "Contact --"}</small><small>Reply latency --</small></span>
            </span>
            <span className={`stage-tag stage-${conversation.stage}`}>{conversation.stage.replaceAll("_", " ")}</span>
            <span className={`status-label ${conversation.human_required ? "status-degraded" : "status-healthy"}`}><span />{conversation.human_required ? "Human" : "AI"}</span>
          </button>
        );
      })}
    </div>
  );
}
