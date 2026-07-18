import { Activity, ChevronDown, ChevronUp, Gauge, MonitorCog } from "lucide-react";
import { useState } from "react";

import type { Device } from "../api";

interface DeviceTableProps {
  devices: Device[];
}

const duration = (milliseconds: number) =>
  milliseconds > 0 ? `${(milliseconds / 1000).toFixed(1)}s` : "--";

export function DeviceTable({ devices }: DeviceTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="table-frame responsive-kv-bands" data-testid="device-table">
      <table className="operations-table device-table">
        <thead>
          <tr>
            <th>Device / account</th>
            <th>Health</th>
            <th>Current assignment</th>
            <th>Timing</th>
            <th className="align-right">Inspect</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((device) => {
            const active = expanded === device.device_id;
            const assignment = device.current_assignment;
            return (
              <DeviceRows
                active={active}
                device={device}
                key={device.device_id}
                onToggle={() => setExpanded(active ? null : device.device_id)}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DeviceRows({ device, active, onToggle }: { device: Device; active: boolean; onToggle: () => void }) {
  const assignment = device.current_assignment;
  const diagnostic = device.latest_diagnostic;
  return (
    <>
      <tr className="device-row">
        <td data-label="Device / account">
          <div className="identity-cell">
            <span className="device-mark"><MonitorCog size={16} aria-hidden="true" /></span>
            <div><strong>{device.device_id}</strong><small>{device.account_id || "Unbound account"}</small></div>
          </div>
        </td>
        <td data-label="Health">
          <span className={`status-label status-${device.health}`}>
            <span aria-hidden="true" />{device.health}
          </span>
          {device.health_error_code && <small className="cell-note">{device.health_error_code}</small>}
        </td>
        <td data-label="Current assignment">
          {assignment ? (
            <div className="assignment-cell">
              <strong>#{assignment.assignment_id}</strong>
              <span>{assignment.phase.replaceAll("_", " ")}</span>
              <small>{assignment.identity_key}</small>
            </div>
          ) : <span className="muted">Idle</span>}
        </td>
        <td data-label="Timing">
          <div className="timing-pair">
            <span><Activity size={13} aria-hidden="true" />mean {duration(device.mean_ms)}</span>
            <span><Gauge size={13} aria-hidden="true" />p90 {duration(device.p90_ms)}</span>
          </div>
        </td>
        <td className="align-right" data-label="Inspect">
          <button
            aria-expanded={active}
            aria-label={`Diagnostics for ${device.device_id}`}
            className="icon-only"
            disabled={!diagnostic}
            onClick={onToggle}
            title={`Diagnostics for ${device.device_id}`}
            type="button"
          >
            {active ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </button>
        </td>
      </tr>
      {active && diagnostic && (
        <tr className="diagnostic-row">
          <td colSpan={5}>
            <div className="diagnostic-strip">
              <span className={`status-label status-${diagnostic.result}`}><span aria-hidden="true" />{diagnostic.result}</span>
              <p>{diagnostic.ui_summary || "No visible UI summary recorded."}</p>
              <code>{diagnostic.screenshot_id ? `capture ${diagnostic.screenshot_id}` : "no capture"}</code>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
