import { Activity, ChevronDown, ChevronUp, CirclePause, CirclePlay, Gauge, Image, MonitorCog, Octagon } from "lucide-react";
import { useState } from "react";

import type { CommandAction, Device } from "../api";
import { ConfirmCommandDialog } from "./ConfirmCommandDialog";
import { localizeValue } from "../localization";

interface DeviceTableProps {
  devices: Device[];
  pendingKeys: ReadonlySet<string>;
  errors: Record<string, string>;
  onCommand: (action: CommandAction, deviceId: string) => void;
}

const duration = (milliseconds: number) =>
  milliseconds > 0 ? `${(milliseconds / 1000).toFixed(1)}s` : "--";

export function DeviceTable({ devices, pendingKeys, errors, onCommand }: DeviceTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stopDeviceId, setStopDeviceId] = useState<string | null>(null);

  return (
    <div className="table-frame responsive-kv-bands" data-testid="device-table">
      <table className="operations-table device-table">
        <thead>
          <tr>
            <th>设备 / 账号</th><th>健康状态</th><th>当前任务</th><th>耗时</th><th>控制</th><th className="align-right">检查</th>
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
                errors={errors}
                key={device.device_id}
                onCommand={(action, deviceId) => {
                  if (action === "stop") setStopDeviceId(deviceId);
                  else onCommand(action, deviceId);
                }}
                onToggle={() => setExpanded(active ? null : device.device_id)}
                pendingKeys={pendingKeys}
              />
            );
          })}
        </tbody>
      </table>
      {stopDeviceId && (
        <ConfirmCommandDialog
          label={`停止 ${stopDeviceId}`}
          onCancel={() => setStopDeviceId(null)}
          onConfirm={() => {
            onCommand("stop", stopDeviceId);
            setStopDeviceId(null);
          }}
          subject={stopDeviceId}
        />
      )}
    </div>
  );
}

function DeviceRows({
  device,
  active,
  pendingKeys,
  errors,
  onCommand,
  onToggle,
}: {
  device: Device;
  active: boolean;
  pendingKeys: ReadonlySet<string>;
  errors: Record<string, string>;
  onCommand: (action: CommandAction, deviceId: string) => void;
  onToggle: () => void;
}) {
  const assignment = device.current_assignment;
  const diagnostic = device.latest_diagnostic;
  return (
    <>
      <tr className="device-row">
        <td data-label="设备 / 账号">
          <div className="identity-cell">
            <span className="device-mark"><MonitorCog size={16} aria-hidden="true" /></span>
            <div><strong>{device.device_id}</strong><small>{device.account_id || "未绑定账号"}</small></div>
          </div>
        </td>
        <td data-label="健康状态">
          <span className={`status-label status-${device.health}`}>
            <span aria-hidden="true" />{localizeValue(device.health)}
          </span>
          <small className={`control-state state-${device.control_state}`}>
            {localizeValue(device.control_state)}
          </small>
          {device.health_error_code && <small className="cell-note">{device.health_error_code}</small>}
        </td>
        <td data-label="当前任务">
          {assignment ? (
            <div className="assignment-cell">
              <strong>#{assignment.assignment_id}</strong>
              <span>{localizeValue(assignment.phase)}</span>
              <small>{assignment.identity_key}</small>
            </div>
          ) : <span className="muted">空闲</span>}
        </td>
        <td data-label="耗时">
          <div className="timing-pair">
            <span><Activity size={13} aria-hidden="true" />平均 {duration(device.mean_ms)}</span>
            <span><Gauge size={13} aria-hidden="true" />p90 {duration(device.p90_ms)}</span>
          </div>
        </td>
        <td data-label="控制">
          <div className="device-controls">
            {([
              ["start", "启动", CirclePlay], ["pause", "暂停", CirclePause], ["stop", "停止", Octagon],
            ] as const).map(([action, label, Icon]) => {
              const key = `device:${device.device_id}:${action}`;
              return (
                <div className="device-control" key={action}>
                  <button
                    aria-label={`${label} ${device.device_id}`}
                    className="icon-only"
                    disabled={pendingKeys.has(key)}
                    onClick={() => onCommand(action, device.device_id)}
                    title={`${label} ${device.device_id}`}
                    type="button"
                  >
                    <Icon size={15} aria-hidden="true" />
                  </button>
                  {errors[key] && <small className="cell-error" role="alert">{errors[key]}</small>}
                </div>
              );
            })}
          </div>
        </td>
        <td className="align-right" data-label="检查">
          <button
            aria-expanded={active}
            aria-label={`查看 ${device.device_id} 诊断信息`}
            className="icon-only"
            disabled={!diagnostic}
            onClick={onToggle}
            title={`查看 ${device.device_id} 诊断信息`}
            type="button"
          >
            {active ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </button>
        </td>
      </tr>
      {active && diagnostic && (
        <tr className="diagnostic-row">
          <td colSpan={6}>
            <div className="diagnostic-strip">
              <span className={`status-label status-${diagnostic.result}`}><span aria-hidden="true" />{localizeValue(diagnostic.result)}</span>
              <p>{diagnostic.ui_summary || "暂无可见界面摘要。"}</p>
              {diagnostic.screenshot_id ? (
                <button
                  aria-label={`截图证据 ${diagnostic.screenshot_id}`}
                  className="icon-only"
                  data-screenshot-id={diagnostic.screenshot_id}
                  onClick={() => window.open(
                    `/api/diagnostic-screenshots/${encodeURIComponent(diagnostic.screenshot_id!)}`,
                    "_blank",
                    "noopener,noreferrer",
                  )}
                  title={`截图证据 ${diagnostic.screenshot_id}`}
                  type="button"
                >
                  <Image size={16} aria-hidden="true" />
                </button>
              ) : <span className="muted">暂无截图</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
