import { Gauge, TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { getLeads, getOperations, type LeadInboxSnapshot, type OperationsSnapshot } from "../api";
import { FunnelTable } from "../components/FunnelTable";

const formatMoney = (currency: string, minor: number) => `${currency} ${(minor / 100).toFixed(2)}`;

export function evaluatePromotion(operations: OperationsSnapshot) {
  if (operations.devices.length === 0 || operations.devices.some((device) => device.mean_ms <= 0 || device.p90_ms <= 0)) {
    return { promoted: false, reason: "Insufficient timing evidence" };
  }
  const coverage = operations.coverage;
  if (coverage.required_devices <= 0 || coverage.targets <= 0) return { promoted: false, reason: "Insufficient coverage evidence" };
  if (coverage.fully_covered !== coverage.targets || coverage.fully_completed !== coverage.targets) {
    return { promoted: false, reason: "Coverage gate failed" };
  }
  if (operations.devices.some((device) => device.mean_ms >= 6_500 || device.p90_ms >= 8_640)) {
    return { promoted: false, reason: "Timing gate failed" };
  }
  // Identity, route, and action-verification gates are not yet fields in this read model.
  return { promoted: false, reason: "Insufficient identity, route and action evidence" };
}

export function AnalyticsView({ roundId }: { roundId: string }) {
  const [operations, setOperations] = useState<OperationsSnapshot | null>(null);
  const [leads, setLeads] = useState<LeadInboxSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setOperations(null);
    setError(null);
    Promise.all([getOperations(roundId, controller.signal), getLeads(undefined, controller.signal)])
      .then(([nextOperations, nextLeads]) => { setOperations(nextOperations); setLeads(nextLeads); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Analytics unavailable"));
    return () => controller.abort();
  }, [roundId]);

  const metrics = useMemo(() => {
    if (!operations) return null;
    const slowestMean = Math.max(0, ...operations.devices.map((device) => device.mean_ms));
    const projected = slowestMean > 0 ? Math.floor(86_400_000 / slowestMean) : null;
    return { slowestMean, projected, ...evaluatePromotion(operations) };
  }, [operations]);

  if (error) return <div className="workspace-state error-state" role="alert">{error}</div>;
  if (!operations || !leads || !metrics) return <div className="workspace-state"><span className="loading-line" />Loading analytics</div>;
  const required = operations.coverage.required_devices;
  const covered = operations.coverage.fully_covered;
  const revenue = Object.entries(leads.sales.confirmed_revenue_minor);

  return (
    <main className="analytics-workspace">
      <header className="workspace-title"><div><span className="section-index">ANALYTICS</span><h1>Acquisition economics</h1><p>Measured evidence and capacity projection remain separate.</p></div><span className={`promotion-label ${metrics.promoted ? "promoted" : "not-promoted"}`} title={metrics.reason}><Gauge size={14} /><span>{metrics.promoted ? "Promoted" : "Not promoted"}<small>{metrics.reason}</small></span></span></header>
      <section className="capacity-band">
        <div><span>Measured completions</span><strong>{operations.coverage.fully_completed.toLocaleString()}</strong><small>{covered.toLocaleString()} targets at {required}/{required}</small></div>
        <div><span>Projected daily capacity</span><strong>{metrics.projected?.toLocaleString() ?? "--"}</strong><small>Based on slowest device mean · not a measured result</small></div>
        <div><span>Coverage</span><strong>{Math.round(operations.coverage.coverage_rate * 100)}%</strong><small>{operations.coverage.confirmed_visits.toLocaleString()} confirmed visits</small></div>
        <div><span>Sales</span><strong>{leads.sales.sales.toLocaleString()}</strong><small>{Object.entries(leads.sales.by_status).map(([status, count]) => `${status} ${count}`).join(" · ") || "No sale outcomes"}</small></div>
      </section>

      <section className="analytics-grid">
        <div className="analytics-pane"><header><TrendingUp size={15} /><div><h2>Lead funnel</h2><p>Durable events from configured accounts.</p></div></header><FunnelTable funnel={leads.funnel} /></div>
        <div className="analytics-pane"><header><Gauge size={15} /><div><h2>Device capacity</h2><p>Timing gate: measured mean &lt; 6.5s and p90 &lt; 8.64s.</p></div></header><div className="table-frame"><table className="operations-table capacity-table"><thead><tr><th>Device</th><th className="align-right">Mean</th><th className="align-right">P90</th><th>Status</th></tr></thead><tbody>{operations.devices.map((device) => { const sampled = device.mean_ms > 0 && device.p90_ms > 0; const passed = sampled && device.mean_ms < 6_500 && device.p90_ms < 8_640; return <tr key={device.device_id}><td><strong>{device.device_id}</strong><small>{device.account_id || "No account"}</small></td><td className="align-right">{sampled ? `${(device.mean_ms / 1000).toFixed(2)}s` : "--"}</td><td className="align-right">{sampled ? `${(device.p90_ms / 1000).toFixed(2)}s` : "--"}</td><td><span className={`status-label ${passed ? "status-healthy" : "status-degraded"}`}><span />{!sampled ? "No sample" : passed ? "Timing pass" : "Timing fail"}</span></td></tr>; })}</tbody></table></div></div>
      </section>

      <section className="revenue-band">
        <div><span>Confirmed revenue</span><strong>{revenue.length ? revenue.map(([currency, minor]) => formatMoney(currency, minor)).join(" · ") : "--"}</strong></div>
        <div><span>Revenue per 1,000 fully covered targets</span><strong>{covered > 0 && revenue.length ? revenue.map(([currency, minor]) => formatMoney(currency, minor * 1000 / covered)).join(" · ") : "--"}</strong></div>
      </section>
    </main>
  );
}
