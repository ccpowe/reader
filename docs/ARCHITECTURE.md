# Reader 架构

本文描述当前工作区的系统组成、职责、数据归属和关键运行流程。源码链接是核对入口；线上部署状态需要另行核实。
开发命令和工作约定见 [AGENTS.md](../AGENTS.md)。系统、运行单元和组件关系见 [architecture/workspace.dsl](../architecture/workspace.dsl)。

## 系统组成

Reader 采用前后端分离结构。后端是一个 Python 应用，分别以 API 和 Worker 进程运行，共用业务模块、SQLAlchemy 数据模型和 PostgreSQL。前端通过 HTTP API 访问 Reader 数据，第三方网页与视频由阅读界面直接加载。

| 运行单元 | 职责与依赖 | 代码入口 |
| --- | --- | --- |
| Expo 客户端 | 页面、服务连接、账户会话、查询缓存、文章／网页／视频阅读；含 Web 预览入口，WebView 阅读能力目前为原生实现 | [App.tsx](../mobile/App.tsx) |
| 客户端本地状态 | 保存连接坐标、界面语言及隔离的凭据；原生使用 SQLite localStorage 适配和 SecureStore，Web 使用浏览器存储或内存 | [storage.ts](../mobile/lib/connection/storage.ts)、[secretStorage.native.ts](../mobile/lib/connection/secretStorage.native.ts) |
| FastAPI API | 服务发现、认证、订阅、信息流、排行、收藏、个人资料和显式翻译请求；访问 PostgreSQL、内容上游、排行及模型服务 | [main.py](../backend/src/app/main.py)、[api/router.py](../backend/src/app/api/router.py) |
| Reader Worker | 来源扫描、候选入库、排行快照、持久翻译任务、网页规则编写与清理；访问同一数据库和相应外部服务 | [workers/main.py](../backend/src/app/workers/main.py) |
| PostgreSQL | 保存 Reader 业务数据、身份会话、任务状态、租约、配额和 Worker 心跳；翻译使用 LISTEN/NOTIFY 唤醒空闲消费者 | [storage/database.py](../backend/src/app/storage/database.py)、[translation/notifier.py](../backend/src/app/translation/notifier.py) |
| Scweet 配套服务 | 在选择 Scweet 作为 X 来源供应商时提供采集接口，独立管理采集凭据和自身状态；通过内部令牌与后端通信 | [scweet_service/app.py](../services/scweet_service/app.py) |
| Scweet 状态库 | 独立保存 Scweet 采集账号与运行状态，不使用 Reader 业务数据库 | [Scweet 服务说明](../services/README.md) |
| Reader 管理 CLI | 按需运行，直接访问 PostgreSQL，管理用户、翻译配额和网页规则任务 | [admin/cli.py](../backend/src/app/admin/cli.py) |

RSS／网站、YouTube、X 供应商、排行提供方和模型提供方是外部依赖。具体供应商及启用条件由 [settings.py](../backend/src/app/core/settings.py) 和相应 adapter 决定。

## 模块职责与依赖

| 模块 | 负责的事情 | 主要协作边界 |
| --- | --- | --- |
| 前端连接与认证 | 发现服务、确认服务身份、切换 runtime、恢复和刷新会话、隔离凭据 | [lib/connection/](../mobile/lib/connection/)、[readerAuth.ts](../mobile/lib/readerAuth.ts) 向页面和 API 客户端提供当前连接 |
| 前端数据与页面 | 发起业务请求、管理服务端数据缓存、展示原文和异步结果 | [lib/api.ts](../mobile/lib/api.ts)、[state/](../mobile/state/) 连接页面与后端；缓存键包含服务器和用户身份 |
| 阅读与翻译界面 | 加载原网页、按需提取和清洗阅读正文，发现可翻译文本、安排可见内容请求并处理导航和视频切换 | [ArticleReaderScreen.tsx](../mobile/screens/ArticleReaderScreen.tsx)、[TranslatableWebView.tsx](../mobile/components/TranslatableWebView.tsx)、[webReaderExtraction.ts](../mobile/domain/webReaderExtraction.ts) 与翻译调度器协作 |
| API 与业务服务 | 校验请求和用户权限，处理订阅、资料、排行、收藏等业务，组装响应 | [api/](../backend/src/app/api/)、[services/](../backend/src/app/services/) 使用共享持久层及抓取、翻译服务 |
| 来源接入与入库 | 来源身份规范化、上游请求、分页、候选去重、内容与媒体入库 | [ingestion/](../backend/src/app/ingestion/)、[workers/sync.py](../backend/src/app/workers/sync.py)、[workers/candidates.py](../backend/src/app/workers/candidates.py) |
| 翻译 | 需求与缓存身份、任务领取、批处理、实时合并、配额和结果生命周期 | [translation/](../backend/src/app/translation/) 调用 provider，并由 API 或 Worker 驱动 |
| 网页规则 Agent | 针对单个来源保存有界证据和发现，生成、执行并评估列表规则后交接激活 | [web_rule_agent/](../backend/src/app/web_rule_agent/)、[workers/web_rules.py](../backend/src/app/workers/web_rules.py) 调用受限工具和规则服务；[browser.py](../backend/src/app/web_rule_agent/browser.py) 统一探索页面接口 |
| 模型适配 | 构造外部聊天模型客户端，集中处理供应商配置 | [llm/factory.py](../backend/src/app/llm/factory.py) 被翻译和规则 Agent 复用；两者分别管理提示词、任务和预算 |
| 持久层 | 数据模型、数据库会话、锁和 schema 就绪检查 | [storage/](../backend/src/app/storage/)、[migrations/](../backend/migrations/) 为 API、Worker 和管理命令共用 |

这些是当前代码的职责划分。API 路由直接使用 SQLAlchemy Session 和 ORM；`services/`、`ingestion/`、`translation/` 之间也存在直接调用。修改模块边界时需要核对实际引用和事务关系，不能仅凭目录名假定已有独立服务或严格分层隔离。

## 数据归属

业务模型定义在 [models.py](../backend/src/app/storage/models.py)，账户模型定义在 [auth_models.py](../backend/src/app/storage/auth_models.py)。

| 数据 | 归属与约束 |
| --- | --- |
| 来源 `FeedSource` | 以规范化来源标识去重；来源级名称、抓取配置、可见性与所有者属于来源。共享来源可被多个用户订阅 |
| 订阅 `SourceSubscription` | 属于用户，关联来源；自定义名称、分组和启用状态保存在订阅上，不改写其他用户的设置 |
| 内容与来源条目 | `Content.authority_source_id` 指定内容的权威来源；URL 哈希在该来源内去重。`SourceEntry` 保存上游条目标识、排序信息和内容关联，数据库约束保证来源归属一致 |
| 阅读状态与收藏 | `UserContentState`、`UserSavedContent` 按用户关联内容；收藏独立于订阅。内容保留清理会保护被任一用户收藏的内容 |
| 账户与资料 | Reader 自有用户、密码哈希、可撤销会话、刷新令牌哈希和个人资料存于 PostgreSQL；头像数据也存于数据库 |
| 排行快照 | 服务端按榜单查询维度缓存；打开榜单或预览不等于订阅或收藏，显式收藏才建立持久收藏关系 |
| 翻译结果与工作 | `TranslationArtifact` 保存成功结果，`TranslationWork` 保存持久执行状态；身份包含文本、用途、语言、引擎指纹和作用域。网页片段与字幕采用用户作用域，公共标题等可共享 |
| 后台运行状态 | 来源游标、候选、扫描租约、规则任务、预算和心跳由服务端管理。规则任务独立保存候选、执行事实、模型判断与版本绑定凭据；diagnostics 中的 authoring checkpoint 仅为恢复提示。页面读取有限状态投影，不拥有调度权 |
| 客户端本地数据 | 保存连接坐标、会话凭据、界面语言和缓存；公共连接元数据与凭据分开，查询缓存按服务器和用户隔离。WebView 提取的正文仅保留在当前阅读界面的内存状态，不写入服务端 Content 或离线文章库 |

账户信息和翻译偏好由后端管理；界面语言由 [mobile/i18n/](../mobile/i18n/) 在客户端管理，两者独立。当前运行链路使用 Reader 自有认证；历史迁移代码中的 Supabase 引用不代表仍依赖其认证服务。

## 关键运行流程

### 连接服务与账户会话

1. 客户端根据用户提供的地址和部署访问令牌请求服务发现，取得服务身份与 API 地址。
2. [ServerAccessMiddleware](../backend/src/app/core/server_access.py) 在路由前保护发现接口及 `/v1`；账户业务还通过 [ReaderJWTVerifier](../backend/src/app/core/auth.py) 验证 Bearer token 和数据库中的有效会话。部署访问令牌与用户身份分别校验。
3. [activateReaderServer](../mobile/lib/connection/runtime.ts) 先构造候选 runtime、恢复对应会话并保存重连信息，再发布新的 runtime。切换失败不会提前替换当前连接。
4. 服务或用户切换时，应用取消旧查询、清空缓存并重建对应页面树。异步结果通过 [generation／session epoch 检查](../mobile/lib/connection/guard.ts) 绑定发起时的连接，避免旧请求污染新会话。

账户会话按服务身份和 API 地址隔离，部署访问令牌按连接地址保存。原生端使用 [SecureStore](../mobile/lib/connection/secretStorage.native.ts)，Web 端使用 [sessionStorage 或内存](../mobile/lib/connection/secretStorage.ts)；公开重连坐标通过 [storage.ts](../mobile/lib/connection/storage.ts) 单独保存。账户刷新由 [readerAuth.ts](../mobile/lib/readerAuth.ts) 合并并发请求，后端 [AuthService](../backend/src/app/services/auth.py) 管理刷新轮换与撤销。

已访问的主标签页及阅读器覆盖层下方页面保留已挂载视图，以维持原生列表的精确位置和返回画面；不可见期间暂停查询、预取、翻译调度和滚动交互。分类分页器只保留当前分类及相邻列表，远离当前分类的列表视图会卸载。查询缓存保护页面恢复所需的身份隔离数据，并清理旧分类、旧搜索和旧排行变体；无限列表不会在用户阅读时截断，列表实际卸载后只能删除当前锚点之后的尾部分页，确保向上阅读和单向游标仍然有效。列表位置、X 卡片成功译文的 200 片段 LRU 及这些页面控制状态都只存在于当前进程内存，X 译文按服务器、账户、目标语言、实际引擎指纹和原文匹配；应用重启或身份切换后重新请求，不建立客户端磁盘内容缓存。

### 订阅、扫描与内容入库

1. [订阅服务](../backend/src/app/services/subscriptions.py) 解析来源身份并建立用户订阅，来源的同步状态与订阅分别维护。
2. [来源扫描器](../backend/src/app/workers/sync.py) 领取到期来源的租约，执行初次扫描、增量扫描或断点续扫，将发现的条目交给持久候选队列。
3. [候选处理器](../backend/src/app/workers/candidates.py) 领取候选，按来源与条目标识完成去重，写入内容、来源条目和媒体，再更新处理状态。
4. 配置了默认翻译引擎时，入库会为标题建立较低优先级的翻译工作；原文先入库，入库流程不等待模型完成翻译。

来源 adapter 统一由扫描器选择：RSS、Web、YouTube 和 X 各有实现。Web 来源优先采用已确认的 RSS／Atom feed；需要发现时执行有界探测，无可用 feed 才进入规则编写流程。临时失败或访问阻断会保留相应状态，不能当成“确认没有 feed”。相关入口为 [feed_discovery.py](../backend/src/app/ingestion/feed_discovery.py) 和 [web_feeds.py](../backend/src/app/services/web_feeds.py)。

订阅发现与阅读正文分别完成。扫描需要取得文章链接、真实标题和适用的续扫能力，正文不是规则激活或订阅更新成功的前置条件。普通 Web 扫描与规则验证只使用列表条目，不再逐篇执行 `article` recipe；该字段仍可解析以兼容旧规则，但只存在于详情页的补充元数据不再自动抓取。RSS 自带内容、列表摘要及数据库已有正文保留，客户端 Web／RSS 阅读模式不以这些内容分流；没有新增正文补抓 Worker。

### 信息流、排行与收藏

信息流查询以当前用户的启用订阅为范围，再应用来源和分组筛选；文章访问检查订阅或已有收藏等允许路径。原文和翻译投影分开返回，客户端可在展示原文后继续查询已加载标题的翻译状态。入口为 [feed.py](../backend/src/app/api/feed.py) 和 [translations.py](../backend/src/app/api/translations.py)。

排行数据经 [ranking_snapshots.py](../backend/src/app/services/ranking_snapshots.py) 管理快照，API 与 Worker 均可参与刷新。榜单响应按当前账户的语言与引擎只读投影已有成功译文，并携带实际引擎指纹，不为缺失项创建工作或等待模型；客户端只采用与已加载语言、启用状态和引擎指纹一致的响应译文，继续显示其他缺失项原文，并仅将缺失字段交给显式片段路径。显式片段产生的成功 Artifact 与榜单响应共用同一身份和存储，后续榜单请求可以直接命中。用户显式收藏榜单条目时，[ranking_saved.py](../backend/src/app/services/ranking_saved.py) 先复用可访问内容，必要时再持久化条目并创建 `UserSavedContent`；Reddit 沿用其来源归属，其他榜单使用不参与订阅扫描的归档来源。

Reddit 订阅的首页内容由 `hot` 快照发布时送入同一候选队列，再经候选处理器入库；这条来源更新路径由排行快照驱动，普通来源扫描器不负责抓取 Reddit。

取消订阅不等于取消收藏。内容保留策略按来源保留最新的一定数量未收藏内容，并保护收藏引用；翻译清理同时检查原文引用、引擎和临时缓存状态。规则见 [content_retention.py](../backend/src/app/workers/content_retention.py) 与 [translation/lifecycle.py](../backend/src/app/translation/lifecycle.py)。

### Web／RSS 原网页与阅读模式

1. [ArticleReaderScreen](../mobile/screens/ArticleReaderScreen.tsx) 根据来源类型让 Web／RSS 文章直接进入原网页，不请求文章正文 API 来决定入口。其他来源保留各自的阅读和视频流程。
2. 用户选择阅读模式时，[页面提取器](../mobile/domain/webReaderExtraction.ts) 对当前 DOM 的副本执行 Readability，移除 Reader 已注入的译文和展示包装，再清洗提取内容。提取不改写正在浏览的原文，也不调用模型或服务端正文抓取任务。
3. [WebView 桥接](../mobile/components/TranslatableWebView.tsx) 对结果类型、UTF-8 消息体积、请求及文档身份做检查。结果上限为 512 KiB；超时、取消、页面变化和过大的结果会结束本次请求，保留原网页。
4. 提取成功后，在应用生成的独立阅读文档中先初始化可信运行时，再用 DOMPurify 清洗正文并装入页面。来自原网页的 HTML 即使声称已净化，也不能直接拼进阅读文档。原 WebView 保留，以便切回；隐藏期间暂停其翻译调度。
5. 正文快照绑定当前 URL、文档和页面代次，导航、正文或提取依赖的页面上下文变化（如 `baseURI`、标题／作者元数据、语言与祖先状态）使其失效。变更判断有界执行，不为每次变更重新提取全文。阅读标题、外链及分享使用当前页面身份；进入另一篇网页后不再沿用原条目的收藏操作，也不自动创建新的服务端内容。

阅读模式复用现有翻译运行时的 `reader` 排版适配，但片段用途保持 `web_segment`，使用用户作用域；不能因进入本地阅读文档就变成共享文章段落翻译。RSS 内容不参与全文判断或正文回退。首次阅读仍需加载原网站；DOM 提取不保证所有页面适配，当前流程不提供正文离线保存或跨设备存档。

### 翻译的三条执行路径

| 路径 | 触发与执行 | 状态归属 |
| --- | --- | --- |
| 持久翻译工作 | 入库标题预热、内容读取产生的缺失需求，以及普通片段的适用回退；Worker 按前台／后台优先级领取批次执行 | PostgreSQL 中的 Work、租约与成功 Artifact；NOTIFY 用于唤醒，轮询作为补充 |
| 普通显式片段 | 排行响应先附带已有成功译文；仍缺失的字段显示原文并请求片段翻译。片段请求命中缓存直接返回，适合直接执行的缺失项在 API 内调用模型，成功结果随后持久化并供榜单响应复用 | [interactive.py](../backend/src/app/translation/interactive.py) 管理直接执行及适用的持久工作回退 |
| 实时片段 | 网页片段、字幕及特定富文本片段由 API 进程中的协调器合并相同需求、限制并发并处理结果 | [RealtimeCoordinator](../backend/src/app/translation/realtime.py) 管理进程内在途任务，成功结果写入数据库；这条路径不承诺重启后恢复在途任务 |

实际路由选择在 [resolve_translation_segments](../backend/src/app/api/translations.py)。翻译偏好与配额由后端读取和执行；用户不能通过片段 ID 指定另一个用户的缓存归属。网页片段和字幕的结果绑定用户、引擎与偏好代次，偏好变更时拒绝旧结果并清理对应临时缓存。

WebView 内的 [网页脚本](../mobile/domain/webTranslation.ts) 和 [字幕脚本](../mobile/domain/youtubeTranslation.ts) 发现文本，由应用桥接层校验消息并交给调度器，再调用 Reader API。结果通过文档导航标识、文本状态和视频 `media_epoch` 校验后应用。脚本由 TypeScript 生成静态运行时资源，生成入口为 [generate-translation-runtime-sources.mjs](../mobile/scripts/generate-translation-runtime-sources.mjs)。

### 网页规则编写与激活

1. 确认没有可用 feed，或已有规则出现符合条件的首窗结构故障后，[web_rule_jobs.py](../backend/src/app/services/web_rule_jobs.py) 建立持久规则任务。普通扫描已取得可用条目时，部分行缺标题或 URL 仅记录警告，不单凭该警告反复派发修复任务；历史续读故障不作为首窗结构故障。
2. Worker 领取任务后，[LangChain Agent](../backend/src/app/web_rule_agent/runtime.py) 通过原生 schema、结构化 DOM 和保存的发现生成候选。模型与供应商配置复用公共模型工厂。
3. [execute_web_rule](../backend/src/app/ingestion/web_rules.py) 直接运行普通 `WebBlogSourceAdapter.scan_page`，最多三段窗口、每段最多 15 项。正常 continuation 的游标与重放检查保持；不额外独立重放首批，也不推断必须覆盖的文章或翻页义务。报告只描述实际标题、URL、可选元数据、各窗口丢弃统计、实际续读与错误。
4. Agent 对照页面判断内容是否满足需求，可继续探索、记笔记或修改并重新执行。`completed` 表示有界执行完成；`partial` 保留后续失败前的结果；`error` 不表示可用规则。能发现当前文章但无法确认历史范围时，可以提交并明确限制，不要求全站完整。
5. 模型通过 `FinalWebRule(rule, execution_id, assessment, limitations)` 交接。宿主核对首窗确实正常完成、同一候选、执行记录、来源修订、租约、TTL 和扫描占用，再原子激活；不会把单次工具成功直接视为最终提交。首窗空结果沿用普通 scanner 的错误语义，只有格式正确或请求失败的规则不能激活。
6. 激活后由正常扫描和候选入库链路发现内容；已有规则的周期运行不调用模型，Agent 的执行样本不直接入库。等待扫描租约期间凭据过期时，同一已批准候选可重新执行刷新凭据，保留模型判断及限制；首窗再次失败则不能激活。

任务租约、预算和运行槽位保存在数据库。重启不刷新原任务的 300 万总 token／80 次模型调用额度。每轮区分包含本次响应的剩余模型调用数、此响应之后的调用数，以及按已记账用量计算的 token 余量和独立估计；提示新试跑后至少留一次调用来评估并交接或有据失败。不按提示阶段关闭探索工具，也不在执行完成后强迫提交。真正的预算、串行调用、来源和传输约束由宿主执行。

| 模型工具 | 边界与作用 |
| --- | --- |
| `web_rule_schema` | 返回原生列表规则 schema |
| `inspect_web_page` | 接收单条 CLI `argv`，执行 open／snapshot／get／eval／click／wait／scroll／find／back／forward；返回有界实际内容与原生错误，只授权最终交付内容中可见的同源完整链接 |
| `read_rule_evidence` | 读取当前 attempt 内保存的页面观察（标记当前、历史或未知状态，不恢复活引用），或按完整执行条目分页读取；省略 `item_start` 时从保存的未读位置继续，默认 5 项、最多 15 项 |
| `record_rule_findings` | 保存带证据引用的简短模型发现，不赋予导航或激活权限 |
| `execute_web_rule` | 保存候选并调用真实 scanner，返回简洁执行事实及 `execution_id`；模型决定是否继续修改或提交 |

[工作记忆](../backend/src/app/web_rule_agent/memory.py) 按完整 AI／工具回复批次裁剪历史，独立保留候选、执行摘要、读取进度和笔记。32 KiB 是旧历史保留目标；最新完整 AI／工具回复批次即使超过这个目标，也必须交给下一次模型调用，避免读取进度跨过尚未交付的条目。单条工具响应上限与实际模型／token 预算保持。执行报告按完整条目保存为有界 JSON，过大时明确报告省略；不会把内部对象切成字符片段要求模型反复拼接。单个条目放不进工具响应时明确报尺寸错误并保留未读位置。失败或 partial 规则执行不会重置探索会话或删除页面证据；探索和 scanner 使用独立浏览器。证据只使用实际 DOM 修订，不把错误文本指纹当成页面版本。历史观察可有界读取但不授权导航；重复查询仍返回实际内容与 repeated 标记。当前执行凭据不因 16 条观察摘要的淘汰而变成未知引用。重启恢复的执行结果仅是历史事实，不恢复旧 DOM 或浏览器导航权限。旧规则预检单独保存诊断和本次初始反馈，不覆盖候选规则的执行报告、模型判断或读取进度；Worker 根据预检的实际首窗事实判断恢复，不依赖工具展示裁剪结果。

数据库沿用 `candidate_rule`、`validation_report`、`validation_receipt` 列：后两者现在保存执行事实和版本绑定凭据，不代表内容质量已由代码判断。模型 `assessment / limitations` 保存于报告和来源版本摘要，可由 `reader-admin web-rules show` 查看；本轮没有新增客户端历史范围展示字段。旧的 `protected_coverage` 不再参与授权或拒绝。管理提交同样要求首窗真实执行成功，允许后续失败的 partial；诊断 CLI 使用 `reader-web-rule execute`，`validate` 保留为兼容别名。

[CLIBrowserSession](../backend/src/app/web_rule_agent/cli_browser.py) 为一次任务保留 agent-browser 0.37.1 与 Lightpanda 会话，跨模型等待、规则试跑和证据读取保留交互状态；结束、取消、超时或失去租约时回收任务。CLI 的 argv、会话、空配置和 CDP 由宿主管理；工具响应加入证据 ID 后按完整 JSON UTF-8 限至 20 KiB，再从最终内容提取导航许可。未配置浏览器任务控制器时，Worker 继续使用本机 bubblewrap 隔离；容器部署则由 [browser_tasks](../backend/src/app/browser_tasks/) 通过内部控制接口申请 `explore` 或 `render` 任务。每个任务包含仅连接自身 Unix socket 的无网络浏览器，以及执行受控 CDP bridge 和 `safe_get` 的 adapter；Worker 只持有任务 capability，不持有 Docker socket。探索任务保留会话，动态规则执行使用独立的一次性 render 任务，因此试跑和周期扫描不会改变探索页面状态。

规则执行／预检与 Worker 周期动态扫描继续通过 [web_crawl.py](../backend/src/app/ingestion/web_crawl.py) 进入同一语义接口；静态页面保留 HTTP 路径，正常运行不自动回退 Chromium。公网目的地、robots、TLS、重定向、请求和传输限制在本地及容器模式都继续适用。容器 adapter 可以访问符合通用公网校验的站点，不维护站点域名白名单；浏览器自身没有 IP 网络，不能绕过 adapter 直连目标。Manager 与独立 Reaper 通过共享 SQLite 状态、租约和硬期限协调任务回收；两者都需要 Docker socket，只有 Manager 持有控制令牌和 capability HMAC key。部署身份同时绑定固定镜像、二进制及运行策略；断线或续租失败会使 Worker 停止使用任务并有界关闭。共享配置、两种部署模式及验收见[部署说明](deployment.md#6-后端-web-规则-agent)。

执行总超时返回已完成窗口与实际错误，取消继续传播；尚未完成的调用不伪装成成功。无法形成可用规则时，模型可引用真实证据返回 `RuleAuthoringFailure`，理由为 `missing_evidence`、`unsupported_schema` 或 `no_usable_rule`；不会激活来源。普通文本或空回复会收到同时允许成功交接与有据失败的恢复反馈，供应商、权限和预算异常不进入此恢复循环。自动规则作者默认停用。API 的轻量配置指纹只包含配置值；Worker 的任务快照另保存 CLI binary／runner 与 Lightpanda 各自的执行身份，重试须匹配，供应商暂停比较排除这两项 Worker 本地身份。

## 必须保持的边界

- **身份与数据访问**：前端通过 API 访问 Reader 业务数据。后端使用已验证身份限定用户数据；共享来源与内容不能因某个用户调整分组、名称或取消订阅而被直接改成其私有状态。
- **内容归属与保留**：去重需要包含来源归属，收藏关系单独维护。改变删除或归并逻辑时，同时检查来源条目、收藏和翻译引用。
- **抓取与模型输出**：用户提供的来源 URL 和页面内容都属于不可信输入。抓取经过 [url_safety.py](../backend/src/app/ingestion/url_safety.py) 的公网目的地校验；规则通过 [web_rules.py](../backend/src/app/ingestion/web_rules.py) 调用实际 adapter 执行。内容是否满足目标由 Agent 结合 DOM 判断；模型不能绕过首窗执行与宿主版本／租约检查或直接写入内容。
- **Reader 凭据与第三方页面**：账户请求沿用当前 Reader runtime 的传输层；第三方文章、视频及注入脚本不接收 Reader 凭据。连接跳转和身份变化由客户端连接流程处理。
- **网页正文与本地阅读文档**：页面消息和 HTML 均不可信。正文只在当前文档身份有效且通过体积／结构检查后使用，并在应用控制的阅读文档内再次净化；导航后的旧结果不能覆盖新页面。
- **原文与翻译**：原文交付不等待外部翻译完成。成功结果、持久工作和实时在途任务分别管理；缓存身份不能只靠客户端片段 ID 或文本本身判断。

## 运行与故障边界

API 在 lifespan 内建立进程级数据库资源、认证服务、模型客户端和实时协调器，并在退出时清理。Worker 的来源扫描、候选处理、排行、清理、规则作者和翻译循环分别运行；翻译还有独立连接资源及前台／后台 lane，其失败处理不会主动终止来源扫描。

后台领取任务依靠数据库状态和租约协调。API 的实时合并、并发限制及部分限流器位于进程内；增加 API 副本时需要分别评估这些限制与数据库配额，不能把单进程限制视为全实例总限制。

`/health` 表示 API 进程存活，`/ready` 检查运行配置及数据库 schema 等就绪条件，`/worker-ready` 根据持久心跳判断 Worker 状态。定义见 [main.py](../backend/src/app/main.py) 和 [workers/health.py](../backend/src/app/workers/health.py)。可选能力的配置失败与整体服务不可用需要分别判断。

## 接口与模型入口

- **HTTP 契约**：后端路由及 Pydantic 请求／响应模型生成 [Reader API OpenAPI 快照](../contracts/openapi.json)；[export_openapi.py](../backend/scripts/export_openapi.py) 负责导出，[generate-api-types.mjs](../mobile/scripts/generate-api-types.mjs) 同步快照及 [前端类型](../mobile/lib/generated/api.ts)。接口字段以这条链路为准，更新规则见 [AGENTS.md](../AGENTS.md)，本文件不重复维护完整协议。
- **数据库结构**：[ORM 模型](../backend/src/app/storage/models.py)、[账户模型](../backend/src/app/storage/auth_models.py)、[Alembic 迁移](../backend/migrations/) 与 [schema 就绪检查](../backend/src/app/storage/schema.py) 共同定义持久化结构与部署兼容条件。
- **内部规则契约**：[WebRule](../backend/src/app/ingestion/web_rules.py) 定义可接受的抓取规则；Python 模块之间的调用约定以代码中的公开类型和函数为准。
- **架构模型**：[architecture/workspace.dsl](../architecture/workspace.dsl) 集中维护 C1 系统上下文、C2 内部运行单元与外部依赖，以及客户端／API／Worker 的 C3 组件视图，共 6 张图。组件的 `sources` 属性指向仓库内代码；C4 直接查看源码。Dynamic View 和 Deployment View 按需要再增加。

模块职责、数据归属、依赖约束或关键运行流程变化时更新本文件；命令、完整接口字段和决策历史分别更新其所属位置。
