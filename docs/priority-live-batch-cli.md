# 直播兴趣用户插队 CLI

本文档定义采集 AI 与 TikPoc 移动触达系统之间的机器接口。插队只改变调度顺序，不改变主页身份确认、资格判断、互动选择、额度、可见结果核验、重试或多账号覆盖规则。

## 前置条件

- 纯触达模式可以继续使用一个活动普通轮次；热评/养号混合模式使用一次性
  `live-host-init` 创建的零目标宿主轮次。
- 宿主的设备 ID 必须与 APK 注册设备一致；系统会从中快照提交时控制状态为
  `running` 的设备，暂停或停止设备不参加该批。
- 采集程序先在临时文件中写完整内容并关闭文件，再原子重命名为最终路径，最后调用 CLI。
- 不要边追加文件边执行导入。TikPoc 会拒绝读取期间发生变化的文件。

## JSONL 输入

文件使用 UTF-8 JSONL，每行一个对象：

```json
{"username":"buyer.one","user_id":"123456","sec_uid":"MS4w...","profile_url":"https://www.tiktok.com/@buyer.one","source_video_id":"746...","source_type":"live_followers","source_id":"live-20260722-01","collected_at":"2026-07-22T20:00:00+08:00"}
```

字段规则：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `username` | 是 | TikTok 用户名，不含 `@`，仅允许字母、数字、点和下划线，最长 24 字符 |
| `user_id` | 否 | 平台真实用户 ID；`dom-*` 采集器本地 ID 不作为稳定身份 |
| `sec_uid` | 否 | 平台 sec UID，优先级最高 |
| `profile_url` | 否 | 必须是与 username 一致的 TikTok HTTPS 主页地址 |
| `source_video_id` | 否 | 来源视频 ID |
| `source_type` | 否 | 来源类型，例如 `live_followers` |
| `source_id` | 否 | 来源标识；如填写，必须与命令参数一致 |
| `collected_at` | 否 | 采集时间字符串，仅保留来源语义 |

所有字段只能是字符串或 `null`。同一提交按 `sec_uid`、真实 `user_id`、规范化 username 合并；稳定 ID 冲突会拒绝整次导入。

系统也接受带有 `follower_handle`、`follower_uid`、`follower_sec_uid` 表头的 `.xlsx`/`.xlsm` 文件。

## 导入命令

### Hybrid 热评/养号运行

```bash
uv run tikpoc live-host-init \
  --db /path/to/tikpoc.db \
  --devices /path/to/devices.yaml \
  --host-id main

uv run tikpoc live-batch-submit \
  --db /path/to/tikpoc.db \
  --host-round HOST_ROUND_ID \
  --file /path/to/live-users.jsonl \
  --source-live live-20260730-01 \
  --navigation-mode deeplink

uv run tikpoc live-batch-status --db /path/to/tikpoc.db
```

APK provision 的 round 值为 `hybrid:HOST_ROUND_ID`。每次 HTTPS 拉取由服务器
统一仲裁：未完成的 `live_interrupt` 优先；参与设备到达 barrier 时保持等待；
直播队列清空后领取已经到期的 `brand_comment`；两者都为空时执行有界只读 Home
浏览。APK 运行期间不依赖 ADB。

浏览器或采集服务也可以向 `POST /api/live-batches` 发送相同规范化目标；请求使用
服务端配置的 `TIKPOC_LIVE_BATCH_TOKEN` Bearer。服务只接收目标字段，不读取浏览器
Cookie、登录存储或凭据。

### 普通触达轮次

```bash
uv run tikpoc priority-import \
  --db /path/to/tikpoc.db \
  --devices /path/to/devices.yaml \
  --file /path/to/live-users.jsonl \
  --source-id live-20260722-01 \
  --navigation-mode search \
  --json
```

成功时 stdout 只输出一行 JSON：

```json
{"batch_class":"live_interrupt","batch_id":"priority-0123456789abcdef","device_count":4,"parent_round_id":"round-0123456789abcdef0123","skipped_duplicates":3,"skipped_invalid":1,"unique_targets":246}
```

相同普通轮次、文件 SHA-256 和 `source-id` 会得到相同 batch ID。批次完成且进程重启后再次执行同一命令，也会返回原 batch ID。若已有新的活动普通轮次，同一来源会为新轮次创建独立批次。

`navigation_mode=search` 是批次不可变字段。APK 只点击唯一、规范化后完全相等的可见 username；没有完全匹配、出现多个完全匹配或最终主页身份不一致时记录明确未触达结果，不点击相似账号，也不回退 Deeplink。

参与设备快照是不可变的。相同命令重放时，即使设备暂停/运行状态已经变化，也返回第一次导入的 batch ID 和参与设备数量。

## 状态命令

```bash
uv run tikpoc priority-status --db /path/to/tikpoc.db
```

stdout 输出一行 JSON，包含：

- `batches`：按 `queue_sequence` 升序排列的全部批次；
- `batch_class`：采集器提交为 `live_interrupt`，预载策略 B 波次为 `background`；
- `state`：`queued`、`running`、`barrier` 或 `completed`；
- `devices`：每台设备的 `total`、`pending`、`deferred`、`completed`、`skipped`；
- `ordinary_checkpoint`：原任务的 `total`、`pending`、`deferred`、`completed`、`skipped` 和 `visits_confirmed`。

`barrier` 表示至少一台设备已完成当前批次，但仍在等待其他参与设备。系统不会让先完成的设备提前返回原任务。

## 调度与恢复

1. 已持有租约的当前用户先完成完整原子流程。
2. `live_interrupt` 优先于所有 `background` 波次；多个实时插队仍按提交顺序 FIFO。
3. 只有导入瞬间为 `running` 的设备进入不可变参与快照。
4. 所有快照设备到达终态后才进入下一实时插队批次。
5. 实时插队队列清空后，从原 background assignment、attempt 和 order key 断点继续。

同一设备账号若已在当前普通轮次确认访问该身份，插队 assignment 直接记录为满足；插队确认访问也会满足同设备账号尚未开始的普通 assignment 和后续重复插队 assignment。`deferred`/uncertain、`skipped` 和缺少 confirmed visit 的结果不会传播。

## 退出码与错误

- `0`：命令成功，stdout 为单行 JSON。
- `2`：argparse 参数错误。
- 其他输入或状态错误：CLI 以非零状态退出并在 stderr 给出简短原因，例如文件变化、工作簿损坏、设备集合不一致、没有唯一活动普通轮次或稳定身份冲突。

调用方只在退出码为 `0` 且 JSON 可解析时确认提交成功。超时后可以安全重放同一命令，不要自行生成新的 batch ID。
