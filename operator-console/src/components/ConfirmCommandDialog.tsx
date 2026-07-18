interface ConfirmCommandDialogProps {
  label: string;
  subject: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmCommandDialog({ label, subject, onCancel, onConfirm }: ConfirmCommandDialogProps) {
  return (
    <div className="command-dialog-backdrop">
      <div aria-label={`Confirm ${label}`} aria-modal="true" className="command-dialog" role="dialog">
        <span className="eyebrow">Confirm command</span>
        <strong>Stop {subject}</strong>
        <p>This changes the persisted control state for {subject}.</p>
        <div>
          <button className="action-button" onClick={onCancel} type="button">Cancel</button>
          <button aria-label={`Confirm ${label}`} className="action-button warning" onClick={onConfirm} type="button">Confirm</button>
        </div>
      </div>
    </div>
  );
}
