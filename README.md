# TikPoc

TikPoc 是一套面向多账号运营的本地任务编排系统。它把移动端触达、浏览器线索承接、持久化断点、插队批次和经营漏斗拆成相互独立的执行面，并通过可见状态核验保证任务可以暂停、恢复和审计。

> 当前仓库仍处于实机校准和容量验证阶段。短时测试结果不代表长期生产吞吐；实测数据和推算数据应分别记录。

## 核心能力

- **多设备触达**：设备与账号一一映射，每个启用账号独立完成同一目标池。
- **可恢复执行**：SQLite 持久化 assignment、租约、动作计划、额度和断点。
- **可见状态核验**：主页身份、视频打开和 confirmed 互动均以界面可见证据为准；唯一一次核验仍不明确时保留 uncertain 证据并终结自动重试。
- **策略 A / B**：支持全量独立乱序，以及按短批次集中触达并设置全设备屏障。
- **实时插队**：直播兴趣用户进入独立 `live_interrupt` 通道，抢占预载策略 B 波次；按提交时运行设备完成后从原断点继续。
- **浏览器承接**：独立 Chrome Profile 处理关注、消息、AI 回复计划和人工接管。
- **运营控制台**：查看设备健康、任务进度、覆盖、异常和线索漏斗。

## 架构

```text
CSV / live collector
        │
        ▼
 Target Pool ──► Exposure Rounds ──► Priority Queue
        │                 │                  │
        └─────────────────┴──────────────────┘
                          ▼
          VMOS Fleet / Autonomous APK
              device ↔ account (1:1)
                          │
                          ▼
                SQLite durable state
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
      Operator Console          Browser Lead Plane
                                Chrome Extension
```

移动触达面和浏览器线索面独立运行：浏览器消息处理不会抢占移动任务，移动设备也不会导航浏览器会话。

## 业务流程

1. 导入并按稳定身份去重目标。
2. 为每个启用设备/账号创建独立 assignment 和可复现乱序。
3. 打开目标主页并从可见界面确认身份。
4. 根据当前批准的资格规则判断是否进入视频互动路径。
5. 对到期动作执行点赞、收藏、转发或纯留痕，并核验最终可见状态。
6. 持久化访问覆盖、动作终态、异常、重试和漏斗事件。
7. 浏览器端承接后续关注与消息，并在需要时转入人工处理。

完整口径见 [TikPoc 获客业务逻辑](docs/tikpoc-business-logic.md)。

## 策略 A 与策略 B

### 策略 A：全量独立乱序

所有设备持续处理同一个完整目标池，每台设备使用不同但可恢复的确定性顺序。快设备无需等待慢设备，适合追求持续吞吐。

### 策略 B：集中波次

大任务被拆成较小的管理批次和触达波次。所有参与设备先完成当前波次，再进入下一波；同一波次内各设备仍使用不同顺序。它只改变调度，不改变资格、互动、额度、核验、幂等或覆盖规则。

策略设计与对照指标见 [触达策略 A/B 决策记录](docs/acquisition-strategy-a-b.md)。

## 环境要求

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js（Chrome 扩展测试）
- Android SDK Platform Tools / ADB
- VMOS 账号与 OpenAPI 配置

生产移动执行不要求 Mac、ADB 或 Appium 常驻。APK 通过 HTTPS 主动拉取任务；
ADB 只用于首次安装、升级和故障诊断。MYT/Appium 配置只保留为历史兼容。

## 安装

```bash
git clone https://github.com/lubei0612/TikPoc.git
cd TikPoc

uv sync --extra test
npm install
```

只有运行历史 Appium Fleet 时才安装兼容依赖：

```bash
uv sync --extra legacy-appium
```

检查 CLI：

```bash
uv run tikpoc --help
```

## 配置

复制示例文件，再把本机值写入被 Git 忽略的本地配置：

```bash
cp config/devices.example.yaml config/devices.yaml
cp config/web-accounts.example.yaml config/web-accounts.yaml
```

设备配置使用一台设备对应一个账号：

```yaml
devices:
  - device_id: vmos-01
    account_id: account-01
    provider: vmos
    provider_instance_id: ACP-SYNTHETIC-01
    backend: device-side
    adb_endpoint: 127.0.0.1:PORT
    helper_host_port: 47101
    helper_device_port: 47101
```

实际字段以 [`config/devices.example.yaml`](config/devices.example.yaml) 为准。账号凭据、代理订阅、API Key、Cookie、数据库和真实目标文件都应保存在本地忽略文件中。

## 基本用法

### 1. 校验并导入目标池

```bash
uv run tikpoc validate path/to/targets.csv
uv run tikpoc pool-import --db runtime/tikpoc.db --csv path/to/targets.csv
```

`pool-import` 会输出目标池标识。使用它创建执行轮次：

```bash
uv run tikpoc round-create \
  --db runtime/tikpoc.db \
  --pool POOL_ID \
  --devices config/devices.yaml \
  --starts-at 2026-07-23T20:00:00+08:00
```

### 2. 启动服务端与自主设备任务

```bash
export TIKPOC_MOBILE_BOOTSTRAP_TOKEN='LOCAL_SECRET'
uv run tikpoc dashboard \
  --db runtime/tikpoc.db \
  --host 127.0.0.1 \
  --port 8765
```

安装并一次性配置 APK 后，每台 VMOS 设备独立注册、拉取、保存断点和回传结果。
生产部署步骤见 [VMOS 自主执行手册](docs/runbooks/vmos-device-side-touch.md)。
`fleet-run` 是旧 Appium 兼容入口，不是新的生产启动方式。

### 3. 导入直播兴趣用户插队批次

热评/养号生产模式先初始化一次空的直播宿主轮次：

```bash
uv run tikpoc live-host-init \
  --db runtime/tikpoc.db \
  --devices config/devices.yaml \
  --host-id main
```

将设备 APK 的 round 配置设为输出的 `hybrid:HOST_ROUND_ID`。采集器完整写入
临时文件并原子重命名后，提交直播用户：

```bash
uv run tikpoc live-batch-submit \
  --db runtime/tikpoc.db \
  --host-round HOST_ROUND_ID \
  --file runtime/live-batch.jsonl \
  --source-live LIVE_SOURCE_ID \
  --navigation-mode deeplink

uv run tikpoc live-batch-status --db runtime/tikpoc.db
```

Hybrid 调度顺序为“直播插队留痕 → 到期热评 → 只读养号浏览”。提交会快照
当时为 `running` 的设备；暂停设备不参加，相同来源与内容重放仍返回第一次的
参与快照。直播批次是用户主页触达，同一目标需要所有快照账号分别触达；热评
仍严格保持一个视频只对应一个品牌账号。

详细机器合同见 [直播插队 CLI](docs/priority-live-batch-cli.md)。
搜索触达验收见 [搜索触达 Canary](docs/runbooks/search-touch-canary.md)。

### 4. 启动本地运营控制台

```bash
uv run tikpoc dashboard \
  --db runtime/tikpoc.db \
  --host 127.0.0.1 \
  --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。部署和操作说明见 [Operator Console Runbook](docs/operator-console-runbook.md)。

## 容量与验收

正式容量基准按 7 个账号计算：每天 10,000 个唯一目标，对应 70,000 次已确认账号访问。性能提升不得跳过身份确认、动作核验、幂等、额度或覆盖记录。

容量推广门槛：

- 完整目标混合下单设备总体平均不高于 `8.64 s/target`；
- p50、p90 和各动作耗时作为诊断指标单独报告；
- 身份、路由、动作核验和 N/N 覆盖检查全部通过；
- 使用长时间实机数据报告吞吐，不用短时合成测试代替。

## 测试

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
```

涉及真实 Chrome 或设备交互的变更，还需要记录可见状态校准证据。

## 文档导航

- [业务逻辑](docs/tikpoc-business-logic.md)
- [VMOS 自主执行手册](docs/runbooks/vmos-device-side-touch.md)
- [历史移动 Fleet 手册](docs/mobile-fleet-runbook.md)
- [策略 A/B](docs/acquisition-strategy-a-b.md)
- [直播插队 CLI](docs/priority-live-batch-cli.md)
- [运营控制台](docs/operator-console-runbook.md)
- [Dashboard](docs/dashboard-runbook.md)
- [浏览器线索承接](docs/web-engagement-runbook.md)
- [代理健康守卫](docs/proxy-guard-runbook.md)
- [互动动作校准](docs/interaction-runbook.md)

## 安全与数据

- 不提交密码、Token、Cookie、代理订阅、个人联系方式或真实目标数据。
- 不提交 SQLite、日志、截图、Chrome Profile、CSV 导出和 Android 调试签名材料。
- 公开示例只使用合成数据与占位符。
- 部署到公网前应单独配置身份认证、网络访问控制和密钥管理。

## 当前状态

移动触达、断点恢复、策略 A/B、优先批次、浏览器事件桥和本地控制台均已有实现与自动化测试。生产使用前仍应完成目标环境下的设备校准、长时间容量验证和浏览器可见状态验收。
