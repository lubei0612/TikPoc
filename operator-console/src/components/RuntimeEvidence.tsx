import { CheckCircle2, CircleDotDashed, MessagesSquare, Route } from "lucide-react";

import type { BrowserHealth, MobileTrace } from "../api";
import { localizeValue } from "../localization";

const time = (timestamp: number) =>
  new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(timestamp);

export function RuntimeEvidence({ traces, browserHealth }: { traces: MobileTrace[]; browserHealth: BrowserHealth[] }) {
  return (
    <div className="evidence-grid">
      <div className="evidence-pane">
        <header><div><Route size={15} /><strong>移动访问留痕</strong></div><span>最近 {traces.length} 条</span></header>
        <div className="table-frame evidence-scroll">
          <table className="operations-table evidence-table" data-testid="mobile-traces">
            <thead><tr><th>目标</th><th>访问证据</th><th>完成动作</th><th>确认时间</th></tr></thead>
            <tbody>
              {traces.length === 0 && <tr><td colSpan={4} className="empty-cell">暂无已确认的移动访问</td></tr>}
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
        <header><div><MessagesSquare size={15} /><strong>浏览器健康</strong></div><span>{browserHealth.length} 个观察器</span></header>
        <div className="table-frame evidence-scroll">
          <table className="operations-table evidence-table" data-testid="browser-health">
            <thead><tr><th>账号 / Profile</th><th>页面</th><th>预期 / 页面用户</th><th>状态</th><th>观察时间</th></tr></thead>
            <tbody>
              {browserHealth.length === 0 && <tr><td colSpan={5} className="empty-cell">暂无浏览器观察器心跳</td></tr>}
              {browserHealth.map((health) => (
                <tr key={`${health.account_id}:${health.page_role}`}>
                  <td><strong>{health.account_id}</strong><small>{health.browser_profile_label || "未命名 Profile"}</small><small>{health.device_id}</small></td>
                  <td>{localizeValue(health.page_role)}</td>
                  <td className="browser-identities"><span>{health.expected_tiktok_username ? `@${health.expected_tiktok_username}` : "-"}</span><small>{health.observed_username ? `@${health.observed_username}` : "-"}</small></td>
                  <td><span className={`status-label status-${health.binding_state}`} title={`详情：${health.detail}`}><span aria-hidden="true" />{localizeValue(health.binding_state)}</span></td>
                  <td>{health.observed_at_ms ? time(health.observed_at_ms) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
