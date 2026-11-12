# Reader 架构

本文描述当前代码的系统组成、职责、数据归属和关键运行流程。源码链接是核对入口；线上部署状态需要另行核实。
开发命令和工作约定见 [AGENTS.md](../AGENTS.md)。系统、运行单元和组件关系见 [architecture/workspace.dsl](../architecture/workspace.dsl)。

## 系统组成

Reader 采用前后端分离结构。后端是一个 Python 包 `reader`，以 API 和 Worker 两个进程运行，共用业务模块、SQLAlchemy 模型和 PostgreSQL。客户端通过 HTTP API 读写 Reader 数据；第三方网页与视频由阅读页的 WebView 直接加载。Reader 面向自己和少数熟人使用，一个部署就是一组共享来源的账户。

| 运行单元 | 职责与依赖 | 代码入口 |
| --- | --- | --- |
| Expo 客户端 | Android App（另有仅供开发的 Web 预览）：连接服务器、账户会话、今日／榜单／订阅／收藏／我的五个标签页、阅读页（原网页、阅读模式、YouTube 播放器）及其中的实时翻译 | [src/app/_layout.tsx](../mobile/src/app/_layout.tsx) |
| 客户端本地状态 | 服务器地址、部署令牌和账户令牌在原生端存 SecureStore（仅供开发的 Web 预览存浏览器 localStorage）；界面语言存在 AsyncStorage；查询缓存只在内存 | [storage.native.ts](../mobile/src/lib/connection/storage.native.ts)、[interface-language.ts](../mobile/src/lib/interface-language.ts) |
| FastAPI API | 服务发现、账户、订阅、信息流、收藏、榜单、翻译请求，以及给 AI 用的 `/mcp`；访问 PostgreSQL、榜单上游和模型服务 | [main.py](../backend/src/reader/main.py) |
| Reader Worker | 来源扫描、后台标题翻译、每日清理；每个循环写心跳 | [worker.py](../backend/src/reader/worker.py) |
| PostgreSQL | 全部业务数据、账户会话、翻译缓存兼任务队列、扫描租约、翻译额度和 Worker 心跳，共 14 张表 | [models.py](../backend/src/reader/models.py)、[migrations/](../backend/migrations/) |
| Scweet 配套服务 | 可选：采集 X 账号，自行保存采集账号与状态；后端用内部令牌调用 | [scweet_service/app.py](../services/scweet_service/app.py) |
| Codex 凭据文件 | 可选翻译引擎的专用登录；API 与 Worker 在跨进程文件锁内刷新并原子写回，Docker 使用独立数据卷 | [codex_auth.py](../backend/src/reader/translation/codex_auth.py) |
| 管理命令 `reader-admin` | 在服务器上运行：重置密码、签发 MCP 令牌、导入 Codex 登录、查看翻译用量 | [cli.py](../backend/src/reader/cli.py) |

外部依赖：RSS／Atom 站点、YouTube（Data API 与公开 Atom、字幕）、Scweet、Reddit、各榜单上游（Hacker News、GitHub Trending、TechTwitter（自 2026-11-09 起拦截服务器请求，App 和 MCP 暂时隐藏该榜单）、Product Hunt），以及翻译模型（DeepSeek、OpenRouter、Codex 订阅）。启用条件由 [settings.py](../backend/src/reader/core/settings.py) 决定。网页爬取和规则编写不在 Reader 内，计划作为独立服务另行实现。

## 模块职责与依赖

后端按业务分包，多数包内分为路由（`api.py`）和用例（`service.py`），用例不依赖 FastAPI；也有少数操作直接在路由里读写数据库（例如从榜单收藏）。修改时以实际代码为准。

| 模块 | 负责的事情 | 主要协作边界 |
| --- | --- | --- |
| [core/](../backend/src/reader/core/) | 配置、数据库会话、统一错误格式、部署令牌门禁、分页游标、失败认证限流 | 被所有包使用 |
| [accounts/](../backend/src/reader/accounts/) | 注册、登录、会话与刷新令牌、改密码和邮箱、资料与头像 | `auth.py` 的 `current_user` 是所有登录路由的身份来源 |
| [subscriptions/](../backend/src/reader/subscriptions/) | 订阅、改名／分组／是否进「全部」、退订、已看位置、来源图标中转 | 新来源交给扫描器；添加网站时调用 [feed_discovery.py](../backend/src/reader/ingestion/feed_discovery.py) 找 RSS／Atom |
| [ingestion/](../backend/src/reader/ingestion/) | 来源身份规范化、抓取安全、各来源适配器、扫描与入库、每日保留清理 | 入库事务里为新标题排队翻译（[titles.py](../backend/src/reader/translation/titles.py)） |
| [reading/](../backend/src/reader/reading/) | 信息流、更新统计、单篇文章、收藏列表与收藏／取消 | 读取已有标题译文附在条目上 |
| [boards/](../backend/src/reader/boards/) | 各榜单的最新快照（按需抓取）与从榜单收藏 | 收藏时把榜单条目存成文章 |
| [translation/](../backend/src/reader/translation/) | 唯一的翻译服务：缓存、领取、分批调用模型、写回；引擎目录、全局字符额度、后台标题翻译、翻译偏好 | API 的 `POST /translations` 与 Worker 的标题循环共用同一个 `Translator` |
| [mcp/](../backend/src/reader/mcp/) | API 进程内的 `/mcp` 端点和工具 | 直接调用上面各包的用例，不经 HTTP 回环 |
| [system/](../backend/src/reader/system/) | 服务发现、`/health`、`/ready`、`/worker-ready`、Worker 心跳 | — |

客户端按「路由装配 → 视图 → 纯逻辑」分层，编写规则见 [React 开发规范](engineering/react-guidelines.md)：

| 模块 | 负责的事情 |
| --- | --- |
| [src/app/](../mobile/src/app/) | Expo Router 路由；每个路由文件只做装配：读数据、持有页面状态、把数据和回调交给视图 |
| [src/screens/](../mobile/src/screens/)、[src/components/](../mobile/src/components/)、[src/ui/](../mobile/src/ui/) | 视图、带状态的复用组件（信息流、阅读页、WebView）、共享 UI 组件 |
| [src/lib/connection/](../mobile/src/lib/connection/)、[api/](../mobile/src/lib/api/) | 会话状态机与唯一的会话存储、服务发现、HTTP 客户端与错误类型 |
| [src/lib/queries/](../mobile/src/lib/queries/) | 每个页面的服务端数据和命令；命令自己更新缓存、失败时回滚 |
| [src/lib/](../mobile/src/lib/) 下的纯逻辑 | 每个页面的数据整理、表单规则和页面状态机，配 `node --test` 单元测试 |
| [src/lib/injected/](../mobile/src/lib/injected/) 与宿主 | 注入 WebView 的网页翻译／阅读模式运行时和 YouTube 字幕运行时；App 一侧的宿主见[翻译](#翻译) |
| [src/lib/i18n/](../mobile/src/lib/i18n/) | 界面文字表（简体中文、English）；界面语言与内容翻译的目标语言互相独立 |

## 数据归属

表结构见 [models.py](../backend/src/reader/models.py)。

| 数据 | 归属与约束 |
| --- | --- |
| 来源 `sources` | RSS 源、YouTube 频道、X 账号或 subreddit，按规范化标识去重，所有人共享。扫描状态（下次扫描时间、租约、连续失败、最近错误）在来源上；新内容序号 `latest_sequence` 只增不减 |
| 订阅 `subscriptions` | 属于用户：自定义名称、分组、是否启用、是否进「全部」、已看到的序号。改动只影响本人；退订只删除本人的订阅，来源保留，没有任何启用中的订阅时才停止扫描，重新订阅可接着用 |
| 文章 `articles` | 属于一个来源，不跨来源去重；从榜单收藏的条目没有来源，榜单名记在 `extra` 里 |
| 收藏 `saved_articles` | 属于用户，独立于订阅；保留清理永不删除被任何人收藏的文章 |
| 账户 | `users`（含密码哈希、资料、头像数据、MCP 令牌哈希）、`sessions`（一台设备一条）、`refresh_tokens`（只存哈希，一次性使用） |
| 翻译 `translations` | 缓存兼任务队列：按用途、原文哈希、目标语言、引擎指纹唯一，所有用户共享；30 天未使用的在每日清理中删除 |
| 翻译偏好 `translation_preferences` | 每个用户的目标语言、是否自动翻译标题、选用的引擎 |
| 榜单快照 `board_snapshots` | 每个榜单版本只保留最新一份；长时间没人打开的在每日清理中删除 |
| 运行状态 | `source_scan_runs`（扫描历史）、`provider_usage`（YouTube 配额用量）、`translation_budget`（全局字符额度）、`worker_heartbeats` |
| 客户端本地 | 连接与令牌在原生端的密钥库（Web 预览为 localStorage），界面语言在 AsyncStorage；查询缓存、列表位置、阅读模式提取的正文只在内存，不写回服务端 |

## 关键运行流程

### 连接与账户会话

1. 用户输入服务器地址和部署令牌。客户端请求 `/.well-known/reader.json`（[discovery.ts](../mobile/src/lib/connection/discovery.ts)），取得服务器 ID 和 API 地址，让用户确认后保存。
2. 除健康与就绪检查、OpenAPI 文档、头像和来源图标（图片组件不带请求头，按随机 ID 寻址）外，每个请求都要带部署令牌 `X-Reader-Server-Token`（[server_access.py](../backend/src/reader/core/server_access.py)）；账户路由另外校验 Bearer 访问令牌和数据库中未撤销的会话（[auth.py](../backend/src/reader/accounts/auth.py)）。
3. 访问令牌是 15 分钟有效的 JWT；刷新令牌一次性使用，重复使用会撤销整台设备的会话。改密码会结束其他设备的会话，并给当前设备换发新令牌（[accounts/service.py](../backend/src/reader/accounts/service.py)）。
4. 客户端只有一个会话存储（[session-store.ts](../mobile/src/lib/connection/session-store.ts)）：负责恢复、登录、刷新、退出和换发。并发请求遇到 401 时合并为一次刷新；换发进行中出现的刷新失败会等换发结果，不会误把用户登出。
5. 每个查询键都以「服务器 + 账户」开头（[query.ts](../mobile/src/lib/query.ts)），命令执行前确认身份没变；切换服务器或账户后，旧账户的数据和迟到的结果不会出现在新账户里。

### 订阅、扫描与入库

1. 添加订阅时，API 规范化来源身份（[identity.py](../backend/src/reader/ingestion/identity.py)）；类型为「网站」时先找 RSS／Atom，找不到返回 `feed_not_found`。同一来源被多人订阅时共用一条来源记录。
2. Worker 每隔 `APP_WORKER_POLL_INTERVAL_SECONDS` 领取到期来源（[scanner.py](../backend/src/reader/ingestion/scanner.py)）：在来源行上设租约，按新到旧读取上游，读到已知条目或达到上限就停；首次扫描只读一页作为基线，不算新内容。
3. 读取期间不写库；读完后在一个事务里、持有来源行锁写入：新条目取得来源的下一个序号（「有更新」角标的依据），有变化的条目原地更新，新标题排队翻译，扫描标记完成。租约已过期的扫描什么都不写；失败记录错误并退避。
4. 适配器（[adapters/](../backend/src/reader/ingestion/adapters/)）：RSS／Atom 一次读完整个 feed；YouTube 有 API key 且配额够时用 Data API，否则用公开 Atom；X 经 Scweet；Reddit 读 subreddit 的公开 RSS。面向公网来源的出站请求经 [url_safety.py](../backend/src/reader/ingestion/url_safety.py) 校验公网目的地并有界读取；管理员配置的私有 Scweet 服务不做公网目的地校验，但响应同样有界读取。
5. 每日清理（[retention.py](../backend/src/reader/ingestion/retention.py)）按来源保留最新的 `APP_ARTICLES_KEPT_PER_SOURCE` 篇文章（按首次看到的时间排序，收藏的文章也占名额），更早的文章只要有人收藏就不删；扫描历史只保留有限条。

### 信息流、收藏与榜单

- 「全部」和分类信息流只包含用户启用的订阅，「全部」另外只含选择进「全部」的订阅；单独打开自己的某个订阅时，即使它已停用也能看到保留的文章。更新统计按订阅的「已看到序号」计算新内容数，客户端打开某个订阅后显式标记已看（`POST /subscriptions/{id}/viewed`）。X 的自回复串折叠到首条（[reading/service.py](../backend/src/reader/reading/service.py)）。
- 文章在用户订阅了其来源或收藏了它时才能打开。
- 榜单（[boards/service.py](../backend/src/reader/boards/service.py)）没有后台刷新，也不保留历史：打开时快照未过期直接返回；过期则当场抓取，最多等 `APP_BOARD_FETCH_WAIT_SECONDS`，超时或失败时返回旧快照并标为过期；手动刷新每个榜单每分钟最多一次；上游限流时暂停抓取，只返回已有数据。Product Hunt 用洛杉矶日历日，已结束的日期抓取后不再过期。
- 从榜单收藏时，条目必须还在当前快照里；它存为一篇没有来源的文章再加入收藏。同一链接之前已被（任何人）从榜单收藏过时复用那篇文章。

### 阅读页

1. 文章按链接决定打开方式（[reader.ts](../mobile/src/lib/reader.ts)）：YouTube 打开嵌入播放器，下面是字幕轴；X 和 Reddit 打开原网页；其他网页默认打开原网页，可切到阅读模式。
2. 阅读模式优先用服务端保存的正文（至少 400 字时，见 [reader.ts](../mobile/src/lib/reader.ts) 的 `hasFullBody`）；否则在当前页面里用 Readability 提取正文（[webReaderExtraction.ts](../mobile/src/lib/injected/webReaderExtraction.ts)），去掉 Reader 注入的译文后交给 App；宿主（[web-translation-host.ts](../mobile/src/lib/web-translation-host.ts)）检查文档身份和大小（上限 512 KiB）。正文在 App 自己生成的阅读文档里先初始化运行时，再用 DOMPurify 清洗后装入。提取不调用服务端，结果只在内存里；页面导航或变化后旧结果作废。
3. 第三方网页不接收 Reader 凭据。Android 上网页不使用 HTTP 缓存，并定期清理站点的 Cache Storage（[webStorage.ts](../mobile/src/lib/injected/webStorage.ts)），Cookie 和登录状态保留。

### 翻译

所有翻译都经同一个服务（[translation/service.py](../backend/src/reader/translation/service.py)）：

1. 先按（用途、原文哈希、目标语言、引擎指纹）查 `translations`，已完成的直接返回。
2. 没人在做的原文由本次请求「领取」：插入一行运行中记录，或接手等待中、失败一段时间后、以及执行者已消失（超过 5 分钟）的记录。领取是逐行原子操作，同一段原文同时到达也只送模型一次；写回时按领取时的尝试次数做栅栏，被接手的旧执行不会覆盖新结果。
3. 领取到的原文分批送模型：先从全局字符额度（每分钟、每天，[budget.py](../backend/src/reader/translation/budget.py)）预留，再调用引擎，检查占位标记后写回。引擎标记为可单独重试的错误会把整批拆成逐条重试；额度用完或其他引擎错误则把这批记为失败。
4. API 请求最多等 `APP_TRANSLATION_WAIT_SECONDS`，返回已完成的部分，其余在后台继续；客户端稍后再问同样的原文即可取到。

两个入口：

- **后台标题翻译**：扫描入库时，为订阅该来源且开启自动翻译的用户所需的（语言、引擎）组合排队；Worker 的 `title_translation` 循环批量处理（[titles.py](../backend/src/reader/translation/titles.py)）。信息流和收藏返回已完成的标题译文。
- **`POST /translations`**：客户端请求缺少的标题、榜单标题与描述、网页片段和字幕。网页和字幕由注入的运行时找出可见文本，交给 App 的调度器（[translation-scheduler.ts](../mobile/src/lib/translation-scheduler.ts)）：可见内容优先、限制并发、等待中的稍后重问、失败退避；结果按文档导航身份、文本和视频切换代次校验后才写回页面。

引擎目录在 [engines.py](../backend/src/reader/translation/engines.py)：DeepSeek、OpenRouter、Codex 订阅，或关闭。Codex 订阅的登录属于 Reader 部署，由管理员用 `reader-admin codex-auth import` 导入专用登录；凭据只在私有文件里，不进数据库、不下发客户端。配置与恢复见[部署说明](deployment.md)。

### MCP

AI 通过 Streamable HTTP 连到 API 进程内的 `/mcp`（[endpoint.py](../backend/src/reader/mcp/endpoint.py)），带两个请求头：部署令牌，以及用 `reader-admin mcp-token create <邮箱>` 签发的个人令牌（数据库只存其哈希）。每次调用以该令牌的用户身份，直接使用与 App 相同的用例。

工具（[server.py](../backend/src/reader/mcp/server.py)）：订阅更新统计、按时间范围列出更新、读取榜单、读取单条内容、读取公开 YouTube 字幕、添加订阅。前五个只读；添加订阅复用订阅用例，只在用户明确要求时由 AI 调用。判断热点、归纳话题和撰写简报是 AI 的工作，工具只报告 Reader 已有的内容。字幕读取（[transcripts.py](../backend/src/reader/mcp/transcripts.py)）只接受校验过的 YouTube 链接，不发送 Reader 凭据，不翻译。

## 必须保持的边界

- **身份与数据访问**：客户端只通过 API 访问 Reader 数据；后端用已验证的身份限定用户数据。某个用户改名、分组或退订，不能改动共享来源或其他人的订阅。
- **内容保留**：删除文章前检查收藏；改变去重、删除或归并逻辑时同时检查收藏、翻译和扫描序号。
- **抓取输入**：用户提供的来源 URL 和上游内容都不可信；面向公网来源的请求经公网目的地校验，所有上游响应都有界读取。
- **Reader 凭据与第三方页面**：第三方网页、视频和注入脚本不接收 Reader 凭据；页面消息和 HTML 一律视为不可信，只在文档身份有效且通过大小检查后使用，并在阅读文档内再次净化。
- **原文与翻译**：原文展示不等待翻译；翻译失败或超出额度时继续显示原文。缓存身份由服务端按原文计算，不信任客户端提供的片段 ID。
- **密钥**：模型 key、Codex 登录、Scweet 令牌只在后端；部署令牌和账户令牌在原生客户端只存密钥库。

## 运行与故障边界

API 启动时建立数据库连接池和 `/mcp` 会话管理，翻译服务和榜单服务在第一次使用时创建，退出时一并清理。Worker 的三个循环各自运行，一个循环出错不会停掉其他循环；`reader-worker --once` 把每项工作跑一次后退出。

扫描与翻译的协调依靠数据库中的租约和领取状态，可以有多个 Worker。榜单抓取的去重、失败认证限流和翻译并发上限在 API 进程内；增加 API 副本时这些限制按副本各自计算，只有全局字符额度在数据库中跨进程共享。

`/health` 表示 API 进程存活；`/ready` 检查运行配置和数据库 schema 版本；`/worker-ready` 根据各循环的心跳判断 Worker 状态。定义见 [system/api.py](../backend/src/reader/system/api.py)。

## 接口与模型入口

- **HTTP 契约**：后端路由与 Pydantic 模型生成 [OpenAPI 快照](../contracts/openapi.json)；[export_openapi.py](../backend/scripts/export_openapi.py) 负责导出，[generate-api-types.mjs](../mobile/scripts/generate-api-types.mjs) 同步快照和[前端类型](../mobile/src/lib/api/schema.gen.ts)。更新规则见 [AGENTS.md](../AGENTS.md)。
- **数据库结构**：[models.py](../backend/src/reader/models.py) 与 [Alembic 迁移](../backend/migrations/versions/)。
- **架构模型**：[architecture/workspace.dsl](../architecture/workspace.dsl) 维护系统上下文、运行单元和客户端／API／Worker 的组件视图；组件的 `sources` 属性指向仓库内代码。

模块职责、数据归属、依赖约束或关键运行流程变化时更新本文件；命令、完整接口字段和决策历史分别更新其所属位置。
