import type { Quota } from "../api";
import { localizeValue } from "../localization";

export function QuotaTable({ quotas }: { quotas: Quota[] }) {
  return (
    <div className="table-frame compact-table-frame">
      <table className="operations-table quota-table">
        <thead><tr><th>设备</th><th>动作</th><th>滚动用量</th><th>结果</th><th>节奏</th><th>剩余</th></tr></thead>
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
                <td><strong>{quota.confirmed}</strong> 已确认{quota.uncertain > 0 && <small className="uncertain-count"> +{quota.uncertain} 待协调</small>}</td>
                <td>
                  <span className={`pacing-state ${quota.token_ready ? "is-ready" : "is-waiting"}`}>{quota.token_ready ? "可执行" : "等待补充"}</span>
                  <small>{quota.token_ready ? `权重 ${quota.candidate_weight}` : `下次 ${new Date(quota.next_due_at_ms).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}`}</small>
                </td>
                <td><strong>{quota.remaining}</strong></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
