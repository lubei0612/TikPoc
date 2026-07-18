import type { Quota } from "../api";
import { localizeValue } from "../localization";

export function QuotaTable({ quotas }: { quotas: Quota[] }) {
  return (
    <div className="table-frame compact-table-frame">
      <table className="operations-table quota-table">
        <thead><tr><th>设备</th><th>动作结果</th><th>用量</th><th>已确认</th><th>剩余</th></tr></thead>
        <tbody>
          {quotas.map((quota) => {
            const ratio = Math.min(100, (quota.reserved / quota.limit) * 100);
            return (
              <tr key={`${quota.device_id}:${quota.outcome}`}>
                <td><strong>{quota.device_id}</strong></td>
                <td>{localizeValue(quota.outcome)}</td>
                <td>
                  <div className="quota-usage"><span>{quota.reserved}/{quota.limit}</span><i><b style={{ width: `${ratio}%` }} /></i></div>
                </td>
                <td>{quota.confirmed}{quota.uncertain > 0 && <small className="uncertain-count"> +{quota.uncertain} 待确认</small>}</td>
                <td><strong>{quota.remaining}</strong></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
