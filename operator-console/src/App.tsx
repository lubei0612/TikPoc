import { Activity, BarChart3, Inbox, Network, RadioTower } from "lucide-react";
import { useEffect, useState } from "react";

import { getRounds, type RoundListItem } from "./api";
import { OperationsView, type FleetHealthSummary } from "./views/OperationsView";
import { AnalyticsView } from "./views/AnalyticsView";
import { InboxView } from "./views/InboxView";

type Tab = "operations" | "inbox" | "analytics";

const tabFromPath = (): Tab => {
  const tab = window.location.pathname.slice(1);
  return tab === "inbox" || tab === "analytics" ? tab : "operations";
};

export default function App() {
  const [rounds, setRounds] = useState<RoundListItem[]>([]);
  const [roundId, setRoundId] = useState("");
  const [tab, setTab] = useState<Tab>(tabFromPath);
  const [error, setError] = useState<string | null>(null);
  const [fleetHealth, setFleetHealth] = useState<FleetHealthSummary | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getRounds(controller.signal)
      .then(({ items }) => {
        setRounds(items);
        setRoundId((current) => current || items[0]?.round_id || "");
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Rounds unavailable"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const syncTab = () => setTab(tabFromPath());
    window.addEventListener("popstate", syncTab);
    return () => window.removeEventListener("popstate", syncTab);
  }, []);

  const selectTab = (nextTab: Tab) => {
    window.history.pushState(null, "", `/${nextTab}`);
    setTab(nextTab);
  };

  const notConnected = fleetHealth !== null
    && fleetHealth.totalDevices === 0
    && fleetHealth.totalBrowserObservers === 0;
  const healthLabel = notConnected
    ? "Fleet health: not connected"
    : fleetHealth
      ? `Fleet health: ${fleetHealth.healthyDevices} of ${fleetHealth.totalDevices} devices healthy; ${fleetHealth.healthyBrowserObservers} of ${fleetHealth.totalBrowserObservers} browser observers healthy`
      : "Fleet health unavailable";
  const healthState = fleetHealth && fleetHealth.totalDevices > 0 && fleetHealth.totalBrowserObservers > 0
    && fleetHealth.healthyDevices === fleetHealth.totalDevices
    && fleetHealth.healthyBrowserObservers === fleetHealth.totalBrowserObservers ? "healthy" : "degraded";
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup"><span><Network size={19} /></span><strong>TikPoc Ops</strong></div>
        <div className="topbar-context">
          <label htmlFor="round-select">Round</label>
          <select id="round-select" onChange={(event) => { setFleetHealth(null); setRoundId(event.target.value); }} value={roundId}>
            {rounds.map((round) => <option key={round.round_id} value={round.round_id}>{round.round_id} · {round.device_count} accounts</option>)}
          </select>
          <span aria-label={healthLabel} className={`global-health state-${healthState}`}><RadioTower size={14} />{notConnected ? "Not connected" : fleetHealth ? `${fleetHealth.healthyDevices}/${fleetHealth.totalDevices} devices · ${fleetHealth.healthyBrowserObservers}/${fleetHealth.totalBrowserObservers} browser` : "Health pending"}</span>
        </div>
      </header>
      <nav className="primary-nav" aria-label="Console views">
        <button aria-current={tab === "operations" ? "page" : undefined} onClick={() => selectTab("operations")}><Activity size={16} />Operations</button>
        <button aria-current={tab === "inbox" ? "page" : undefined} onClick={() => selectTab("inbox")}><Inbox size={16} />Inbox</button>
        <button aria-current={tab === "analytics" ? "page" : undefined} onClick={() => selectTab("analytics")}><BarChart3 size={16} />Analytics</button>
      </nav>
      {error && <div className="shell-error" role="alert">{error}</div>}
      {tab === "operations" && roundId && <OperationsView onHealthChange={setFleetHealth} roundId={roundId} />}
      {tab === "operations" && !roundId && !error && <div className="workspace-state">No acquisition rounds recorded.</div>}
      {tab === "inbox" && <InboxView />}
      {tab === "analytics" && roundId && <AnalyticsView roundId={roundId} />}
      {tab === "analytics" && !roundId && !error && <div className="workspace-state">No acquisition rounds recorded.</div>}
    </div>
  );
}
