import { CheckCircle2, CircleDotDashed, MessagesSquare, Route } from "lucide-react";

import type { BrowserHealth, MobileTrace } from "../api";

const time = (timestamp: number) =>
  new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(timestamp);

export function RuntimeEvidence({ traces, browserHealth }: { traces: MobileTrace[]; browserHealth: BrowserHealth[] }) {
  return (
    <div className="evidence-grid">
      <div className="evidence-pane">
        <header><div><Route size={15} /><strong>Mobile visit traces</strong></div><span>{traces.length} recent</span></header>
        <div className="table-frame evidence-scroll">
          <table className="operations-table evidence-table" data-testid="mobile-traces">
            <thead><tr><th>Target</th><th>Visit proof</th><th>Action</th><th>Confirmed</th></tr></thead>
            <tbody>
              {traces.length === 0 && <tr><td colSpan={4} className="empty-cell">No confirmed mobile visits yet</td></tr>}
              {traces.map((trace) => (
                <tr key={trace.identity_key}>
                  <td><strong>{trace.username}</strong><small>{trace.identity_key}</small></td>
                  <td><span className={`proof-state ${trace.fully_covered ? "proof-complete" : "proof-partial"}`}>{trace.fully_covered ? <CheckCircle2 size={14} /> : <CircleDotDashed size={14} />}{trace.confirmed_devices}/{trace.required_devices}</span></td>
                  <td>{trace.completed_devices}/{trace.required_devices}</td>
                  <td>{time(trace.last_visit_confirmed_at_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="evidence-pane browser-pane">
        <header><div><MessagesSquare size={15} /><strong>Browser health</strong></div><span>{browserHealth.length} observers</span></header>
        <div className="table-frame evidence-scroll">
          <table className="operations-table evidence-table" data-testid="browser-health">
            <thead><tr><th>Account</th><th>Surface</th><th>Status</th><th>Observed</th></tr></thead>
            <tbody>
              {browserHealth.length === 0 && <tr><td colSpan={4} className="empty-cell">No browser observer heartbeat</td></tr>}
              {browserHealth.map((health) => (
                <tr key={`${health.account_id}:${health.page_role}`}>
                  <td><strong>{health.account_id}</strong><small>{health.device_id}</small></td>
                  <td className="capitalize">{health.page_role}</td>
                  <td><span className={`status-label status-${health.status}`} title={health.detail}><span aria-hidden="true" />{health.status}</span></td>
                  <td>{time(health.observed_at_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
