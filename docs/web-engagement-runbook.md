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

首次加载扩展后，在 `chrome://extensions` 打开“开发者模式”，从
`TikPoc Event Bridge` 卡片读取 32 位扩展 ID，并把精确来源写入 `.env.local`：

```bash
TIKPOC_BROWSER_EXTENSION_ORIGINS=chrome-extension://EXTENSION_ID
```

只从 Chrome 的可见扩展管理页面读取该 ID，不读取 Chrome Profile 文件、Cookie、
Token 或登录存储。多个受控 Profile 使用不同扩展 ID 时，以英文逗号分隔多个完整
`chrome-extension://...` 来源。服务未配置有效扩展来源时，浏览器控制 API 默认关闭；
TikTok 页面来源不会获得绑定、事件、计划、租约、结果或健康接口权限。修改配置后需
重启本机服务，再重载扩展和 TikTok 页面。

```bash
mkdir -p /Users/Shared/TikPoc

uv run tikpoc serve \
  --db /Users/Shared/TikPoc/tikpoc.db \
  --host 127.0.0.1 \
  --port 8766 \
  --web-accounts config/web-accounts.yaml
```

打开 `http://127.0.0.1:8766/inbox` 查看中文线索工作台。服务必须保持在 loopback；不要把浏览器事件、计划、租约、健康或管理接口暴露到公网。

### 3.1 AI 与私域设置

打开 `http://127.0.0.1:8766/settings`：

1. 在“AI 服务”中配置 OpenAI 兼容转发地址、模型和写入式 API Key。
2. 保存后 Key 输入框保持为空，服务端只返回“已配置”状态。
3. 点击“测试 AI 连接”，只核对成功状态、模型和延迟，不记录回复正文。
4. 在“账号自动化”中为每个账号分别配置品牌名称、默认欢迎语言、回关后欢迎开关、商品事实、FAQ 和回复语气。WhatsApp、Telegram 等运营联系方式可留作本机管理信息，自动回复不会直接输出这些值。
5. 未收到客户文字时，关注欢迎语使用默认欢迎语言；客户回复后跟随客户语言。首次自动消息只介绍一次品牌 AI 客服身份。
6. AI 回复和自动回关开关仍在线索工作台按账号独立控制；自动绑定接口会把数据库中的实时开关同步到扩展。

设置保存在忽略提交的 `config/secrets/operator-settings.json`，权限必须为
`0600`。空 Key 保存会保留当前 Key；只有明确点击清除命令才删除已保存值。

## 4. 配置 Chrome Profile

每个账号重复以下步骤：

1. 新建或打开专用 Chrome Profile，并只在该 Profile 登录对应 TikTok 账号。
2. 打开 `chrome://extensions`，启用开发者模式，点击“加载已解压的扩展程序”。文件夹选择器中按 `Command+Shift+G`，输入当前工作树的 `chrome-event-bridge/` 绝对路径并选择该目录。
3. 打开已登录的任意 TikTok 页面。扩展默认按页面可见用户名自动匹配服务端唯一账号映射，不需要填写 Account ID 或 Device ID。
4. 新 Profile 默认使用 `http://127.0.0.1:8766`，并开启自动识别、浏览器自动化、自动回关、私信回复和 Activity 自动打开；已保存的人工开关选择不会被默认值覆盖。
5. 登录 TikTok 后点击扩展弹窗中的“开始监控”。扩展会复用或创建一个普通 TikTok 观察页和一个 Messages 页，刷新复用页面，按可见用户名自动绑定唯一账号并恢复健康检查，不要求操作人预先打开 Activity、Messages 或账号主页。
6. 检查扩展弹窗中的 Profile、预期用户、页面用户和绑定状态。首次启动或基线版本升级时，动作保持关闭，只有 Activity 和 Messages 都显示“已就绪”并完成安全基线后，再开启“自动回关”或“私信回复”。

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

### 4.3 持续观察边界

- Chrome Profile 和至少一个对应 TikTok 页面必须保持运行；Chrome 完全退出后，本地扩展没有后台 DOM 可观察，服务端不会声称仍在监控。
- Messages 观察器同时支持 `/messages*` 和 `/business-suite/messages*`。TikTok 在两类路由间跳转时，扩展继续使用同一账号基线和持久去重记录。
- Activity 与 Messages 都有 15 秒 watchdog；DOM 变化、页面可见性变化、同页路径变化和健康 tick 也会触发串行扫描。多个触发只合并为一个执行中的扫描和一个待执行扫描。
- 健康记录包含 `last_scan_at_ms`、`last_success_at_ms` 和 `scan_state`，不包含消息正文。绑定心跳新鲜但成功扫描过期时，运营台仍按过期处理。
- AI 回复或自动回关开关关闭时，观察器继续建立只读扫描健康；动作开关只禁止计划领取、关注点击和消息发送。

## 5. 基线与去重

- Profile 首次绑定、换绑或首次打开 Activity/Messages 时，当前可见历史内容只用于建立账号基线，不执行历史回关或历史回复。
- Activity 关注基线带版本号。旧版数字基线不会直接进入动作扫描；面板有历史关注行时立即把全部可见行标记为基线，暂时为空时至少稳定观察 30 秒后才建立空基线，避免延迟加载的历史通知被当作新关注。
- 页面重载和 DOM 重新渲染继续使用账号基线与持久已处理记录，不重复发送同一入站指纹。
- 相同 follower、conversation、message、timestamp 或动作键出现在不同账号时，按 `account_id` 分别持久化。
- 每次可见回关或发送前必须取得账号级动作租约；发送后必须看到对应可见状态才记为完成。
- 回关动作必须先达到 `completed`，服务端才按标准化 `(account_id, follower_username)` 创建一条欢迎计划。Messages 空闲扫描优先处理新入站消息，其次才领取欢迎计划。
- 欢迎发送使用独立 `welcome_send` 租约，并在目标用户名唯一匹配、当前参与者再次核对且没有既有对话内容后才发送。重载、重复关注事件和扩展重启不创建第二条欢迎消息。
- 欢迎语以是否对 IKUN 镜像品质包感兴趣为核心，只能引导客户查看 TikTok 主页链接或置顶作品中的联系方式，不承诺转人工，也不直接打印本机保存的联系方式。
- 客户明确要求停止联系时，会话生成空的 `closed` 计划，并按 `(account_id, participant_username)` 持久抑制后续欢迎、回关领取和 AI 回复；同名用户在其他账号下不受影响。
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
3. 从 B 对 A 发起一次新关注，确认 A 只回关一次且按钮可见状态已变化；随后确认 A 只向 B 发送一条欢迎私信。
4. 从 A 对 B 重复一次，确认反方向同样只有一次回关和一条欢迎私信。
5. 每个方向发送三条新私信，确认每个入站指纹只生成一个计划并只出现一条可见出站回复。
6. 第二次有效对话或购买信号触发主页链接/置顶作品联系方式引导；24 小时冷却内不重复引导，回复不得输出保存的 WhatsApp 或 Telegram 值。
7. 重载页面并触发列表重新渲染，确认关注和私信均不重复执行。
8. 使用新的受控会话发送明确停止联系请求，确认服务端创建空 `closed` 计划、页面没有可见出站消息，后续同账号欢迎和回关领取均被抑制。
9. 验证两个账号的相同会话标识、消息标识、用户名、时间戳和动作键分别形成计划、租约、结果、漏斗和健康记录。
10. 确认手机任务在整个网页验收期间持续前进，没有被 Activity 或 Messages 导航打断。

验收记录只保存脱敏账号代号、时间、计划/动作状态、漏斗阶段和手机进度。不要保存消息正文、联系方式、私域目的地、Cookie、Token 或含个人数据的截图。

## 9. 当前门禁

双账号合成隔离测试覆盖指纹、关注去重键、基线、计划、动作租约、结果、漏斗和健康记录。真实双账号可见动作验收仍以第 8 节逐项记录为准；接口返回成功或自动化点击调用本身不代表验收完成。

2026-07-19 最新真实检查点：两个专用 Profile 的 Activity 与 Messages 健康为
`4/4` 已就绪；双向可见关注状态和双向人工私信送达的既有证据继续有效。全局 AI
服务和两个账号的私域/销售设置已写入本机忽略配置，文件权限为 `0600`；真实模型
连接测试成功。两个账号的 AI 回复、自动回关、私域就绪和模型就绪状态均已开启，
自动绑定会同步数据库中的实时开关。

本轮受控会话可以各方向持久送达一条消息，但连续气泡会在发送方重载后消失；
已送达的新消息只在接收页重载后出现。扩展按历史保护规则把启动时可见内容建立为
基线，因此本轮没有新增 AI 计划、私信发送租约或关注租约，也没有可见自动回复可
计为通过。不要从 `4/4` 健康或模型连接成功推断 live send 已通过。下一次只需使用
一条能实时推送 DOM 变化、允许连续收发的新鲜受控会话，完成自动回复、重载去重、
渠道偏好、单渠道邀请、联系方式阶段和人工接管；使用一次新关注事件完成自动回关
租约与可见按钮状态验证。

Supabase 第一阶段已部署：中心库保存账号映射、目标池、设备健康、线索、漏斗事件和
销售结果；设备事务日志、租约、动作额度和断点暂留 SQLite。服务端密钥只保存在本机
`config/secrets/` 的 `0600` 忽略文件中，浏览器扩展不接触密钥。真实去重导出已按
确定性池 ID 导入 `16,384` 个目标并完成只读计数核验。运行时仍以 SQLite 完成动作
原子性和真实验收，不要直接从 Supabase 表驱动可见点击或发送。目标池分批同步期间
标记为 `importing`，只有清理旧行、全部批次成功并核对计数后才更新为 `complete`。

2026-07-19 客服与关注欢迎增量已实现：后台增加品牌名称、默认欢迎语言和回关后
欢迎开关；普通回复使用温和专业的品牌客服结构，首次说明 AI 身份；已确认回关可
生成账号隔离、按用户名幂等的欢迎计划，Messages 使用精确目标和独立租约发送。
本地两个账号的 IKUN 品牌与业务事实已写入忽略配置且保持 `0600`。自动化回归为
Python `642`、Chrome `76`、前端 `35`，生产构建、Android 构建、Ruff 和格式检查
通过。真实双账号欢迎私信、自动回复和后续私域阶段仍须按第 8 节获得新的可见证据，
本条不把自动化回归计作真实发送成功。

TikTok Business Messaging API 保留为兼容路径，不是当前 Chrome 多账号默认运行方式。

### 9.1 自主异步客服实现检查点（2026-07-19）

- 自动客服策略、产品兴趣欢迎、主页/置顶作品引导和多语言停止联系已实现；原先需要人工接管的支付、退款、投诉、取消、折扣和代表请求现在由 AI 简短回应并引导主页联系方式，不承诺实时转接。
- 明确停止联系会持久写入账号隔离的抑制表，取消尚未发送的欢迎计划，并阻止后续欢迎、回关领取和 AI 生成；已发送或结果不确定的历史证据保持不变。
- 两类 Messages 路由、15 秒串行 watchdog、健康 tick 实际扫描、页面可见性/路径恢复、Activity 面板限频重开和扫描健康持久化已进入扩展与服务端。
- 最新回归：Python `687/687`、Chrome `83/83`、浏览器健康 Python `108/108`、前端 `35/35`；生产前端和 Android 构建、Ruff 通过。
- 当前运行服务识别到两个账号映射且两者 AI 回复已开启；自动回关仍关闭。四条浏览器绑定心跳中 Messages 身份可见，但扫描时间仍为零，说明两个 Profile 尚未重载包含新扫描字段的扩展版本。
- 下一真实门禁：两个 Profile 手动重载扩展并恢复新鲜 `4/4` 扫描健康；之后再开启分账号自动回关，执行一次新关注、一次欢迎、后台自然语言回复、购买兴趣引导、重载去重和新的停止联系会话。没有可见结果前不记录真实发送通过。

### 9.2 双账号受信输入检查点（2026-07-20）

- 实现仍按 `config/web-accounts.yaml` 中的任意账号数量运行；本轮只使用两个受控 Chrome Profile 验收，没有写死双账号数量。
- 两个 Profile 的 Activity 与 Messages 扫描健康恢复为新鲜 `4/4`，连续基线观察没有把启动时历史未读内容重新生成计划。两个账号的自动回关保持开启。
- 购买咨询在后台 Messages 页面被检测，AI 生成的回复包含 IKUN 客服身份、商品兴趣问题以及 TikTok 主页链接/置顶作品引导，不包含本机保存的 WhatsApp、Telegram 或密钥。
- 内容脚本使用合成的 `contenteditable` 输入事件时，TikTok 页面返回“请稍后重试”，计划和动作租约保持 `uncertain` 且没有自动重试。同一条 AI 回复正文不变，改用 Chrome 受信输入后发送成功；计划 `13` 对账为 `sent`，`dm_send:13` 对账为 `completed`。双方页面重载后该回复各可见一次，没有重复消息。
- 受控对照同时证明账号权限、模型输出和 TikTok 消息内容可发送；当前剩余门禁是让扩展或本地伴随进程使用受信键盘输入，而不是继续派发页面合成事件。在该门禁关闭前，两个账号的 AI 回复开关保持关闭，避免重复触发 TikTok 错误。
- 调试期间发送端页面被历史会话扫描切换，一条测试询问进入了非目标历史会话。后续验收在发送前暂停发送端 AI、核对目标主页链接和唯一编辑器，避免再次发生目标漂移。
- 最新完整回归：Python `701/701`、Chrome `87/87`、前端 `35/35`；生产前端构建、Android 构建、Ruff 和 `git diff --check` 通过。
- 自动回关的账号隔离、租约、去重和欢迎计划自动化回归通过，运行开关已开启；本轮没有制造新的关注事件，因此新的可见回关按钮变化和回关后欢迎仍是开放的真实门禁。

### 9.3 一键监控与历史关注保护检查点（2026-07-20）

- 扩展弹窗已提供 Profile 本地的“开始监控/停止监控”。开始时检查 loopback 服务，复用或创建普通 TikTok 与 Messages 两类页面，刷新复用页面，并在可见用户名唯一匹配后绑定账号；后台恢复只补齐缺失页面，不重复创建标签页，也不覆盖运营人员主动关闭的 AI 或自动回关开关。
- 新 Profile 默认 Dashboard 为 `http://127.0.0.1:8766`，自动识别、浏览器自动化、自动回关、私信回复和 Activity 自动打开均为开启状态；已经显式保存过的值保持原样。
- 真实启动时发现旧版 Activity 基线过早在空面板上建立，随后延迟加载的历史关注通知被误判为新关注，产生了历史回关和欢迎尝试。发现后立即关闭两个账号的 AI 回复和自动回关；历史 `uncertain` 证据保留且不重试。
- 修复把 Activity 基线升级为版本 `2`：旧基线强制重新建立；有可见关注行时全部只记为历史基线，空面板需连续稳定 30 秒才可建立空基线。自动回关关闭时仍会完成只读基线，但不会领取租约或点击关注。扩展更新后，已处于监控状态的 Profile 会自动刷新复用的观察页和 Messages 页。Chrome 回归最新通过 `110/110`，前端 `35/35`、生产构建和 Android 构建通过。
- 两个 Profile 已重载版本 `2` 基线扩展，Activity/Messages 恢复为新鲜 `4/4`。只读观察期间关注事件和欢迎计划上界保持不变，历史“回关”按钮仍保持原状；随后重新开启两个账号的 AI 回复与自动回关，继续观察也没有重放历史关注或创建历史欢迎。
- 重新开启后，两个受控账号各生成一条新的 AI 回复计划，均通过受信输入自动记为 `sent`，对应 `dm_send` 租约均为 `completed`；当前可见会话中回复只出现一次。新的自然关注事件及其回关后欢迎仍需等待新关注到达后记录可见按钮变化。
- 最新完整 Python 回归受到同一工作树中未提交的移动获客改动影响：`721` 通过、`1` 个 acquisition API 用例失败；该失败不在本次 Chrome 基线提交的文件范围内，需待并行移动改动稳定后重新运行完整门禁。

### 9.4 回关冷却与单次验证检查点（2026-07-20）

- 受控账号之间的关注点击曾短暂显示已关注，但页面重载后恢复为未关注；接收账号没有产生新的粉丝事件、回关租约或欢迎计划。因此该轮属于平台关注回滚，不计为真实回关通过。
- 服务端现在为每个账号持久保存独立的回关熔断状态：`closed` 正常执行、`cooldown` 只读观察、`canary` 只放行一个新的回关动作。一次不确定结果会让对应账号进入 24 小时冷却；冷却到期后只有一个动作键可以领取租约，完成后恢复正常，再次不确定则重新冷却。
- 线索工作台分别显示“回关正常”“回关冷却中”和“等待单次验证”。冷却只锁定该账号的自动回关开关；AI 回复、Messages 扫描、Activity 只读扫描和其他账号保持独立运行。
- 当前两个受控浏览器账号均已写入 `platform_follow_reverted` 冷却，自动回关偏好关闭，AI 回复保持开启。等待一个 watchdog 周期后 Activity 与 Messages 仍为新鲜 `4/4`，没有新增回关租约或欢迎计划。
- 手工恢复检查优先在对应账号的 TikTok 手机端或专用浏览器 Profile 对另一个受控账号执行一次关注，然后重载目标主页。只有关注状态在重载后仍保持，才进入下一步单次验证；不得用历史粉丝或其他用户测试。
- 单次验证流程：等待冷却到期或由运营命令进入验证状态；制造一个新的受控关注事件；确认只领取一个回关租约；重载后仍显示已关注/好友；租约状态为 `completed`；只创建一个欢迎计划并只出现一条欢迎私信；再次重载不得重复。
- 本次回归中浏览器/API 相关测试全部通过；完整 Python 为 `761` 通过、`2` 个同工作树未提交移动获客用例失败，失败文件为移动 assignment fence/worker 处理测试，不在回关提交范围内。Chrome `111/111`、前端 `36/36` 和生产构建通过。

### 9.5 TikTok Developer 公共站点（2026-07-20）

IKUN Product Publisher 的公开产品说明、隐私政策、服务条款与 OAuth 回调页面已部署到 Vercel 生产别名，并完成四条 HTTPS 路由与安全响应头核验：

- Website URL: `https://tiktok-developer-site-eta.vercel.app/`
- Terms of Service URL: `https://tiktok-developer-site-eta.vercel.app/terms`
- Privacy Policy URL: `https://tiktok-developer-site-eta.vercel.app/privacy`
- OAuth Redirect URI: `https://tiktok-developer-site-eta.vercel.app/oauth/callback`
- Platform: `Web`

OAuth 回调页只读取 TikTok 返回的 `code`、`state`、`error` 和 `error_description`，不把授权码写入日志或浏览器存储，并在渲染结果后清除地址栏查询参数。静态站点不需要也不保存 TikTok Client Secret。TikTok Developer 应用继续保持 Draft；完成 Login Kit、Content Posting API、所需 scopes、真实服务端 OAuth 交换和端到端演示视频后再提交审核。
