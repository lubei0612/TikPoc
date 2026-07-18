import type { Quota } from "../api";

export function QuotaTable({ quotas }: { quotas: Quota[] }) {
  return (
    <div className="table-frame compact-table-frame">
      <table className="operations-table quota-table">
        <thead><tr><th>Device</th><th>Outcome</th><th>Usage</th><th>Confirmed</th><th>Remaining</th></tr></thead>
        <tbody>
          {quotas.map((quota) => {
            const ratio = Math.min(100, (quota.reserved / quota.limit) * 100);
            return (
              <tr key={`${quota.device_id}:${quota.outcome}`}>
                <td><strong>{quota.device_id}</strong></td>
                <td className="capitalize">{quota.outcome}</td>
                <td>
                  <div className="quota-usage"><span>{quota.reserved}/{quota.limit}</span><i><b style={{ width: `${ratio}%` }} /></i></div>
                </td>
                <td>{quota.confirmed}{quota.uncertain > 0 && <small className="uncertain-count"> +{quota.uncertain} uncertain</small>}</td>
                <td><strong>{quota.remaining}</strong></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
