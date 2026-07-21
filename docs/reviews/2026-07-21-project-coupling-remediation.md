# 项目耦合审查整改记录

日期：2026-07-21
基线审查：`docs/reviews/2026-07-21-project-coupling-business-risk-review.md`

## 本次已处理

### 1. 业务进度可观测性

- 操作台设备行现在同时显示原始进程健康状态和业务有效状态。
- 当前 assignment 在同一阶段停留达到 120 秒时，显示 `degraded / phase_stalled`。
- 仍有未完成 assignment 且 300 秒没有阶段进展时，显示
  `degraded / no_recent_progress`。
- 返回 `last_progress_at_ms`、`current_phase_started_at_ms` 和
  `progress_age_ms`，可以区分进程活着与业务正在前进。

这次逻辑只读阶段历史，不修改轮次、覆盖、配额或动作规则，不会改变任务分配。

### 2. 失败诊断证据

- Appium 诊断保存到数据库同级的 `screenshots/` 目录。
- 记录 activity、窗口尺寸、页面源哈希/长度，以及点赞、收藏、分享、转发控件的可见状态和 bounds。
- 页面源不再直接截取前 2,000 个字符，避免误把个人页面文本写入诊断摘要。
- 截图路径仍经过服务层的目录、类型和大小校验。

### 3. 租约围栏

- `claim_next_assignment()` 在同一 SQLite 事务中回收当前设备已经过期的 assignment lease，再选择下一个任务。
- 子 worker 遇到 `DeviceWorkerLeaseLost` 时关闭 driver 并干净退出，不把预期的围栏丢失升级成异常重启噪声。
- 原有 owner/fence 校验保留，迟到的终端写入仍会被拒绝。

## 暂未处理

以下耦合继续放到当前 CSV 完成后的独立窗口：

- 移动触达适配器与历史 Inbox 方法拆分；
- `db.py`、`acquisition_db.py` 和 service 的 SQLite 边界收敛；
- Supabase 执行真相迁移；
- 主检出目录业务规则文档与活跃分支同步。

这些项目会改变边界或迁移状态，当前运行轮次不做大范围重构。

## 验证

- Python 全量：`792 passed`。
- 受影响测试：`148 passed`。
- 受影响文件 Ruff check/format 与 `git diff --check` 通过。
- 真实 SQLite 操作快照查询连续三次耗时约 1.5–2.5 秒，六设备均能返回健康和进度字段。
