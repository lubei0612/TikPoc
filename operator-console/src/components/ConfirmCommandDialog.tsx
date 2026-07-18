interface ConfirmCommandDialogProps {
  label: string;
  subject: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmCommandDialog({ label, subject, onCancel, onConfirm }: ConfirmCommandDialogProps) {
  return (
    <div className="command-dialog-backdrop">
      <div aria-label={`确认${label}`} aria-modal="true" className="command-dialog" role="dialog">
        <span className="eyebrow">确认指令</span>
        <strong>停止{subject}</strong>
        <p>此操作会修改{subject}的持久化控制状态。</p>
        <div>
          <button className="action-button" onClick={onCancel} type="button">取消</button>
          <button aria-label={`确认${label}`} className="action-button warning" onClick={onConfirm} type="button">确认</button>
        </div>
      </div>
    </div>
  );
}
