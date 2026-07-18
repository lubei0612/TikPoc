import type { FunnelSnapshot } from "../api";

const rows: Array<[keyof FunnelSnapshot, string]> = [
  ["followers", "Followers"],
  ["dm_inbound", "Inbound DMs"],
  ["qualified", "Qualified leads"],
  ["invited", "Private-channel invitations"],
  ["contact_captured", "Captured contacts"],
  ["human_required", "Human takeovers"],
];

export function FunnelTable({ funnel }: { funnel: FunnelSnapshot }) {
  return (
    <div className="table-frame" data-testid="funnel-table">
      <table className="operations-table funnel-table" aria-label="Lead funnel">
        <thead><tr><th>Funnel event</th><th className="align-right">Measured count</th></tr></thead>
        <tbody>{rows.map(([key, label]) => <tr key={key}><td>{label}</td><td className="align-right metric-value">{funnel[key] ?? "Not recorded"}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
