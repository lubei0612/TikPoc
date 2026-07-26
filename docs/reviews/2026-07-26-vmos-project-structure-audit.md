# TikPoc VMOS 项目结构审计

日期：2026-07-26  
分支：`feat/web-lead-conversion`

## 结论

核心业务模型（目标池、轮次、设备覆盖、身份核验、不可变动作计划、配额、
幂等结果和断点）可以保留。当前最需要整理的不是重写业务，而是把已经退役的
MYT 运行时从生产配置和通用模块中隔离，并收敛数据库、API、移动适配器和部署
边界。

当前触达轮次处于暂停状态。本次整理不得自动恢复轮次，也不得修改用户持有的
`src/tikpoc/runner.py`。

## 运行路径分类

### 当前生产路径

- VMOS 实例与每实例独立账号；
- Android `android-touch-executor` 通过 HTTPS 主动拉取任务；
- ADB 只用于安装、升级和诊断；
- `AcquisitionRepository`、移动 API、SQLite 负责轮次、租约、计划和结果；
- VMOS OpenAPI/在线 ADB 负责实例控制面。

### 需要隔离的历史路径

- `src/tikpoc/myt.py`；
- `FleetConfig.myt_host`、`myt_sdk_port`、`FleetDevice.myt_slot`；
- `ProxyGuard` 中依赖 MYT 宿主机地址的 Mac 代理转发；
- 旧 MYT Appium/Inbox 运行手册和部署计划；
- `runner.py` 中的 MYT 分辨率偏移兼容代码（用户持有，先记录、不编辑）。

历史设计和实测文档保留为证据，不删除或改写成当前运行说明。

## 高耦合发现

### P0：生产 Fleet 配置仍以 MYT 为根

`fleet.py` 的通用设备模型要求每台设备具有 `myt_slot`，配置根必须包含
`myt.host`，代理守护也通过 `myt_host` 推导本机地址。VMOS 设备即使完全不使用
MYT，仍要伪造这些字段。这是当前最直接的错误抽象。

整改方向：引入中性 `provider`、`provider_instance_id` 和可选网络探测配置；旧
MYT YAML 只通过显式兼容解析器读取，并发出退役提示，不能成为新配置模板。

### P0：自主 APK 与旧 Appium Fleet 并存但入口不清晰

当前推荐路径是 APK 自主拉取，旧 `fleet.py`/`device.py` 仍包含 Appium 触达、
Inbox、代理恢复等能力。CLI 和文档没有把“生产入口”和“历史入口”完全分开，
容易在故障时误启动旧路径。

整改方向：为 autonomous mobile runtime 建立唯一生产命令和配置；旧 Appium
入口移入 `legacy` 命名空间或要求显式 `--legacy-myt` 开关。

### P0：业务规则不是单一可执行来源

规则同时出现在 `rules.py`、数据库移动结果处理、AGENTS、多个设计/计划和历史
复盘中。规则变更时容易出现文档、Appium worker 和自主 APK 不一致。

整改方向：建立版本化 `AcquisitionPolicy`，服务端负责下发规则版本和动作计划；
APK 只执行不可变任务，不自行解释获客规则。

### P1：数据层和迁移边界过大

- `acquisition_db.py` 约 5,400 行；
- `db.py` 约 3,800 行；
- `api.py` 约 1,400 行；
- `acquisition_service.py` 仍包含直接 SQL。

整改方向：先按 schema migration、round/task repository、mobile session/outbox、
browser/lead repository、read model 分接口；保持同一个 SQLite 事务事实来源，
不在本轮整理中切换 Supabase。

### P1：部署存在源码和 site-packages 双副本

服务器曾需要同时复制 `/opt/tikpoc/src/tikpoc` 和虚拟环境
`site-packages/tikpoc` 才能生效。这会造成“源码已更新、运行包仍旧”的版本漂移。

整改方向：服务器只从一个已安装 wheel/commit 启动；健康接口返回 commit、APK
协议版本和 policy version。

### P1：移动执行器观测不足

APK 过去会吞掉执行异常，心跳只显示 `idle`，无法区分正在执行、等待结果、网络
降级或队列停滞。已修复两类动作结果卡死，但仍需结构化阶段、错误码和最近进度
心跳。

### P2：获客与商品发布共享设备层概念

`mobile_catalog_publisher.py`、触达 worker、旧 Inbox 和代理恢复都依赖同一类设备
配置与会话概念。应共享“设备身份/控制通道”，但不能共享页面状态机、任务租约
或失败恢复策略。

## 保留与删除原则

- 保留历史 MYT 文档、实测数据库说明和回滚证据；
- 新生产配置、README 和运行手册不得要求 MYT 字段；
- 不删除旧代码直到对应生产入口、测试和回滚说明完成迁移；
- 不做仅为缩短文件的机械拆分；
- 每次拆分先加边界测试，再移动实现；
- 整理期间保持触达轮次暂停。

## 验收门槛

1. 新 VMOS 配置中不存在 `myt` 或 `myt_slot`；
2. 生产 CLI 无需 Mac、MYT SDK、Appium 或本地 ADB 常驻；
3. MYT 只能通过显式历史入口运行；
4. 服务器只有一个可验证的运行包来源；
5. 规则版本、运行 commit、APK 版本可从健康接口读取；
6. Python、Android、Node、Ruff、格式和差异检查全部通过；
7. 整理不改变已保存轮次断点，也不自动恢复任务。
