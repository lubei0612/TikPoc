import { RefreshCw, ShieldCheck, Smartphone, Target } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getCoverage,
  getOperations,
  postCommand,
  type CommandAction,
  type CoverageSnapshot,
  type OperationsSnapshot,
} from "../api";
import { CommandBar } from "../components/CommandBar";
import { CoverageTable } from "../components/CoverageTable";
import { DeviceTable } from "../components/DeviceTable";
import { QuotaTable } from "../components/QuotaTable";
import { RuntimeEvidence } from "../components/RuntimeEvidence";
import { localizeError, localizeValue } from "../localization";

export interface FleetHealthSummary {
  healthyDevices: number;
  totalDevices: number;
  healthyBrowserObservers: number;
  totalBrowserObservers: number;
}

export function OperationsView({
  roundId,
  onHealthChange,
}: {
  roundId: string;
  onHealthChange?: (health: FleetHealthSummary) => void;
}) {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [coverage, setCoverage] = useState<CoverageSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [commandErrors, setCommandErrors] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(() => new Set());
  const [loadedRoundId, setLoadedRoundId] = useState<string | null>(null);
  const [failedRoundId, setFailedRoundId] = useState<string | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const healthChange = useRef(onHealthChange);
  const mounted = useRef(false);
  const requestGeneration = useRef(0);
  const selectedRoundId = useRef(roundId);
  healthChange.current = onHealthChange;
  selectedRoundId.current = roundId;

  const setPending = (key: string, pending: boolean) => {
    setPendingKeys((current) => {
      const next = new Set(current);
      if (pending) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const load = useCallback(async () => {
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    const generation = ++requestGeneration.current;
    const requestedRoundId = roundId;
    const isCurrent = () => mounted.current
      && !controller.signal.aborted
      && requestGeneration.current === generation
      && selectedRoundId.current === requestedRoundId;

    try {
      const [nextSnapshot, nextCoverage] = await Promise.all([
        getOperations(requestedRoundId, controller.signal),
        getCoverage(requestedRoundId, controller.signal),
      ]);
      if (!isCurrent()) return;
      setSnapshot(nextSnapshot);
      setCoverage(nextCoverage);
      setLoadedRoundId(requestedRoundId);
      setFailedRoundId(null);
      healthChange.current?.({
        healthyDevices: nextSnapshot.devices.filter((device) => device.health === "healthy").length,
        totalDevices: nextSnapshot.devices.length,
        healthyBrowserObservers: nextSnapshot.browser_health.filter((observer) => observer.status === "healthy").length,
        totalBrowserObservers: nextSnapshot.browser_health.length,
      });
      setLoadError(null);
    } catch (error) {
      if (!isCurrent() || (error instanceof DOMException && error.name === "AbortError")) return;
      setLoadError(localizeError(error, "运营数据加载失败"));
      setFailedRoundId(requestedRoundId);
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [roundId]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestGeneration.current += 1;
      activeController.current?.abort();
    };
  }, []);

  useEffect(() => {
    setLoading(true);
    setSnapshot(null);
    setCoverage(null);
    setLoadedRoundId(null);
    setFailedRoundId(null);
    setLoadError(null);
    setCommandErrors({});
    setRowErrors({});
    setPendingKeys(new Set());
    void load();
    return () => {
      requestGeneration.current += 1;
      activeController.current?.abort();
    };
  }, [load]);

  async function runControl(action: CommandAction, scope: "device" | "fleet" | "round", scopeId?: string) {
    const commandRoundId = roundId;
    const key = scope === "device" ? `device:${scopeId}:${action}` : `${scope}:${action}`;
    const commandId = crypto.randomUUID();
    setPending(key, true);
    setCommandErrors((current) => ({ ...current, [key]: "" }));
    try {
      await postCommand({
        action,
        commandId,
        scope,
        scopeId: scopeId || (scope === "fleet" ? "all" : commandRoundId),
      });
      if (mounted.current && selectedRoundId.current === commandRoundId) await load();
    } catch (error) {
      if (!mounted.current || selectedRoundId.current !== commandRoundId) return;
      setCommandErrors((current) => ({
        ...current,
        [key]: localizeError(error, "指令执行失败"),
      }));
    } finally {
      if (mounted.current && selectedRoundId.current === commandRoundId) setPending(key, false);
    }
  }

  async function retryAssignment(assignmentId: number) {
    const commandRoundId = roundId;
    const key = `retry:${assignmentId}`;
    setPending(key, true);
    setRowErrors((current) => ({ ...current, [assignmentId]: "" }));
    try {
      await postCommand({ action: "retry", commandId: crypto.randomUUID(), assignmentId });
      if (mounted.current && selectedRoundId.current === commandRoundId) await load();
    } catch (error) {
      if (!mounted.current || selectedRoundId.current !== commandRoundId) return;
      setRowErrors((current) => ({
        ...current,
        [assignmentId]: localizeError(error, "任务重试失败"),
      }));
    } finally {
      if (mounted.current && selectedRoundId.current === commandRoundId) setPending(key, false);
    }
  }

  if ((loadedRoundId !== roundId && failedRoundId !== roundId) || (loading && !snapshot)) return <div className="workspace-state"><span className="loading-line" />正在加载运营数据</div>;
  if (!snapshot || !coverage) return <div className="workspace-state error-state" role="alert">{loadError || "轮次数据暂不可用"}</div>;

  const healthyDevices = snapshot.devices.filter((device) => device.health === "healthy").length;
  const stateLabel = localizeValue(snapshot.round.state);
  return (
    <main className="operations-workspace">
      <section className="round-strip">
        <div><span className="eyebrow">轮次状态</span><span className={`state-block state-${snapshot.round.state}`}>{stateLabel}</span></div>
        <div><Target size={16} aria-hidden="true" /><span><strong>{snapshot.coverage.fully_covered}</strong> / {snapshot.coverage.targets} 个目标已覆盖</span></div>
        <div><Smartphone size={16} aria-hidden="true" /><span><strong>{healthyDevices}</strong> / {snapshot.devices.length} 台设备健康</span></div>
        <div><ShieldCheck size={16} aria-hidden="true" /><span><strong>{Math.round(snapshot.coverage.coverage_rate * 100)}%</strong> 覆盖率</span></div>
        <button aria-label="刷新运营数据" className="icon-only" onClick={() => void load()} title="刷新运营数据" type="button"><RefreshCw size={17} /></button>
      </section>

      <CommandBar errors={commandErrors} onCommand={runControl} pendingKeys={pendingKeys} roundState={snapshot.round.state} />
      {loadError && <p className="stale-warning" role="status">正在显示最近一次已确认快照。刷新失败：{loadError}</p>}

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">01</span><div><h2>设备运行状态</h2><p>账号身份、可见状态与当前任务诊断</p></div></div><span>已配置 {snapshot.devices.length} 台</span></header>
        <DeviceTable
          devices={snapshot.devices}
          errors={commandErrors}
          onCommand={(action, deviceId) => runControl(action, "device", deviceId)}
          pendingKeys={pendingKeys}
        />
      </section>

      <section className="workspace-section split-section">
        <div>
          <header className="section-heading"><div><span className="section-index">02</span><div><h2>自然小时配额</h2><p>当前固定小时预留窗口</p></div></div></header>
          <QuotaTable quotas={snapshot.quotas} />
        </div>
        <aside className="coverage-ledger">
          <span className="eyebrow">覆盖账本</span>
          <dl>
            <div><dt>确认访问</dt><dd>{snapshot.coverage.confirmed_visits}</dd></div>
            <div><dt>完成动作</dt><dd>{snapshot.coverage.completed_assignments}</dd></div>
            <div><dt>每目标所需账号</dt><dd>{snapshot.coverage.required_devices}</dd></div>
            <div><dt>全部完成</dt><dd>{snapshot.coverage.fully_completed}</dd></div>
          </dl>
        </aside>
      </section>

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">03</span><div><h2>目标覆盖</h2><p>逐账号访问证据与可重试任务</p></div></div><span>{coverage.total} 个目标</span></header>
        <CoverageTable coverage={coverage} onRetry={retryAssignment} pendingKeys={pendingKeys} rowErrors={rowErrors} />
      </section>

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">04</span><div><h2>运行证据</h2><p>已确认的移动留痕与可见浏览器观察器</p></div></div></header>
        <RuntimeEvidence browserHealth={snapshot.browser_health} traces={snapshot.recent_mobile_traces} />
      </section>
    </main>
  );
}
