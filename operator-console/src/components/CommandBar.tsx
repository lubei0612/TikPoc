import { CirclePause, CirclePlay, Octagon, RadioTower } from "lucide-react";

import type { CommandAction, RoundState } from "../api";

interface CommandBarProps {
  roundState: RoundState;
  pendingKey: string | null;
  error: string | null;
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

export function CommandBar({ roundState, pendingKey, error, onCommand }: CommandBarProps) {
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
                return (
                  <button
                    aria-label={`${label} ${scope}`}
                    className={`icon-command ${tone}`}
                    disabled={pendingKey !== null || (roundState === "completed" && scope === "round")}
                    key={action}
                    onClick={() => onCommand(action, scope)}
                    title={`${label} ${scope}`}
                    type="button"
                  >
                    <Icon size={16} aria-hidden="true" />
                    <span>{pendingKey === key ? "Working" : label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
  );
}
