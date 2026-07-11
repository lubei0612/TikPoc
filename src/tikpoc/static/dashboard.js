const ids = ["pending", "completed", "skipped", "retry", "failed"];
const stateLabels = { running: "运行中", paused: "已暂停", stopped: "已停止" };
const resultLabels = { completed: "完成", skipped: "跳过", retry_wait: "重试", failed: "失败", running: "处理中" };
let lastSuccess = 0;

function count(data, name) { return data.counts[name] || 0; }

async function refresh() {
  try {
    const [statusResponse, recentResponse] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/recent?limit=10", { cache: "no-store" })
    ]);
    if (!statusResponse.ok || !recentResponse.ok) throw new Error("request failed");
    const status = await statusResponse.json();
    const recent = await recentResponse.json();
    render(status, recent);
    lastSuccess = Date.now();
  } catch (error) {
    document.getElementById("staleIndicator").textContent = "连接中断";
  }
}

function render(status, recent) {
  const processed = status.processed || 0;
  const total = status.total || 0;
  const percent = total ? Math.round(processed / total * 100) : 0;
  document.getElementById("processedCount").textContent = `${processed} / ${total}`;
  document.getElementById("progressPercent").textContent = `${percent}%`;
  document.getElementById("progressBar").style.width = `${percent}%`;
  ids.forEach(name => {
    const key = name === "retry" ? "retry_wait" : name;
    document.getElementById(`${name}Count`).textContent = count(status, key);
  });
  const state = status.control || "running";
  const stateNode = document.getElementById("workerState");
  stateNode.textContent = stateLabels[state] || state;
  stateNode.className = `state ${state}`;
  document.getElementById("pauseButton").disabled = state !== "running";
  document.getElementById("resumeButton").disabled = state === "running";
  document.getElementById("stopButton").disabled = state === "stopped";

  const current = status.current;
  document.getElementById("currentUser").textContent = current?.username || "暂无运行任务";
  document.getElementById("currentPhase").textContent = current?.checkpoint || status.latest_event?.event_type || "等待 worker";
  document.getElementById("lastUpdated").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  document.getElementById("staleIndicator").textContent = "";

  const list = document.getElementById("recentTasks");
  list.replaceChildren();
  if (!recent.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "暂无记录";
    list.append(item);
  } else {
    recent.forEach(task => {
      const item = document.createElement("li");
      const user = document.createElement("span");
      const result = document.createElement("span");
      user.textContent = `@${task.username}`;
      result.className = "result";
      result.textContent = resultLabels[task.state] || task.state;
      item.append(user, result);
      list.append(item);
    });
  }
}

async function control(action) {
  await fetch(`/api/control/${action}`, { method: "POST" });
  await refresh();
}

document.querySelectorAll("button[data-action]").forEach(button => {
  button.addEventListener("click", () => control(button.dataset.action));
});

setInterval(() => {
  refresh();
  if (lastSuccess && Date.now() - lastSuccess > 6000) {
    document.getElementById("staleIndicator").textContent = "数据已过期";
  }
}, 2000);
refresh();
