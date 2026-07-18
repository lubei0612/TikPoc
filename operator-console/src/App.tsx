import { Activity, BarChart3, Inbox, Network, RadioTower } from "lucide-react";
import { useEffect, useState } from "react";

import { getRounds, type RoundListItem } from "./api";
import { OperationsView, type FleetHealthSummary } from "./views/OperationsView";
import { AnalyticsView } from "./views/AnalyticsView";
import { InboxView } from "./views/InboxView";
import { localizeError } from "./localization";

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
      .catch((reason: unknown) => setError(localizeError(reason, "轮次列表加载失败")));
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
    ? "设备组健康：未连接"
    : fleetHealth
      ? `设备组健康：${fleetHealth.totalDevices} 台设备中 ${fleetHealth.healthyDevices} 台健康；${fleetHealth.totalBrowserObservers} 个浏览器观察器中 ${fleetHealth.healthyBrowserObservers} 个健康`
      : "设备组健康：等待数据";
  const healthState = fleetHealth && fleetHealth.totalDevices > 0 && fleetHealth.totalBrowserObservers > 0
    && fleetHealth.healthyDevices === fleetHealth.totalDevices
    && fleetHealth.healthyBrowserObservers === fleetHealth.totalBrowserObservers ? "healthy" : "degraded";
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup"><span><Network size={19} /></span><strong>TikPoc 运营台</strong></div>
        <div className="topbar-context">
          <label htmlFor="round-select">轮次</label>
          <select id="round-select" onChange={(event) => { setFleetHealth(null); setRoundId(event.target.value); }} value={roundId}>
            {rounds.map((round) => <option key={round.round_id} value={round.round_id}>{round.round_id} · {round.device_count} 个账号</option>)}
          </select>
          <span aria-label={healthLabel} className={`global-health state-${healthState}`}><RadioTower size={14} />{notConnected ? "未连接" : fleetHealth ? `设备 ${fleetHealth.healthyDevices}/${fleetHealth.totalDevices} · 浏览器 ${fleetHealth.healthyBrowserObservers}/${fleetHealth.totalBrowserObservers}` : "等待健康数据"}</span>
        </div>
      </header>
      <nav className="primary-nav" aria-label="管理后台视图">
        <button aria-current={tab === "operations" ? "page" : undefined} onClick={() => selectTab("operations")}><Activity size={16} />运营监控</button>
        <button aria-current={tab === "inbox" ? "page" : undefined} onClick={() => selectTab("inbox")}><Inbox size={16} />线索收件箱</button>
        <button aria-current={tab === "analytics" ? "page" : undefined} onClick={() => selectTab("analytics")}><BarChart3 size={16} />经营分析</button>
      </nav>
      {error && <div className="shell-error" role="alert">{error}</div>}
      {tab === "operations" && roundId && <OperationsView onHealthChange={setFleetHealth} roundId={roundId} />}
      {tab === "operations" && !roundId && !error && <div className="workspace-state">暂无获客轮次。</div>}
      {tab === "inbox" && <InboxView />}
      {tab === "analytics" && roundId && <AnalyticsView roundId={roundId} />}
      {tab === "analytics" && !roundId && !error && <div className="workspace-state">暂无获客轮次。</div>}
    </div>
  );
}
