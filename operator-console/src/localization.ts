const labels: Record<string, string> = {
  pending: "待开始",
  running: "运行中",
  paused: "已暂停",
  stopped: "已停止",
  completed: "已完成",
  healthy: "健康",
  degraded: "异常",
  offline: "离线",
  idle: "空闲",
  uncertain: "待确认",
  failed: "失败",
  success: "成功",
  planned: "已规划",
  sent: "已发送",
  activity: "动态",
  messages: "消息",
  inbound: "收到",
  outbound: "发出",
  new: "新线索",
  engaged: "已互动",
  qualified: "合格线索",
  invited: "已邀请私域",
  contact_captured: "已留联系方式",
  human_required: "需人工处理",
  closed: "已关闭",
  confirmed: "已确认",
  refunded: "已退款",
  cancelled: "已取消",
  like: "点赞",
  favorite: "收藏",
  repost: "转发",
  trace_only: "仅留痕",
  deferred: "已延后",
  action_reconciling: "动作核对中",
  assigned: "已分配",
  visiting: "访问中",
  profile_confirmed: "主页已确认",
  video_opened: "视频已打开",
  action_selected: "动作已选择",
};

export const localizeValue = (value: string | null | undefined) =>
  value ? labels[value] ?? `其他（${value}）` : "暂无";

export const localizeError = (reason: unknown, fallback = "操作失败") => {
  const detail = reason instanceof Error ? reason.message : fallback;
  return `操作失败：${detail}`;
};

export const actionLabel = (action: string) => ({ start: "启动", pause: "暂停", stop: "停止", retry: "重试" })[action] ?? action;
export const scopeLabel = (scope: string) => ({ round: "轮次", fleet: "设备组", device: "设备" })[scope] ?? scope;
