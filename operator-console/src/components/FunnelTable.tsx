import type { FunnelSnapshot } from "../api";

const rows: Array<[keyof FunnelSnapshot, string]> = [
  ["followers", "新增关注"], ["dm_inbound", "收到私信"], ["qualified", "合格线索"],
  ["invited", "私域邀请"], ["contact_captured", "已留联系方式"], ["human_required", "人工接管"],
];

export function FunnelTable({ funnel }: { funnel: FunnelSnapshot }) {
  return (
    <div className="table-frame" data-testid="funnel-table">
      <table className="operations-table funnel-table" aria-label="线索漏斗">
        <thead><tr><th>漏斗事件</th><th className="align-right">实测数量</th></tr></thead>
        <tbody>{rows.map(([key, label]) => <tr key={key}><td>{label}</td><td className="align-right metric-value">{funnel[key] ?? "暂无记录"}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
