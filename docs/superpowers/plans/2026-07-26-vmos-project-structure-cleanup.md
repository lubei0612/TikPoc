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
