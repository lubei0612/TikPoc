import { Check, Clock3, RotateCcw, TriangleAlert } from "lucide-react";

import type { CoverageAssignment, CoverageSnapshot } from "../api";

interface CoverageTableProps {
  coverage: CoverageSnapshot;
  pendingKey: string | null;
  rowErrors: Record<number, string>;
  onRetry: (assignmentId: number) => void;
}

function statusIcon(assignment: CoverageAssignment) {
  if (assignment.completed) return <Check size={14} aria-hidden="true" />;
  if (assignment.phase === "deferred") return <TriangleAlert size={14} aria-hidden="true" />;
  return <Clock3 size={14} aria-hidden="true" />;
}

export function CoverageTable({ coverage, pendingKey, rowErrors, onRetry }: CoverageTableProps) {
  const deviceIds = Array.from(new Set(coverage.items.flatMap((item) => item.devices.map((item) => item.device_id))));
  return (
    <div className="coverage-scroll" data-testid="coverage-scroller">
      <table className="operations-table coverage-table" data-testid="coverage-matrix">
        <thead><tr><th className="sticky-target" data-testid="coverage-target-header">Target</th>{deviceIds.map((id) => <th key={id}>{id}</th>)}</tr></thead>
        <tbody>
          {coverage.items.map((target) => (
            <tr key={target.identity_key}>
              <td className="sticky-target"><strong>{target.username}</strong><small>{target.identity_key}</small></td>
              {deviceIds.map((deviceId) => {
                const assignment = target.devices.find((item) => item.device_id === deviceId);
                if (!assignment) return <td key={deviceId}><span className="muted">Missing</span></td>;
                const retryable = assignment.phase === "deferred";
                return (
                  <td key={deviceId}>
                    <div className={`coverage-state coverage-${assignment.phase}`}>
                      <span>{statusIcon(assignment)}{assignment.phase.replaceAll("_", " ")}</span>
                      {assignment.last_error_code && <small>{assignment.last_error_code}</small>}
                      {retryable && (
                        <button
                          aria-label={`Retry ${deviceId} for ${target.username}`}
                          className="retry-button"
                          disabled={pendingKey !== null}
                          onClick={() => onRetry(assignment.assignment_id)}
                          title={`Retry ${deviceId} for ${target.username}`}
                          type="button"
                        >
                          <RotateCcw size={14} aria-hidden="true" />
                          {pendingKey === `retry:${assignment.assignment_id}` ? "Retrying" : "Retry"}
                        </button>
                      )}
                      {rowErrors[assignment.assignment_id] && <small className="cell-error" role="alert">{rowErrors[assignment.assignment_id]}</small>}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
