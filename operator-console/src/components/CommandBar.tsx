import { CirclePause, CirclePlay, Octagon, RadioTower } from "lucide-react";
import { useState } from "react";

import type { CommandAction, RoundState } from "../api";
import { ConfirmCommandDialog } from "./ConfirmCommandDialog";

interface CommandBarProps {
  roundState: RoundState;
  pendingKeys: ReadonlySet<string>;
  errors: Record<string, string>;
  onCommand: (action: CommandAction, scope: "fleet" | "round") => void;
}

const actions: Array<{
  action: CommandAction;
  label: string;
  Icon: typeof CirclePlay;
  tone: string;
}> = [
  { action: "start", label: "Start", Icon: CirclePlay, tone: "positive" },
  { action: "pause", label: "Pause", Icon: CirclePause, tone: "warning" },
  { action: "stop", label: "Stop", Icon: Octagon, tone: "danger" },
];

export function CommandBar({ roundState, pendingKeys, errors, onCommand }: CommandBarProps) {
  const [confirmation, setConfirmation] = useState<{ action: CommandAction; scope: "fleet" | "round" } | null>(null);
  return (
    <section className="command-band" aria-label="Fleet and round controls">
      <div className="command-context">
        <span className="context-icon"><RadioTower size={17} aria-hidden="true" /></span>
        <div>
          <span className="eyebrow">Selected round</span>
          <strong>{roundState[0].toUpperCase() + roundState.slice(1)}</strong>
        </div>
      </div>
      <div className="command-groups">
        {(["round", "fleet"] as const).map((scope) => (
          <div className="command-group" key={scope}>
            <span>{scope === "round" ? "Round" : "Fleet"}</span>
            <div className="segmented-actions">
              {actions.map(({ action, label, Icon, tone }) => {
                const key = `${scope}:${action}`;
                const pending = pendingKeys.has(key);
                return (
                  <div className="command-control" key={action}>
                    <button
                      aria-label={`${label} ${scope}`}
                      className={`icon-command ${tone}`}
                      disabled={pending || (roundState === "completed" && scope === "round")}
                      onClick={() => {
                        if (action === "stop") setConfirmation({ action, scope });
                        else onCommand(action, scope);
                      }}
                      title={`${label} ${scope}`}
                      type="button"
                    >
                      <Icon size={16} aria-hidden="true" />
                      <span>{pending ? "Working" : label}</span>
                    </button>
                    {errors[key] && <small className="cell-error" role="alert">{errors[key]}</small>}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {confirmation && (
        <ConfirmCommandDialog
          label={`${confirmation.action} ${confirmation.scope}`}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => {
            onCommand(confirmation.action, confirmation.scope);
            setConfirmation(null);
          }}
          subject={`the selected ${confirmation.scope}`}
        />
      )}
    </section>
  );
}
