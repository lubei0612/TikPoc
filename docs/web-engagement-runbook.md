# 网页留痕与私信运行手册

## 1. 运行结构

网页线索处理与手机留痕相互独立：

- 每个 TikTok 账号使用一个专用 Chrome Profile。
- 每个 Profile 只绑定 `config/web-accounts.yaml` 中的一条账号映射。
- Activity 页面负责发现新关注并回关；Messages 页面负责发现新私信、生成回复计划、发送并核对可见结果。
- Chrome 扩展只读取可见页面并执行可见点击。Python 服务负责账号配置、SQLite 持久化、会话策略、AI 计划、漏斗和动作租约。
- 手机 worker 继续处理目标批次，不会被网页回关或私信流程导航、暂停或抢占。

账号数量由配置决定，不固定为 2 个或 7 个。生产部署按“一条账号映射、一个 Chrome Profile、一个 TikTok 登录态”水平扩展。

## 2. 配置账号

从 `config/web-accounts.example.yaml` 创建忽略提交的 `config/web-accounts.yaml`。浏览器主路径示例：

```yaml
accounts:
  - account_id: account-01
    device_id: phone-01
    mode: browser
    expected_tiktok_username: shop_one
    browser_profile_label: TikPoc 01
    private_channel_hint: "PRIVATE_DESTINATION"
    offer_context: "Configured catalog facts"
    faq_file: ""
    reply_language: auto
    max_auto_replies: 12
    invite_after_meaningful_turns: 2
    fallback_acknowledgement: "Thanks for your message. What are you looking for?"
    browser_followback_enabled: true
    browser_dm_enabled: true
    enabled: true
```

每条启用的浏览器账号必须满足：

1. `account_id`、`device_id`、`expected_tiktok_username` 在配置内唯一。
2. `expected_tiktok_username` 填写 TikTok 页面实际显示的用户名，可省略前导 `@`。
3. `private_channel_hint` 只写本地真实目的地；未准备好时留空。
4. 商品、价格、交付等事实只来自 `offer_context` 和 `faq_file`。
5. 新增账号只增加配置和 Profile，不复制服务或修改固定账号列表。

密钥、联系方式、登录态、Cookie 和消息内容不得写入提交文件或验收记录。

## 3. 启动本机服务

环境变量放在忽略提交的 `.env.local`。数据库使用本机私有运行目录：

```bash
mkdir -p /Users/Shared/TikPoc

uv run tikpoc serve \
  --db /Users/Shared/TikPoc/tikpoc.db \
  --host 127.0.0.1 \
  --port 8766 \
  --web-accounts config/web-accounts.yaml
```

打开 `http://127.0.0.1:8766/inbox` 查看中文线索工作台。服务必须保持在 loopback；不要把浏览器事件、计划、租约、健康或管理接口暴露到公网。

## 4. 配置 Chrome Profile

每个账号重复以下步骤：

1. 新建或打开专用 Chrome Profile，并只在该 Profile 登录对应 TikTok 账号。
2. 打开 `chrome://extensions`，启用开发者模式，点击“加载已解压的扩展程序”。文件夹选择器中按 `Command+Shift+G`，输入当前工作树的 `chrome-event-bridge/` 绝对路径并选择该目录。
3. 打开已登录的任意 TikTok 页面。扩展默认按页面可见用户名自动匹配服务端唯一账号映射，不需要填写 Account ID 或 Device ID。
4. 打开扩展设置，确认 Dashboard URL 为 `http://127.0.0.1:8766`，保持“自动识别当前 TikTok 账号”开启，并点击“测试连接”。
5. 打开 TikTok Activity 和 Messages 页面，检查扩展弹窗中的 Profile、预期用户、页面用户和绑定状态。
6. 只有 Activity 和 Messages 都显示“已就绪”并建立新基线后，再开启“自动回关”或“私信回复”。

自动识别只接受一个可见 TikTok 用户名与一个启用的服务端映射精确匹配。退出、验证、无匹配或歧义状态不会绑定或执行动作。需要人工处理时，关闭“自动识别当前 TikTok 账号”，从服务端菜单选择映射并保存；换绑必须确认，且只清理旧账号在该 Profile 内的关注/私信基线和已处理记录。

### 4.1 CLI 连接与状态

```bash
uv run tikpoc browser guide
uv run tikpoc browser status --dashboard-url http://127.0.0.1:8766
uv run tikpoc browser connect \
  --web-accounts config/web-accounts.yaml \
  --dashboard-url http://127.0.0.1:8766 \
  --timeout 60
```

`browser connect` 校验本地注册表与服务端脱敏映射完全一致，并等待每个启用账号的 Activity、Messages 两条健康状态。成功输出 `ready=N/N`；状态输出只含账号、Profile、预期/页面用户名、页面角色、绑定状态和心跳年龄。

### 4.2 AI 连接指令

对 AI 说“连接这个 Chrome”时，按以下固定流程执行：

1. 读取当前 TikTok 页面可见用户名，不读取 Cookie、Token 或 Chrome Profile 存储。
2. 检查或启动 loopback TikPoc 服务和本地账号注册表。
3. 扩展或服务更新后重载 TikTok 页面。
4. 运行 `tikpoc browser connect`，等待对应 Activity、Messages 状态均为“已就绪”。
5. 如出现已退出、需验证、无匹配、歧义或身份不符，报告具体状态并保持动作关闭。
6. 已获得真实动作批准且新基线完成后，才开启对应账号的自动回关和 AI 回复。

扩展源码更新后，在 `chrome://extensions` 点击 `TikPoc Event Bridge` 的重新加载按钮，再重载 TikTok 页面。日常新增已配置账号只需在新 Profile 手动加载扩展一次，之后使用自动识别或上述 AI 指令连接。

## 5. 基线与去重

- Profile 首次绑定、换绑或首次打开 Activity/Messages 时，当前可见历史内容只用于建立账号基线，不执行历史回关或历史回复。
- 页面重载和 DOM 重新渲染继续使用账号基线与持久已处理记录，不重复发送同一入站指纹。
- 相同 follower、conversation、message、timestamp 或动作键出现在不同账号时，按 `account_id` 分别持久化。
- 每次可见回关或发送前必须取得账号级动作租约；发送后必须看到对应可见状态才记为完成。
- `uncertain` 保持占用直到对账或租约到期，不立即重试。

## 6. 身份异常恢复

管理后台会分别显示 Activity 和 Messages 的账号状态：`未绑定`、`身份不符`、`已退出`、`需验证`、`已就绪`、`心跳过期`。

出现异常时按以下顺序处理：

1. 暂停该账号对应的“自动回关”或“AI 回复”开关；其他账号和手机 worker 保持运行。
2. 打开对应 Chrome Profile 的 TikTok 页面，处理退出登录或页面验证。
3. 核对页面可见用户名与 `expected_tiktok_username`，不要通过改写客户端状态绕过不匹配。
4. 如配置确需变更，先改服务端账号映射，再在扩展菜单中确认换绑。
5. 等 Activity/Messages 心跳恢复为“已就绪”，重新建立基线后再开启该账号开关。

## 7. 分账号停止开关

“线索工作台”中的账号控制彼此独立：

- “AI 回复”只控制该账号的 Messages 计划和发送。
- “自动回关”只控制该账号的 Activity 回关。
- 页面身份不健康时，只锁定受影响账号和对应功能。
- 关闭浏览器账号开关不会暂停手机留痕任务，也不会操作其他 Chrome Profile。

## 8. 双账号真实验收清单

真实动作开始前先通知操作人。只对身份显示“已就绪”的两个专用 Profile 执行，缺少或不匹配的登录态由操作人补登录。

1. 启动一个手机留痕任务，记录脱敏的任务 ID 和进度位置。
2. Profile A、Profile B 分别打开 Activity 和 Messages，并确认四条健康状态均为“已就绪”。
3. 从 B 对 A 发起一次新关注，确认 A 只回关一次且按钮可见状态已变化。
4. 从 A 对 B 重复一次，确认反方向同样只回关一次。
5. 每个方向发送三条新私信，确认每个入站指纹只生成一个计划并只出现一条可见出站回复。
6. 第二次有效对话或购买信号触发私域邀请；24 小时冷却内不重复邀请。
7. 重载页面并触发列表重新渲染，确认关注和私信均不重复执行。
8. 发送联系方式，确认进入 `contact_captured`；发送人工请求，确认进入 `human_required`，普通 AI 自动回复停止。
9. 验证两个账号的相同会话标识、消息标识、用户名、时间戳和动作键分别形成计划、租约、结果、漏斗和健康记录。
10. 确认手机任务在整个网页验收期间持续前进，没有被 Activity 或 Messages 导航打断。

验收记录只保存脱敏账号代号、时间、计划/动作状态、漏斗阶段和手机进度。不要保存消息正文、联系方式、私域目的地、Cookie、Token 或含个人数据的截图。

## 9. 当前门禁

双账号合成隔离测试覆盖指纹、关注去重键、基线、计划、动作租约、结果、漏斗和健康记录。真实双账号可见动作验收仍以第 8 节逐项记录为准；接口返回成功或自动化点击调用本身不代表验收完成。

TikTok Business Messaging API 保留为兼容路径，不是当前 Chrome 多账号默认运行方式。
