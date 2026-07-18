import { RefreshCw, ShieldCheck, Smartphone, Target } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
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

  const setPending = (key: string, pending: boolean) => {
    setPendingKeys((current) => {
      const next = new Set(current);
      if (pending) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [nextSnapshot, nextCoverage] = await Promise.all([
        getOperations(roundId, signal),
        getCoverage(roundId, signal),
      ]);
      setSnapshot(nextSnapshot);
      setCoverage(nextCoverage);
      onHealthChange?.({
        healthyDevices: nextSnapshot.devices.filter((device) => device.health === "healthy").length,
        totalDevices: nextSnapshot.devices.length,
        healthyBrowserObservers: nextSnapshot.browser_health.filter((observer) => observer.status === "healthy").length,
        totalBrowserObservers: nextSnapshot.browser_health.length,
      });
      setLoadError(null);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setLoadError(error instanceof Error ? error.message : "Operations data is unavailable");
    } finally {
      setLoading(false);
    }
  }, [onHealthChange, roundId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function runControl(action: CommandAction, scope: "device" | "fleet" | "round", scopeId?: string) {
    const key = scope === "device" ? `device:${scopeId}:${action}` : `${scope}:${action}`;
    const commandId = crypto.randomUUID();
    setPending(key, true);
    setCommandErrors((current) => ({ ...current, [key]: "" }));
    try {
      await postCommand({
        action,
        commandId,
        scope,
        scopeId: scopeId || (scope === "fleet" ? "all" : roundId),
      });
      await load();
    } catch (error) {
      setCommandErrors((current) => ({
        ...current,
        [key]: error instanceof ApiError || error instanceof Error ? error.message : "Command failed",
      }));
    } finally {
      setPending(key, false);
    }
  }

  async function retryAssignment(assignmentId: number) {
    const key = `retry:${assignmentId}`;
    setPending(key, true);
    setRowErrors((current) => ({ ...current, [assignmentId]: "" }));
    try {
      await postCommand({ action: "retry", commandId: crypto.randomUUID(), assignmentId });
      await load();
    } catch (error) {
      setRowErrors((current) => ({
        ...current,
        [assignmentId]: error instanceof Error ? error.message : "Retry failed",
      }));
    } finally {
      setPending(key, false);
    }
  }

  if (loading && !snapshot) return <div className="workspace-state"><span className="loading-line" />Loading operations</div>;
  if (!snapshot || !coverage) return <div className="workspace-state error-state" role="alert">{loadError || "Round data is unavailable"}</div>;

  const healthyDevices = snapshot.devices.filter((device) => device.health === "healthy").length;
  const stateLabel = snapshot.round.state[0].toUpperCase() + snapshot.round.state.slice(1);
  return (
    <main className="operations-workspace">
      <section className="round-strip">
        <div><span className="eyebrow">Round state</span><span className={`state-block state-${snapshot.round.state}`}>{stateLabel}</span></div>
        <div><Target size={16} aria-hidden="true" /><span><strong>{snapshot.coverage.fully_covered}</strong> / {snapshot.coverage.targets} targets covered</span></div>
        <div><Smartphone size={16} aria-hidden="true" /><span><strong>{healthyDevices}</strong> / {snapshot.devices.length} devices healthy</span></div>
        <div><ShieldCheck size={16} aria-hidden="true" /><span><strong>{Math.round(snapshot.coverage.coverage_rate * 100)}%</strong> coverage</span></div>
        <button aria-label="Refresh operations" className="icon-only" onClick={() => void load()} title="Refresh operations" type="button"><RefreshCw size={17} /></button>
      </section>

      <CommandBar errors={commandErrors} onCommand={runControl} pendingKeys={pendingKeys} roundState={snapshot.round.state} />
      {loadError && <p className="stale-warning" role="status">Showing last confirmed snapshot. Refresh failed: {loadError}</p>}

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">01</span><div><h2>Device runtime</h2><p>Identity, visible state and active assignment diagnostics</p></div></div><span>{snapshot.devices.length} configured</span></header>
        <DeviceTable
          devices={snapshot.devices}
          errors={commandErrors}
          onCommand={(action, deviceId) => runControl(action, "device", deviceId)}
          pendingKeys={pendingKeys}
        />
      </section>

      <section className="workspace-section split-section">
        <div>
          <header className="section-heading"><div><span className="section-index">02</span><div><h2>Rolling quotas</h2><p>Current one-hour reservation windows</p></div></div></header>
          <QuotaTable quotas={snapshot.quotas} />
        </div>
        <aside className="coverage-ledger">
          <span className="eyebrow">Coverage ledger</span>
          <dl>
            <div><dt>Confirmed visits</dt><dd>{snapshot.coverage.confirmed_visits}</dd></div>
            <div><dt>Completed actions</dt><dd>{snapshot.coverage.completed_assignments}</dd></div>
            <div><dt>Required per target</dt><dd>{snapshot.coverage.required_devices}</dd></div>
            <div><dt>Fully complete</dt><dd>{snapshot.coverage.fully_completed}</dd></div>
          </dl>
        </aside>
      </section>

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">03</span><div><h2>Target coverage</h2><p>Per-account visit evidence and retryable assignments</p></div></div><span>{coverage.total} targets</span></header>
        <CoverageTable coverage={coverage} onRetry={retryAssignment} pendingKeys={pendingKeys} rowErrors={rowErrors} />
      </section>

      <section className="workspace-section">
        <header className="section-heading"><div><span className="section-index">04</span><div><h2>Runtime evidence</h2><p>Confirmed mobile traces and visible browser observers</p></div></div></header>
        <RuntimeEvidence browserHealth={snapshot.browser_health} traces={snapshot.recent_mobile_traces} />
      </section>
    </main>
  );
}
