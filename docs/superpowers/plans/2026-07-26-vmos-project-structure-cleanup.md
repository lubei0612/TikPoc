# VMOS 项目结构整理实施计划

> 触达轮次保持暂停。每个任务独立红绿重构、独立提交和复核。

## Task 1：中性化设备与 Fleet 配置

1. 为纯 VMOS YAML 增加失败测试：不含 `myt`、`myt_slot` 也能加载。
2. 为旧 MYT YAML 增加兼容测试，确保历史配置仍可显式读取。
3. 将设备字段改为 `provider`、`provider_instance_id`；网络源探测字段移出 MYT。
4. 更新 `config/devices.example.yaml` 为 VMOS 示例，新增历史 MYT 示例文件。
5. 跑 `test_fleet.py`、`test_proxy_guard.py` 和完整 Python 回归。

## Task 2：隔离历史 MYT 运行时

1. 添加测试，证明默认生产 CLI 不导入或初始化 MYT SDK。
2. 将 MYT SDK、旧代理转发和 MYT Appium 启动放入显式 legacy 边界。
3. 保留兼容导入一轮，输出结构化退役事件；新文档只指向 VMOS。
4. 更新历史 runbook 顶部状态，不删除历史测量。

## Task 3：建立唯一自主移动生产入口

1. 为注册、拉取、执行、断点、回传和暂停/恢复写端到端服务测试。
2. CLI 增加唯一的 autonomous runtime 部署/状态命令。
3. ADB 安装脚本与业务运行命令分离。
4. 健康接口报告 server commit、policy version、APK protocol version。

## Task 4：收敛获客规则边界

1. 新建版本化 `AcquisitionPolicy` 测试，覆盖资格、动作权重和配额。
2. 数据库只保存 policy version 和不可变计划；APK 不解释资格规则。
3. 把文档中的当前规则链接到 policy 测试，历史规则明确标记历史状态。

## Task 5：拆分数据访问接口

1. 先提取 migration runner，不改变表结构。
2. 提取 mobile session/outbox repository。
3. 提取 round/task/action repository。
4. 提取 browser/lead repository 和只读运营查询。
5. API 仅依赖 service，不直接访问 SQLite。

## Task 6：统一服务器构建与部署

1. 构建 wheel，记录 commit 和依赖锁。
2. systemd 只运行该 wheel，不再复制源码与 site-packages 双份文件。
3. 增加部署后版本、迁移、健康和回滚验证。

## Task 7：最终清理与验收

1. 检查生产路径中的 MYT 字符串、死入口和未使用配置。
2. 完整 Python、Node、Android、前端、Ruff 和 `git diff --check`。
3. 运行暂停/恢复断点验证，但不启动真实触达轮次。
4. 更新 README、AGENTS、VMOS runbook 和 GitHub。

## 2026-07-26 执行结果

- Task 1 完成：新配置使用中性 provider 字段，保留显式 legacy MYT 兼容。
- Task 2 完成：MYT 适配器进入 legacy 边界，核心安装不再依赖 Appium。
- Task 3 完成生产入口收敛：自主 HTTPS APK 是唯一推荐移动运行时；运行元数据公开 commit、policy、协议及 helper 版本。
- Task 4 完成当前规则版本固化：`following > followers` 且 `video_count >= 1`，规则版本为 `following-gt-followers-posts-gte-1-v1`。本轮未改规则。
- Task 5 完成第一批高价值边界：操作命令迁移、幂等写入、失败重放和控制状态 SQL 已移入 `OperatorControlRepository`；异常类型移入独立领域模块。其余巨型数据库按业务变更逐域继续提取，避免为缩短文件做机械搬移。
- Task 6 完成：wheel 单一部署源、systemd 示例、版本核验和回滚流程已落库，并通过隔离虚拟环境安装冒烟。
- Task 7 完成当前整理验收：Python 1024、Chrome 111、控制台 36、Playwright 15、开发者站点 3、两个 Android 构建/Java 测试全部通过；差异检查通过。触达轮次保持暂停，未改变断点。

### 验收说明

全仓库 Ruff 在扩展规则集下仍报告 134 条历史存量告警，涉及旧 Appium、商品发布和既有测试等未触碰模块；本次新增及修改文件 Ruff check/format 均通过。该存量不影响上述运行回归，也不在本轮进行低价值批量格式改写。
