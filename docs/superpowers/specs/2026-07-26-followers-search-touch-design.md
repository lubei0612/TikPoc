# Followers 采集接口与站内搜索组合触达设计

日期：2026-07-26  
状态：待用户书面复核  
工作树：`feat/web-lead-conversion`

## 1. 目标

将 `/Users/chenyuqi/Desktop/followers` 作为独立的用户发现项目接入 TikPoc，
并为 Android 自主执行 APK 增加 TikTok 站内搜索导航。采集器只负责产出候选
用户；TikPoc 负责校验、去重、插队、断点、逐账号覆盖、主页身份确认、组合触达、
结果回传和运营统计。

第一阶段通过一个账号进行整夜搜索触达试验。试验不使用 Deeplink，不要求
`8.64 s/target`，优先验证精确性、访客记录写入和账号持续可用性。

## 2. 不变边界

- 一个 VMOS 实例只对应一个 TikTok 账号和一个自主 APK worker。
- Mac/ADB 只用于安装、升级和诊断；任务拉取、执行、断点和回传均由 APK 通过
  HTTPS 完成。
- 大任务与插队任务共用既有 durable assignment、lease、action plan、quota 和
  coverage 事实来源。
- 插队前保存大任务断点；插队参与账号全部到达终态后恢复原断点。
- 自动回关、自动私信及 Chrome 收件箱工作不属于本设计。
- 现有任务保持暂停，直到新的搜索 canary 获得明确启动指令。

## 3. 系统边界

### 3.1 followers 项目

负责：

- 从公开视频评论、公开粉丝列表或直播公开事件发现用户；
- 合并 `sec_uid`、真实 `user_id` 和规范化 username；
- 将一个完整批次先写入临时文件，再原子重命名为最终 JSONL；
- 调用 TikPoc CLI 提交批次；
- 只根据退出码和机器 JSON 确认提交成功。

不负责：

- 连接或修改 TikPoc SQLite；
- 选择设备、动作或配额；
- 判断任务是否已被其他账号触达；
- 控制 APK 页面状态。

### 3.2 TikPoc 服务端

负责：

- 解析和验证 JSONL；
- 按稳定身份去重；
- 建立普通批次或实时插队批次；
- 将不可变 `navigation_mode` 和 policy version 固化在批次/任务中；
- 向 APK 下发任务并持久化结果；
- 保证搜索失败不产生 confirmed visit；
- 统计搜索漏斗和逐账号覆盖。

### 3.3 Android 自主 APK

负责：

- 根据任务的 `navigation_mode` 选择明确的页面状态机；
- 使用 TikTok 站内搜索进入目标主页；
- 在点击前和进入后分别做精确用户名核验；
- 读取主页作品与指标；
- 执行服务端已经规划的单一互动动作并验证可见结果；
- 本地保存任务和回传 outbox，网络恢复后继续发送。

APK 不自行改变资格规则、动作权重、配额或失败分类。

## 4. 统一 JSONL 接口

UTF-8 JSONL 每行一个对象：

```json
{"username":"buyer.one","user_id":"123456","sec_uid":"MS4w...","profile_url":"https://www.tiktok.com/@buyer.one","source_type":"comments","source_id":"video-746","source_video_id":"746","source_live_id":"","collected_at":"2026-07-26T20:00:00+08:00"}
```

字段：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `username` | 是 | 去掉 `@` 后仅允许字母、数字、点和下划线，最长 24 字符 |
| `user_id` | 否 | TikTok 真实数字 ID；本地临时 ID 不作为稳定身份 |
| `sec_uid` | 否 | TikTok sec UID，身份优先级最高 |
| `profile_url` | 否 | 必须与 username 一致的 TikTok HTTPS 主页地址 |
| `source_type` | 是 | `followers`、`comments` 或 `live` |
| `source_id` | 是 | 稳定来源标识；用于审计和提交幂等 |
| `source_video_id` | 否 | 评论来源视频 ID |
| `source_live_id` | 否 | 直播来源标识 |
| `collected_at` | 否 | ISO-8601 采集时间 |

稳定身份优先级为 `sec_uid`、真实 `user_id`、规范化 username。字段冲突拒绝整批
导入；无效行只在不存在稳定身份冲突时计入 `skipped_invalid`。

## 5. CLI 交接

followers 文件夹中的 AI 使用 TikPoc 已安装 CLI，不导入 TikPoc Python 内部模块。

普通精准池：

```bash
tikpoc targets-import \
  --file /absolute/path/to/targets.jsonl \
  --source-id SOURCE_ID \
  --navigation-mode search \
  --json
```

实时插队：

```bash
tikpoc priority-import \
  --db /absolute/path/to/tikpoc.db \
  --devices /absolute/path/to/devices.yaml \
  --file /absolute/path/to/priority.jsonl \
  --source-id SOURCE_ID \
  --navigation-mode search \
  --json
```

命令成功时 stdout 只输出一个 JSON 对象，至少包含 `batch_id`、`batch_class`、
`navigation_mode`、`unique_targets`、`skipped_duplicates`、`skipped_invalid` 和
`device_count`。相同来源 ID、文件摘要、父轮次和导航模式安全重放并返回同一批次。

## 6. 不可变导航模式

第一版枚举：

- `search`：TikTok 站内搜索；本设计新增的 canary/生产候选路径；
- `deeplink`：保留用于历史任务回放和显式诊断，不作为搜索批次的失败兜底。

`navigation_mode` 固化在批次和移动任务 envelope 中。worker 重启、任务重试、
uncertain reconciliation 和插队恢复不得改变它。服务端拒绝未知模式，旧数据库
迁移后的历史任务显式标记为 `deeplink`，避免无声改变历史语义。

## 7. 搜索状态机

每个目标执行：

1. 回到已知 TikTok 稳定入口；
2. 打开站内搜索；
3. 确认英文直接输入状态并清空旧查询；
4. 输入完整规范化 username；
5. 提交搜索并选择“用户”结果面；
6. 只接受一个文本完全相等的 username 结果；
7. 点击该精确结果；
8. 等待主页稳定，并再次确认可见 `@username` 完全相等；
9. 读取 access state、following、followers、video count 和可见作品句柄；
10. 向服务端回传 profile observation；
11. 执行服务端返回的不可变 action plan；
12. 验证可见动作结果并回传终态。

用户名比较统一执行 Unicode NFKC、去除方向控制字符、去掉一个前导 `@`、转小写；
不做模糊匹配、前缀匹配、昵称匹配或头像匹配。

## 8. 精准失败规则

以下结果统一结束为显式未触达，不产生 confirmed visit，不打开任何相似用户：

- `search_no_exact_match`：无精确用户名；
- `search_ambiguous_exact_match`：存在多个无法唯一选择的精确结果；
- `search_surface_timeout`：搜索结果面未在时限内稳定；
- `profile_identity_mismatch`：点击后主页可见用户名不相等；
- `profile_unavailable`：账号封禁、注销、不存在或页面明确不可用。

搜索失败不回退 Deeplink。第一版对同一目标只执行一次完整搜索核验；基础设施断线
按既有 outbox/session 恢复，不能重复点击一个已经确认过的其他账号。

## 9. 组合触达政策

本设计的搜索试验采用“主页访问 + 有作品时一次互动”：

- 精确主页身份确认是 visit confirmation 的必要条件；
- `video_count >= 1` 时，服务端规划 exactly one interaction；
- 动作限定为 like、favorite 或 repost，并继续服从每账号滚动配额；
- 动作计划一旦创建即不可变，重试复用原计划；
- 动作只有在可见状态核验后才完成；
- 无作品时只保留 confirmed profile visit；
- 访问其他用户名、搜索相似结果或为了凑数量改变目标均被禁止。

该政策使用新的版本号，不覆盖历史任务的 policy version。上线前同步更新 AGENTS、
业务逻辑文档、规则测试和 runtime metadata。

## 10. 观测漏斗

每账号、每批次至少记录：

- `search_started`；
- `search_exact_found`；
- `search_unresolved` 及分类错误码；
- `profile_identity_verified`；
- `profile_visit_confirmed`；
- `profile_has_posts`；
- `interaction_planned`；
- `interaction_verified`；
- `interaction_failed` / `interaction_uncertain`；
- 单阶段及端到端耗时。

访客记录是否在目标端真实出现属于抽样外部核验，必须与 APK 的
`profile_visit_confirmed` 分开报告，不能用页面打开成功替代。

## 11. 单账号整夜试验

分阶段执行：

1. 10 个受控目标校准：核验搜索精确性、互动状态和目标端访客记录；
2. 50 个目标门禁：每 10 个抽样核验访客记录；
3. 100 个目标门禁：检查 unresolved、identity mismatch、互动成功率和延迟；
4. 获得明确指令后运行单账号整夜 canary。

整夜 canary 不以日均一万为通过条件。验收重点：

- 错误账号点击数必须为 0；
- 目标端访客写入率单独报告；
- 搜索精确命中率、互动核验率和阶段耗时有完整记录；
- 账号出现访客记录连续下降、页面限制或互动异常时自动暂停并保留断点；
- canary 结束后由用户检查账号状态，再决定多账号和容量门禁。

## 12. 测试要求

### Python

- JSONL 新旧来源字段兼容、冲突和原子文件检查；
- `navigation_mode` migration、幂等、批次/任务持久化；
- 插队断点、参与设备快照、完成 barrier 和恢复；
- 搜索失败不计 coverage、不触发 Deeplink fallback；
- policy version、动作计划和配额不变性。

### Android

- 搜索入口与输入框语义；
- 英文用户名直接输入，包括点和下划线；
- 用户结果面精确匹配、无结果和歧义；
- 点击后二次身份核验；
- 进程重启后 task/outbox 恢复；
- exactly-one action 和可见结果核验。

### 实机

- 记录 TikTok 版本、APK commit、policy version、实例、账号和出口；
- 保存 10/50/100/整夜四阶段测量；
- 报告 measured throughput，不用短时结果推算正式日产能；
- 任何测试不得恢复当前暂停的大任务。

## 13. 文档交付

实现阶段在 followers 项目根目录新增 `TIKPOC_HANDOFF.md`，内容只包含 JSONL、
原子写入、CLI 命令、退出码、幂等重放和示例；不包含数据库路径之外的凭据、
账号信息或服务器密钥。TikPoc README 与业务逻辑文档链接该契约，不复制两份会
漂移的规则说明。
