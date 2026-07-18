import { Gauge, TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { getLeads, getOperations, type LeadInboxSnapshot, type OperationsSnapshot } from "../api";
import { FunnelTable } from "../components/FunnelTable";
import { localizeError, localizeValue } from "../localization";

const formatMoney = (currency: string, minor: number) => `${currency} ${(minor / 100).toFixed(2)}`;

export function evaluatePromotion(operations: OperationsSnapshot) {
  if (operations.devices.length === 0 || operations.devices.some((device) => device.mean_ms <= 0 || device.p90_ms <= 0)) {
    return { promoted: false, reason: "时延数据不足" };
  }
  const coverage = operations.coverage;
  if (coverage.required_devices <= 0 || coverage.targets <= 0) return { promoted: false, reason: "覆盖数据不足" };
  if (coverage.fully_covered !== coverage.targets || coverage.fully_completed !== coverage.targets) {
    return { promoted: false, reason: "覆盖门槛未通过" };
  }
  if (operations.devices.some((device) => device.mean_ms >= 6_500 || device.p90_ms >= 8_640)) {
    return { promoted: false, reason: "时延门槛未通过" };
  }
  return { promoted: false, reason: "身份、路由与动作证据不足" };
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
      .catch((reason: unknown) => setError(localizeError(reason, "经营分析加载失败")));
    return () => controller.abort();
  }, [roundId]);

  const metrics = useMemo(() => {
    if (!operations) return null;
    const slowestMean = Math.max(0, ...operations.devices.map((device) => device.mean_ms));
    const projected = slowestMean > 0 ? Math.floor(86_400_000 / slowestMean) : null;
    return { projected, ...evaluatePromotion(operations) };
  }, [operations]);

  if (error) return <div className="workspace-state error-state" role="alert">{error}</div>;
  if (!operations || !leads || !metrics) return <div className="workspace-state"><span className="loading-line" />正在加载经营分析</div>;

  const required = operations.coverage.required_devices;
  const covered = operations.coverage.fully_covered;
  const revenue = Object.entries(leads.sales.confirmed_revenue_minor);
  const revenueText = revenue.length ? revenue.map(([currency, minor]) => formatMoney(currency, minor)).join(" · ") : "暂无记录";
  const revenuePerThousandText = covered > 0 && revenue.length
    ? revenue.map(([currency, minor]) => formatMoney(currency, minor * 1_000 / covered)).join(" · ")
    : "暂无记录";
  const saleOutcomes = Object.entries(leads.sales.by_status).map(([status, count]) => `${localizeValue(status)} ${count}`).join(" · ") || "暂无成交结果";

  return (
    <main className="analytics-workspace">
      <header className="workspace-title"><div><span className="section-index">分析</span><h1>获客经营分析</h1><p>实测证据与容量预测分开展示。</p></div><span className={`promotion-label ${metrics.promoted ? "promoted" : "not-promoted"}`} title={metrics.reason}><Gauge size={14} /><span>{metrics.promoted ? "已达推广门槛" : "未达推广门槛"}<small>{metrics.reason}</small></span></span></header>
      <div className="table-frame evidence-summary-frame">
        <table className="operations-table evidence-summary-table" aria-label="获客证据">
          <thead><tr><th>指标</th><th>证据类型</th><th className="align-right">数值</th><th>说明</th></tr></thead>
          <tbody>
            <tr><td>实测完成数</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{operations.coverage.fully_completed.toLocaleString()}</td><td>{covered.toLocaleString()} 个目标达到 {required}/{required}</td></tr>
            <tr><td>完整覆盖率</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{Math.round(operations.coverage.coverage_rate * 100)}%</td><td>{covered.toLocaleString()} 个目标完整覆盖</td></tr>
            <tr><td>实测确认访问数</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{operations.coverage.confirmed_visits.toLocaleString()}</td><td>持久化访问确认</td></tr>
            <tr><td>预计日容量</td><td><span className="status-label status-degraded"><span />预测</span></td><td className="align-right metric-value">{metrics.projected?.toLocaleString() ?? "暂无记录"}</td><td>基于最慢设备平均耗时，并非实测结果</td></tr>
            <tr><td>成交数</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{leads.sales.sales.toLocaleString()}</td><td>{saleOutcomes}</td></tr>
            <tr><td>确认收入</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{revenueText}</td><td>已确认成交结果</td></tr>
            <tr><td>每千个完整覆盖目标收入</td><td><span className="status-label status-healthy"><span />实测</span></td><td className="align-right metric-value">{revenuePerThousandText}</td><td>按实测完整覆盖目标数归一化</td></tr>
          </tbody>
        </table>
      </div>

      <section className="analytics-grid">
        <div className="analytics-pane"><header><TrendingUp size={15} /><div><h2>线索漏斗</h2><p>来自已配置账号的持久化事件。</p></div></header><FunnelTable funnel={leads.funnel} /></div>
        <div className="analytics-pane"><header><Gauge size={15} /><div><h2>设备容量</h2><p>时延门槛：实测平均值 &lt; 6.5秒，P90 &lt; 8.64秒。</p></div></header><div className="table-frame"><table className="operations-table capacity-table" aria-label="设备容量"><thead><tr><th>设备</th><th className="align-right">平均值</th><th className="align-right">P90</th><th>状态</th></tr></thead><tbody>{operations.devices.map((device) => { const sampled = device.mean_ms > 0 && device.p90_ms > 0; const passed = sampled && device.mean_ms < 6_500 && device.p90_ms < 8_640; return <tr key={device.device_id}><td><strong>{device.device_id}</strong><small>{device.account_id || "未绑定账号"}</small></td><td className="align-right">{sampled ? `${(device.mean_ms / 1_000).toFixed(2)}秒` : "--"}</td><td className="align-right">{sampled ? `${(device.p90_ms / 1_000).toFixed(2)}秒` : "--"}</td><td><span className={`status-label ${passed ? "status-healthy" : "status-degraded"}`}><span />{!sampled ? "暂无样本" : passed ? "时延达标" : "时延未达标"}</span></td></tr>; })}</tbody></table></div></div>
      </section>
    </main>
  );
}
