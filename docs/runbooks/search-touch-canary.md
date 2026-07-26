# VMOS 搜索触达 Canary

## 目的

验证 APK 在不依赖持续 ADB 的情况下，通过 HTTPS 自主拉取 `navigation_mode=search` 任务，精确搜索 username、确认主页身份、记录访问，并按版本化组合策略处理作品互动。

## 上线前门槛

1. 服务端和 APK 版本固定，数据库新建并保留失败证据。
2. 账号处于可见正常状态；正式任务保持暂停，先用受控目标检查。
3. APK 已配置 HTTPS API、设备/账号、round 和 scoped token。
4. 搜索只接受唯一完全匹配；搜索不到直接跳过，不触达相似账号。
5. 作品互动必须验证可见最终状态；uncertain 只读核验一次。

## 分级验收

1. **单目标**：搜索框输入准确；唯一完全匹配；最终主页 username 完全一致。
2. **20 目标**：身份、主页访问、作品识别、动作计划和回传 20/20 正确。
3. **100 目标**：记录未命中、歧义、超时、身份不一致、零作品、有作品和互动分布。
4. **30 分钟**：报告实测 mean、p90、每小时触达、漏斗和错误码；短测投影不代替实测。
5. 每设备 mean `< 6.5s`、p90 `< 8.64s` 后才进入日容量验证。

## 结果口径

- `search_no_exact_match`、`search_ambiguous_exact_match`、`profile_identity_mismatch`：终态未触达，不计覆盖。
- `search_surface_timeout`、网络或 API 故障：基础设施 deferred，保留断点。
- 有作品：策略只从 like/favorite/repost 计划；额度仍是硬约束。
- 零作品：确认主页访问后 trace-only。
- 结果记录 `navigation_mode`、`policy_version`、目标身份、访问状态、动作计划/结果和耗时。

## ADB 边界

ADB 只用于首次安装、升级、开启无障碍和诊断。Canary 开始后断开 SSH/ADB 隧道，确认 APK 仍持续拉取、保存断点和回传结果；Mac 离网不应终止设备任务。
