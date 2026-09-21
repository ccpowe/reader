// Reader 当前实现的结构模型；职责、数据约束与运行流程见 ../ARCHITECTURE.md。
// 一个 model 生成 C1、C2 和按容器展开的 C3；C4 直接查看代码。
// sources 属性中的路径相对仓库根目录。组件表示稳定职责，不逐文件/函数建模。
// 元素、职责边界或依赖变化时同步更新；普通内部重构只需修正源码入口。
// 同一源码可在 API、Worker 中分别实例化，不因此建立跨进程调用关系。
// 只画已实现的能力；可选能力标明条件，不表示线上已启用。
// Dynamic / Deployment View 按实际需要再加；生成的图和 JSON 不作为第二份模型维护。
workspace "Reader" "基于仓库代码的 C1-C3 架构模型" {
    !identifiers hierarchical
    !impliedRelationships false

    model {
        reader = person "读者" "连接 Reader 服务，订阅、浏览、收藏、阅读和翻译内容。"
        operator = person "管理员" "通过管理命令维护用户、翻译配额和网页规则任务。"

        websites = softwareSystem "内容网站与 RSS" "提供文章、RSS/Atom、来源头像和可提取网页。" "External"
        youtube = softwareSystem "YouTube" "提供频道、视频、播放器及字幕。" "External"
        x = softwareSystem "X" "Scweet 采集的上游社交平台，也提供客户端阅读的原网页。" "External"
        apify = softwareSystem "Apify" "选择 Apify 时提供 X 内容采集结果。" "External,Optional"
        reddit = softwareSystem "Reddit" "提供 subreddit 榜单 RSS；hot 快照也驱动已订阅内容入库。" "External"
        hackerNews = softwareSystem "Hacker News" "通过 Firebase API 提供榜单与条目。" "External"
        github = softwareSystem "GitHub" "通过 Trending 页面提供排行。" "External"
        models = softwareSystem "模型提供方" "按配置选择 DeepSeek 或 OpenRouter，执行翻译及可选网页规则生成。" "External" {
            properties {
                "sources" "backend/src/app/llm/factory.py"
            }
        }

        system = softwareSystem "Reader" "聚合订阅内容与排行，提供个人收藏、阅读和翻译。" {
            client = container "Reader 客户端" "Expo 原生应用，含同源代码的 Web 预览入口；WebView 阅读能力为原生实现。" "Expo / React Native / TypeScript" {
                properties {
                    "sources" "mobile/index.ts; mobile/App.tsx"
                }
                shell = component "应用壳与导航" "组织连接、登录和业务页面；服务器或用户变化时重建页面与查询上下文。" "React" {
                    properties {
                        "sources" "mobile/App.tsx; mobile/components/MainAppShell.tsx; mobile/domain/mainNavigation.ts"
                    }
                }
                screens = component "内容与账户界面" "展示信息流、排行、订阅、收藏与个人资料，处理用户操作。" "React Native" {
                    properties {
                        "sources" "mobile/screens/HomeScreen.tsx; mobile/screens/RankingsScreen.tsx; mobile/screens/SourcesScreen.tsx; mobile/screens/SavedScreen.tsx; mobile/screens/ProfileScreen.tsx; mobile/screens/EmailAuthScreen.tsx"
                    }
                }
                connection = component "连接与会话" "服务发现、runtime 切换、会话恢复和刷新；隔离服务器身份及过期请求。" "TypeScript" {
                    properties {
                        "sources" "mobile/lib/connection/; mobile/lib/readerAuth.ts"
                    }
                }
                data = component "查询与 API 客户端" "组织业务 HTTP 请求及查询缓存；携带当前身份，校验响应所属连接。" "TanStack Query / fetch" {
                    properties {
                        "sources" "mobile/lib/api.ts; mobile/lib/readerTransport.ts; mobile/lib/readerTransport.native.ts; mobile/state/; mobile/hooks/useInboxFeed.ts; mobile/hooks/useSources.ts; mobile/hooks/useSavedContent.ts; mobile/hooks/useRanking.ts; mobile/hooks/useTranslationPreference.ts"
                    }
                }
                reading = component "阅读宿主与桥接" "Web/RSS 默认原网页；按需展示本地阅读文档，校验提取、导航与媒体消息及当前页身份。" "React Native WebView" {
                    properties {
                        "sources" "mobile/screens/ArticleReaderScreen.tsx; mobile/screens/RankingPreviewScreen.tsx; mobile/components/TranslatableWebView.tsx; mobile/domain/webReader.ts"
                    }
                }
                readerContent = component "正文提取与阅读文档" "对当前 DOM 副本提取正文，在应用阅读文档中再次净化；结果仅供当前阅读，不写入 Content。" "Readability / DOMPurify / DOM" {
                    properties {
                        "sources" "mobile/domain/webReaderExtraction.ts; mobile/domain/readerHtml.ts"
                    }
                }
                pageRuntime = component "页面注入运行时" "在 WebView 文档中发现段落与字幕并应用译文；不持有 Reader 凭据。" "JavaScript / DOM" {
                    properties {
                        "sources" "mobile/domain/webTranslation.ts; mobile/domain/webTranslationRuntime.ts; mobile/domain/youtubeTranslation.ts; mobile/scripts/generate-translation-runtime-sources.mjs"
                    }
                }
                translation = component "翻译调度与结果收敛" "安排实时、普通片段与标题请求，批处理、重试并丢弃过期结果。" "React Hooks / TypeScript" {
                    properties {
                        "sources" "mobile/hooks/useRealtimeTranslationScheduler.ts; mobile/hooks/useSegmentTranslationQueue.ts; mobile/hooks/useTitleTranslationConvergence.ts"
                    }
                }
                language = component "界面语言" "管理客户端界面语言；与服务端翻译偏好独立。" "React / i18next" {
                    properties {
                        "sources" "mobile/i18n/"
                    }
                }
            }

            localState = container "客户端本地状态" "连接坐标、界面语言及隔离的会话凭据；查询缓存另在内存中，不是离线文章库。" "SecureStore / localStorage / sessionStorage" "Database" {
                properties {
                    "sources" "mobile/lib/connection/storage.ts; mobile/lib/connection/secretStorage.native.ts; mobile/lib/connection/secretStorage.ts; mobile/i18n/store.ts; mobile/App.tsx"
                    "platforms" "原生公开数据使用 Expo SQLite localStorage 适配、凭据使用 SecureStore；Web 凭据使用 sessionStorage/内存。"
                }
            }

            api = container "Reader API" "服务发现、认证和用户业务；直接处理交互翻译，也建立持久工作。" "Python / FastAPI" {
                properties {
                    "sources" "backend/src/app/main.py; backend/src/app/api/router.py"
                    "persistence" "组件可直接使用 storage/ 中的 SQLAlchemy Session/ORM，不存在强制经过的统一 Repository。"
                }
                entry = component "入口、发现与认证" "HTTP 路由装配、就绪检查、部署访问令牌、用户会话与权限依赖。" "FastAPI / JWT" {
                    properties {
                        "sources" "backend/src/app/main.py; backend/src/app/api/router.py; backend/src/app/api/discovery.py; backend/src/app/api/auth.py; backend/src/app/core/server_access.py; backend/src/app/core/auth.py; backend/src/app/services/auth.py"
                    }
                }
                sources = component "来源与订阅" "管理来源和用户订阅，解析 YouTube 频道并代理来源头像。" "FastAPI / SQLAlchemy / HTTPX" {
                    properties {
                        "sources" "backend/src/app/api/sources.py; backend/src/app/services/subscriptions.py; backend/src/app/ingestion/source_identity.py; backend/src/app/ingestion/youtube.py"
                    }
                }
                content = component "信息流、收藏与资料" "按用户范围查询内容、维护收藏与阅读状态，管理个人资料和头像。" "FastAPI / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/api/feed.py; backend/src/app/api/saved.py; backend/src/app/api/profile.py; backend/src/app/api/avatars.py; backend/src/app/services/ranking_saved.py"
                    }
                }
                rankings = component "排行快照" "读取或刷新榜单；发布适用 Reddit hot 快照时写入持久候选。" "Python / HTTPX / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/api/rankings.py; backend/src/app/services/ranking_snapshots.py; backend/src/app/services/rankings.py"
                    }
                }
                translationPolicy = component "翻译请求与策略" "校验文本、访问范围、用户偏好与配额，分派标题及片段翻译路径。" "FastAPI / Python" {
                    properties {
                        "sources" "backend/src/app/api/translations.py; backend/src/app/api/translation_preferences.py; backend/src/app/translation/quota.py"
                    }
                }
                interactive = component "直接与实时翻译" "在 API 进程内执行模型请求、合并实时在途需求；普通片段可回退到持久工作。" "asyncio / LangChain" {
                    properties {
                        "sources" "backend/src/app/translation/interactive.py; backend/src/app/translation/realtime.py; backend/src/app/translation/providers/langchain.py; backend/src/app/translation/factory.py; backend/src/app/llm/factory.py"
                    }
                }
                translationState = component "翻译需求与持久状态" "投影成功结果、建立缺失 Work、保存 Artifact，并通知空闲消费者。" "SQLAlchemy / PostgreSQL" {
                    properties {
                        "sources" "backend/src/app/translation/projection.py; backend/src/app/translation/demand.py; backend/src/app/translation/store.py; backend/src/app/translation/notifier.py"
                    }
                }
            }

            worker = container "Reader Worker" "执行来源扫描、候选入库、排行刷新、持久翻译、规则任务与清理。" "Python / asyncio" {
                properties {
                    "sources" "backend/src/app/workers/main.py"
                    "persistence" "与 API 共用 storage/、业务源码和 PostgreSQL；各自拥有运行时实例，不通过 RPC 互相调度。"
                }
                loops = component "循环调度与健康状态" "运行并监督各后台循环，记录持久心跳；翻译使用独立数据库资源。" "asyncio" {
                    properties {
                        "sources" "backend/src/app/workers/main.py; backend/src/app/workers/health.py"
                    }
                }
                scan = component "来源扫描与接入" "领取来源租约，选择 RSS/Web/YouTube/X adapter，发现 feed 或网页列表并写入候选；不逐篇补抓网页正文。" "Python / HTTPX / Crawl4AI" {
                    properties {
                        "sources" "backend/src/app/workers/sync.py; backend/src/app/ingestion/sources/; backend/src/app/ingestion/feed_discovery.py; backend/src/app/services/web_feeds.py; backend/src/app/ingestion/web_crawl.py; backend/src/app/ingestion/url_safety.py"
                    }
                }
                candidates = component "候选入库" "领取持久候选，去重并写入内容、来源条目与媒体；按配置建立标题翻译 Work。" "Python / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/workers/candidates.py; backend/src/app/ingestion/candidate_queue.py; backend/src/app/translation/store.py"
                    }
                }
                rankings = component "排行刷新" "定期刷新榜单快照；适用 Reddit hot 条目进入同一持久候选队列。" "Python / HTTPX / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/services/ranking_snapshots.py; backend/src/app/services/rankings.py"
                    }
                }
                translation = component "持久翻译执行" "前台/后台 lane 领取 Work、批量调用模型并保存结果；通知唤醒并辅以轮询。" "asyncio / SQLAlchemy / LangChain" {
                    properties {
                        "sources" "backend/src/app/translation/executor.py; backend/src/app/translation/store.py; backend/src/app/translation/notifier.py; backend/src/app/translation/providers/langchain.py; backend/src/app/llm/factory.py"
                    }
                }
                ruleJobs = component "网页规则任务与激活" "持久管理租约、预算、提示性检查点与真实执行报告，核验首窗执行、候选凭据和来源版本后激活规则。" "Python / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/workers/web_rules.py; backend/src/app/services/web_rule_jobs.py; backend/src/app/services/web_rules.py"
                    }
                }
                ruleAgent = component "网页规则 Agent" "自动作者默认停用；启用后保存有界证据，根据 DOM 和真实执行结果评估、修正列表规则并交接。" "LangChain / Crawl4AI" "Optional" {
                    properties {
                        "sources" "backend/src/app/web_rule_agent/runtime.py; backend/src/app/web_rule_agent/context.py; backend/src/app/web_rule_agent/tools.py; backend/src/app/web_rule_agent/memory.py; backend/src/app/web_rule_agent/store.py; backend/src/app/ingestion/web_rules.py; backend/src/app/llm/factory.py"
                    }
                }
                ruleBrowser = component "受控网页执行客户端" "本机模式直接管理受隔离的 Lightpanda；容器模式申请 explore/render 任务并通过类型化内部接口执行，静态规则仍走 HTTP。" "Python / HTTPX / Crawl4AI" {
                    properties {
                        "sources" "backend/src/app/browser_tasks/client.py; backend/src/app/web_rule_agent/cli_browser.py; backend/src/app/web_rule_agent/cli_process.py; backend/src/app/web_rule_agent/cli_bridge.py; backend/src/app/ingestion/browser_runtime.py; backend/src/app/ingestion/web_crawl.py; backend/src/app/ingestion/url_safety.py"
                    }
                }
                cleanup = component "保留与清理" "清理孤立来源、过量未收藏内容和过期翻译，保护收藏及仍有效的引用。" "Python / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/app/workers/cleanup.py; backend/src/app/workers/content_retention.py; backend/src/app/translation/lifecycle.py"
                    }
                }
            }

            browserManager = container "浏览器任务管理器" "可选容器部署的可信控制面；核验固定镜像身份，持久化任务状态并创建、续租、关闭任务资源。" "Python / FastAPI / Docker Engine API" "Optional" {
                properties {
                    "sources" "backend/src/app/browser_tasks/manager_app.py; backend/src/app/browser_tasks/controller.py; backend/src/app/browser_tasks/docker_runtime.py; backend/src/app/browser_tasks/state.py"
                    "deployment" "仅控制网络；持有 Docker socket、控制令牌与 capability HMAC key，并与 Reaper 共享状态目录。"
                }
            }
            browserReaper = container "浏览器任务回收器" "独立监督过期租约、硬期限和残留资源；原子领取清理权后回收本部署的两个任务容器与任务控制卷。" "Python / Docker Engine API" "Optional" {
                properties {
                    "sources" "backend/src/app/browser_tasks/reaper.py; backend/src/app/browser_tasks/docker_runtime.py; backend/src/app/browser_tasks/state.py"
                    "deployment" "没有控制或 capability 密钥；持有 Docker socket并与 Manager 共享任务状态目录。"
                }
            }
            browserState = container "浏览器任务状态" "保存请求幂等、nonce、租约、硬期限、资源身份与清理状态；Manager 和 Reaper 通过原子状态转换协调。" "SQLite" "Database,Optional" {
                properties {
                    "sources" "backend/src/app/browser_tasks/state.py"
                }
            }
            browserAdapter = container "浏览器任务 Adapter" "每任务短期数据面；校验 task capability，通过 safe_get/robots/预算获取公网页面，并经专属 Unix socket 驱动浏览器。" "Python / FastAPI / Crawl4AI" "Optional,Ephemeral" {
                properties {
                    "sources" "backend/src/app/browser_tasks/adapter_app.py; backend/src/app/browser_tasks/adapter_runtime.py; backend/src/app/browser_tasks/protocol.py"
                    "deployment" "仅任务 data 与受控 egress 网络；没有 Docker socket、Manager 令牌、数据库或模型凭据。"
                }
            }
            browserSandbox = container "无网络浏览器任务" "每任务运行固定版 Lightpanda 与 agent-browser；只通过任务 Unix socket 接收 adapter 控制，不能发起 IP 网络连接。" "Lightpanda 0.4.0 / agent-browser 0.37.1" "Optional,Ephemeral" {
                properties {
                    "sources" "services/browser_runtime/Dockerfile.browser; services/browser_runtime/browser-entrypoint.sh; services/browser_runtime/agent-browser.json"
                    "deployment" "network none、非 root、只读文件系统、cap_drop ALL、no-new-privileges；不挂载 Reader 源码或凭据。"
                }
            }

            database = container "Reader 数据库" "业务与用户数据、翻译 Artifact/Work、候选、规则任务、租约、配额及心跳。" "PostgreSQL" "Database" {
                properties {
                    "sources" "backend/src/app/storage/; backend/migrations/; backend/src/app/translation/notifier.py"
                }
            }
            admin = container "Reader 管理 CLI" "按需运行，管理用户、翻译配额和网页规则任务；直接访问数据库。" "Python / argparse" {
                properties {
                    "sources" "backend/src/app/admin/cli.py; backend/pyproject.toml"
                }
            }
            scweet = container "Scweet 配套服务" "仅选择 Scweet 作为 X 供应商时使用；内部令牌认证，独立管理采集账号和状态。" "Python / FastAPI / Scweet" "Optional" {
                properties {
                    "sources" "services/scweet_service/app.py; services/docker-compose.scweet.yml"
                }
            }
            scweetState = container "Scweet 状态库" "保存 Scweet 采集账号与运行状态；独立于 Reader 业务数据库。" "SQLite" "Database,Optional" {
                properties {
                    "sources" "services/scweet_service/app.py; services/scweet_service/Dockerfile; services/docker-compose.scweet.yml"
                }
            }
        }

        // C1：系统边界与外部依赖。只描述 Reader 实际使用的外部能力。
        reader -> system "订阅、浏览、收藏、阅读和翻译"
        operator -> system "维护用户、配额和规则任务"
        system -> websites "获取内容、feed 与头像；加载阅读页面"
        system -> youtube "获取频道内容，播放视频并读取字幕"
        system -> x "加载原网页；选择 Scweet 时采集内容"
        system -> apify "获取 X 采集结果（选择 Apify 时）"
        system -> reddit "获取榜单及订阅热门内容，加载原网页"
        system -> hackerNews "获取榜单条目"
        system -> github "获取 Trending 排行"
        system -> models "请求翻译；按配置生成网页规则"

        // C2：运行单元与数据存储。数据库工作队列不是另一套消息服务。
        reader -> system.client "操作应用" "界面交互" "ContainerRelationship"
        operator -> system.admin "执行管理命令" "CLI" "ContainerRelationship"
        system.client -> system.api "服务发现、账户与业务请求" "HTTP(S) / JSON" "ContainerRelationship"
        system.client -> system.localState "保存连接、语言及隔离的凭据" "平台存储 API" "ContainerRelationship"
        system.client -> websites "加载原网页或打开外链" "HTTPS / WebView / 系统浏览器" "ContainerRelationship"
        system.client -> youtube "加载视频与字幕" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.client -> x "加载原网页" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.client -> reddit "加载原网页" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.api -> system.database "读写业务、会话、配额及翻译状态，发布通知" "SQL / NOTIFY" "ContainerRelationship"
        system.api -> websites "代理来源头像" "HTTPS" "ContainerRelationship"
        system.api -> youtube "解析频道" "HTTPS" "ContainerRelationship"
        system.api -> reddit "刷新榜单" "HTTPS / RSS" "ContainerRelationship"
        system.api -> hackerNews "刷新榜单" "HTTPS / Firebase JSON API" "ContainerRelationship"
        system.api -> github "刷新榜单" "HTTPS / HTML" "ContainerRelationship"
        system.api -> models "直接和实时翻译" "HTTPS / 模型 API" "ContainerRelationship"
        system.worker -> system.database "领取任务、读写内容和结果，监听通知" "SQL / LISTEN / NOTIFY" "ContainerRelationship"
        system.worker -> websites "抓取 feed、内容及规则验证页面" "HTTPS / RSS / HTML" "ContainerRelationship"
        system.worker -> youtube "扫描频道" "HTTPS / Data API / 回退抓取" "ContainerRelationship"
        system.worker -> system.scweet "采集 X（x_provider=scweet）" "HTTP / 内部令牌" "ContainerRelationship"
        system.worker -> apify "采集 X（x_provider=apify）" "HTTPS / API" "ContainerRelationship"
        system.worker -> reddit "刷新榜单并产生订阅候选" "HTTPS / RSS" "ContainerRelationship"
        system.worker -> hackerNews "刷新榜单" "HTTPS / Firebase JSON API" "ContainerRelationship"
        system.worker -> github "刷新榜单" "HTTPS / HTML" "ContainerRelationship"
        system.worker -> models "持久翻译；启用时编写网页规则" "HTTPS / 模型 API" "ContainerRelationship"
        system.worker -> system.browserManager "申请、续租和关闭浏览器任务（容器模式）" "内部 HTTP / Bearer" "ContainerRelationship"
        system.worker -> system.browserAdapter "执行 explore CLI 或一次性 render（容器模式）" "内部 HTTP / Task capability" "ContainerRelationship"
        system.browserManager -> system.browserAdapter "创建、核验健康并回收任务容器" "Docker Engine" "ContainerRelationship"
        system.browserManager -> system.browserSandbox "创建、核验健康并回收无网络浏览器" "Docker Engine" "ContainerRelationship"
        system.browserManager -> system.browserState "记录创建、租约、幂等与清理状态" "SQLite" "ContainerRelationship"
        system.browserReaper -> system.browserState "原子领取过期任务并记录清理结果" "SQLite" "ContainerRelationship"
        system.browserReaper -> system.browserAdapter "回收过期或残留 adapter" "Docker Engine" "ContainerRelationship"
        system.browserReaper -> system.browserSandbox "回收过期或残留 browser 与任务控制卷" "Docker Engine" "ContainerRelationship"
        system.browserAdapter -> system.browserSandbox "经任务专属 Unix socket 提供 CDP 与 CLI 控制" "Unix sockets" "ContainerRelationship"
        system.browserAdapter -> websites "以受控公网传输获取页面" "HTTPS / safe_get" "ContainerRelationship"
        system.admin -> system.database "管理用户、配额及规则任务" "SQL" "ContainerRelationship"
        system.scweet -> x "请求上游内容" "HTTPS / GraphQL" "ContainerRelationship"
        system.scweet -> system.scweetState "管理采集账号与状态" "SQLite" "ContainerRelationship"

        // C3 客户端：页面注入运行时在 WebView 文档中执行；凭据留在应用侧。
        reader -> system.client.shell "操作应用"
        system.client.shell -> system.client.connection "恢复连接与会话" "函数 / 状态订阅"
        system.client.shell -> system.client.data "提供并清理查询上下文" "React Provider"
        system.client.shell -> system.client.screens "挂载业务页面" "React"
        system.client.shell -> system.client.reading "打开阅读界面" "React"
        system.client.shell -> system.client.language "初始化并监听界面语言" "React / 生命周期"
        system.client.screens -> system.client.data "查询和修改业务数据" "Hooks / 函数"
        system.client.screens -> system.client.connection "登录、登出及切换服务" "函数"
        system.client.data -> system.client.connection "获取身份，刷新会话并校验请求代次" "函数"
        system.client.data -> system.client.translation "收敛标题和榜单译文" "Hooks"
        system.client.translation -> system.client.data "提交翻译请求与查询结果" "API 函数"
        system.client.reading -> system.client.data "读取文章、收藏及翻译偏好" "Hooks / 函数"
        system.client.reading -> system.client.translation "安排可见片段与字幕翻译" "Hook"
        system.client.reading -> system.client.pageRuntime "注入运行时，请求正文并应用译文" "injectJavaScript"
        system.client.reading -> system.client.readerContent "构造应用控制的空阅读文档" "TypeScript"
        system.client.pageRuntime -> system.client.readerContent "提取原文副本；阅读文档内净化后挂载" "DOM / 函数"
        system.client.pageRuntime -> system.client.reading "提交有界正文、导航、段落和字幕消息" "postMessage"
        system.client.connection -> system.localState "保存连接坐标与隔离的凭据" "平台存储 API"
        system.client.language -> system.localState "保存界面语言" "localStorage"
        system.client.connection -> system.api "发现服务并管理账户会话" "HTTP(S) / JSON"
        system.client.data -> system.api "请求业务与翻译接口" "HTTP(S) / JSON"
        system.client.reading -> websites "加载原文与外链" "HTTPS / WebView / 系统浏览器"
        system.client.reading -> youtube "加载视频页面" "HTTPS / 原生 WebView"
        system.client.reading -> x "加载原网页" "HTTPS / 原生 WebView"
        system.client.reading -> reddit "加载原网页" "HTTPS / 原生 WebView"
        system.client.pageRuntime -> youtube "在页面上下文读取字幕" "HTTPS / timedtext"

        // C3 API：路由及服务直接使用 ORM；翻译代码在本进程内执行。
        system.client -> system.api.entry "请求服务发现与受保护接口" "HTTP(S) / JSON"
        system.api.entry -> system.api.sources "路由与认证依赖" "ASGI / Python"
        system.api.entry -> system.api.content "路由与认证依赖" "ASGI / Python"
        system.api.entry -> system.api.rankings "路由与认证依赖" "ASGI / Python"
        system.api.entry -> system.api.translationPolicy "路由与认证依赖" "ASGI / Python"
        system.api.entry -> system.database "验证会话、维护账户并检查就绪状态" "SQL"
        system.api.sources -> system.database "维护来源和用户订阅" "SQL"
        system.api.sources -> websites "代理来源头像" "HTTPS"
        system.api.sources -> youtube "解析频道标识" "HTTPS"
        system.api.content -> system.database "查询内容、维护收藏和个人资料" "SQL"
        system.api.content -> system.api.translationState "投影标题译文并建立缺失需求" "Python / async"
        system.api.rankings -> system.database "读取/发布快照及 Reddit hot 候选" "SQL"
        system.api.rankings -> reddit "获取 subreddit 榜单" "HTTPS / RSS"
        system.api.rankings -> hackerNews "获取榜单和条目" "HTTPS / Firebase JSON API"
        system.api.rankings -> github "获取 Trending" "HTTPS / HTML"
        system.api.translationPolicy -> system.database "校验内容访问、偏好及配额" "SQL"
        system.api.translationPolicy -> system.api.translationState "解析标题需求及缓存投影" "Python"
        system.api.translationPolicy -> system.api.interactive "分派普通或实时片段" "Python / async"
        system.api.interactive -> system.api.translationState "读写结果；适用时建立持久工作" "Python / async"
        system.api.interactive -> models "调用配置的翻译模型" "HTTPS / 模型 API"
        system.api.translationState -> system.database "读写 Artifact/Work 并唤醒消费者" "SQL / NOTIFY"

        // C3 Worker：扫描和排行通过持久候选交接入库，API 同样可写入这些状态。
        // 不画扫描器→候选执行器或 API→Worker 的同步调用。
        system.worker.loops -> system.worker.scan "运行扫描循环" "asyncio"
        system.worker.loops -> system.worker.candidates "运行候选处理循环" "asyncio"
        system.worker.loops -> system.worker.rankings "运行排行刷新循环" "asyncio"
        system.worker.loops -> system.worker.translation "监督前台/后台翻译 lane" "asyncio"
        system.worker.loops -> system.worker.ruleJobs "运行规则任务循环" "asyncio"
        system.worker.loops -> system.worker.cleanup "运行清理循环" "asyncio"
        system.worker.loops -> system.database "记录 Worker 心跳" "SQL"
        system.worker.scan -> system.database "领取来源租约，读规则并写入候选/规则任务" "SQL"
        system.worker.scan -> system.worker.ruleBrowser "执行网页规则，共用动态浏览器与传输限制" "Python / async"
        system.worker.scan -> websites "发现并抓取 feed，按有效规则提取页面" "HTTPS / RSS / HTML"
        system.worker.scan -> youtube "扫描频道内容" "HTTPS / Data API / 回退抓取"
        system.worker.scan -> system.scweet "采集 X（选择 Scweet 时）" "HTTP / 内部令牌"
        system.worker.scan -> apify "采集 X（选择 Apify 时）" "HTTPS / API"
        system.worker.candidates -> system.database "领取候选并入库；按配置建立标题 Work" "SQL / NOTIFY"
        system.worker.rankings -> system.database "发布快照及适用 Reddit hot 候选" "SQL"
        system.worker.rankings -> reddit "获取 subreddit 榜单" "HTTPS / RSS"
        system.worker.rankings -> hackerNews "获取榜单和条目" "HTTPS / Firebase JSON API"
        system.worker.rankings -> github "获取 Trending" "HTTPS / HTML"
        system.worker.translation -> system.database "领取 Work、保存 Artifact、等待通知" "SQL / LISTEN / NOTIFY"
        system.worker.translation -> models "执行持久翻译" "HTTPS / 模型 API"
        system.worker.ruleJobs -> system.database "管理任务、预算、租约并激活有效规则" "SQL"
        system.worker.ruleJobs -> websites "预检旧规则是否已恢复" "HTTPS / 抓取与验证"
        system.worker.ruleJobs -> system.worker.ruleAgent "启用时编写并验证候选" "Python / async"
        system.worker.ruleAgent -> system.worker.ruleJobs "记录预算、发现、执行事实与模型判断及凭据，提交激活交接" "Python / JobStore"
        system.worker.ruleAgent -> system.worker.ruleBrowser "执行单条 CLI 命令并读取有界真实结果" "Python / async"
        system.worker.ruleBrowser -> websites "安全传输获取页面并执行有界动作" "HTTPS / safe_get / Crawl4AI"
        system.worker.ruleBrowser -> system.browserManager "申请、续租和关闭任务（容器模式）" "内部 HTTP / Bearer"
        system.worker.ruleBrowser -> system.browserAdapter "执行类型化 CLI/render 请求（容器模式）" "内部 HTTP / Task capability"
        system.worker.ruleAgent -> websites "用真实执行器取得列表及续读事实" "HTTPS / Crawl4AI / Lightpanda（动态）"
        system.worker.ruleAgent -> models "生成结构化候选规则（启用时）" "HTTPS / 模型 API"
        system.worker.cleanup -> system.database "清理孤立状态并保护有效引用" "SQL"
    }

    views {
        systemContext system "C1-SystemContext" {
            title "C1 · Reader 系统上下文"
            include *
            autoLayout lr 350 80
        }
        container system "C2-Containers" {
            title "C2 · Reader 运行单元与数据存储"
            include reader operator system.client system.localState system.api system.worker system.browserManager system.browserReaper system.browserState system.browserAdapter system.browserSandbox system.database system.admin system.scweet system.scweetState
            autoLayout lr 350 80
        }
        container system "C2-ExternalDependencies" {
            title "C2 · Reader 外部依赖"
            include system.client system.api system.worker system.browserManager system.browserReaper system.browserAdapter system.browserSandbox system.scweet websites youtube x apify reddit hackerNews github models
            autoLayout lr 350 80
        }
        component system.client "C3-Client" {
            title "C3 · Reader 客户端"
            include *
            exclude "relationship.tag==ContainerRelationship"
            autoLayout lr 350 80
        }
        component system.api "C3-API" {
            title "C3 · Reader API"
            include *
            exclude "relationship.tag==ContainerRelationship"
            autoLayout lr 350 80
        }
        component system.worker "C3-Worker" {
            title "C3 · Reader Worker"
            include *
            exclude "relationship.tag==ContainerRelationship"
            autoLayout lr 350 80
        }

        styles {
            element "Element" {
                color #ffffff
                background #2563a6
                shape RoundedBox
                width 420
                height 240
                fontSize 26
            }
            element "Person" {
                background #173b63
                shape Person
            }
            element "Software System" {
                background #173b63
            }
            element "Component" {
                background #dceafa
                color #173b63
            }
            element "External" {
                background #64748b
            }
            element "Database" {
                shape Cylinder
            }
            element "Optional" {
                border Dashed
            }
            relationship "Relationship" {
                color #64748b
                routing Direct
                fontSize 24
                width 240
                dashed false
            }
        }
    }
}
