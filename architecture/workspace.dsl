// Reader 当前实现的结构模型；职责、数据约束与运行流程见 ../docs/ARCHITECTURE.md。
// 一个 model 生成 C1、C2 和按容器展开的 C3；符号与细节以源码为准。
// sources 属性中的路径相对仓库根目录。组件表示稳定职责，不逐文件/函数建模。
// 元素、职责边界或依赖变化时同步更新；普通内部重构只需修正源码入口。
// 同一源码可在 API、Worker 中分别实例化（如翻译服务），不因此建立跨进程调用关系。
// 只画已实现的能力；可选能力标明条件，不表示线上已启用。
// Dynamic / Deployment View 按实际需要再加；生成的图和 JSON 不作为第二份模型维护。
workspace "Reader" "基于仓库代码的 C1-C3 架构模型" {
    !identifiers hierarchical
    !impliedRelationships false

    model {
        reader = person "读者" "连接 Reader 服务，订阅、浏览、收藏、阅读和翻译内容。"
        operator = person "管理员" "在服务器上用管理命令重置密码、签发 MCP 令牌、导入 Codex 登录、查看翻译用量。"
        aiHost = softwareSystem "AI 助手" "通过 /mcp 读取用户的订阅更新、榜单和公开字幕，按用户要求添加订阅；判断热点和撰写简报属于 AI。" "External"

        websites = softwareSystem "内容网站与 RSS" "提供文章、RSS/Atom 与站点图标；阅读页加载原网页。" "External"
        youtube = softwareSystem "YouTube" "提供频道（Data API 与公开 Atom）、播放器和字幕。" "External"
        x = softwareSystem "X" "Scweet 采集的上游；阅读页加载原网页。" "External"
        reddit = softwareSystem "Reddit" "subreddit 的公开 RSS：既是订阅来源，也是榜单。" "External"
        hackerNews = softwareSystem "Hacker News" "通过 Firebase API 提供榜单。" "External"
        github = softwareSystem "GitHub" "通过 Trending 页面提供榜单。" "External"
        techTwitter = softwareSystem "TechTwitter" "提供 X 技术热门榜单。" "External"
        productHunt = softwareSystem "Product Hunt" "可选官方 GraphQL API，按洛杉矶日期提供每日发布榜单。" "External,Optional"
        models = softwareSystem "模型提供方" "DeepSeek、OpenRouter 与 Codex 订阅执行翻译。" "External" {
            properties {
                "sources" "backend/src/reader/translation/engines.py"
            }
        }
        codexIdentity = softwareSystem "OpenAI 认证" "为导入的 Codex ChatGPT 登录交换新的访问／刷新令牌。" "External,Optional"

        system = softwareSystem "Reader" "聚合订阅内容与榜单，提供收藏、阅读和翻译。" {
            client = container "Reader 客户端" "Expo Android App（另有仅供开发的 Web 预览）；WebView 阅读能力为原生实现。" "Expo Router / React Native / TypeScript" {
                properties {
                    "sources" "mobile/src/app/_layout.tsx"
                }
                routes = component "路由与页面装配" "按会话状态决定可用路由；五个标签页、阅读页与设置页只做装配：读数据、持有页面状态、交给视图。" "Expo Router / React" {
                    properties {
                        "sources" "mobile/src/app/"
                    }
                }
                views = component "视图与组件" "信息流、榜单、订阅、收藏、我的等视图与共享 UI 组件，只接数据和回调。" "React Native / uniwind" {
                    properties {
                        "sources" "mobile/src/screens/; mobile/src/components/; mobile/src/ui/"
                    }
                }
                session = component "连接与会话" "服务发现、会话状态机与唯一的会话存储：恢复、登录、刷新、换发、退出与身份切换。" "TypeScript" {
                    properties {
                        "sources" "mobile/src/lib/connection/; mobile/src/lib/app-session.ts"
                    }
                }
                data = component "查询、命令与 API 客户端" "每个页面的服务端数据与命令；查询键以服务器和账户身份开头，命令自己更新缓存并在失败时回滚。" "TanStack Query / fetch" {
                    properties {
                        "sources" "mobile/src/lib/queries/; mobile/src/lib/query.ts; mobile/src/lib/api/"
                    }
                }
                logic = component "页面纯逻辑与文字" "数据整理、表单规则、页面状态机与界面文字表；可在 node --test 下运行。" "TypeScript" {
                    properties {
                        "sources" "mobile/src/lib/feed.ts; mobile/src/lib/boards.ts; mobile/src/lib/subscriptions.ts; mobile/src/lib/saved.ts; mobile/src/lib/me.ts; mobile/src/lib/reader.ts; mobile/src/lib/reader-page.ts; mobile/src/lib/i18n/"
                    }
                }
                readerHost = component "阅读页宿主" "选择原网页、阅读模式或 YouTube 播放器；校验页面消息与文档身份，调度实时翻译并回送结果。" "React Native WebView" {
                    properties {
                        "sources" "mobile/src/components/Reader.tsx; mobile/src/components/TranslatedWebView.tsx; mobile/src/components/CaptionPlayer.tsx; mobile/src/lib/web-translation-host.ts; mobile/src/lib/caption-host.ts; mobile/src/lib/translation-scheduler.ts"
                    }
                }
                pageRuntime = component "页面注入运行时" "在 WebView 文档中发现段落与字幕、提取正文、应用译文；不持有 Reader 凭据。" "TypeScript → 生成的脚本 / DOM" {
                    properties {
                        "sources" "mobile/src/lib/injected/; mobile/scripts/generate-translation-runtime-sources.mjs"
                    }
                }
            }

            localState = container "客户端本地状态" "连接、部署令牌与账户令牌在原生端的 SecureStore（Web 预览为 localStorage）；界面语言在 AsyncStorage。查询缓存只在内存，不是离线文章库。" "SecureStore / AsyncStorage / localStorage" "Database" {
                properties {
                    "sources" "mobile/src/lib/connection/storage.native.ts; mobile/src/lib/connection/storage.ts; mobile/src/lib/interface-language.ts"
                }
            }

            api = container "Reader API" "服务发现、账户、订阅、信息流、收藏、榜单、翻译请求与 /mcp。" "Python / FastAPI" {
                properties {
                    "sources" "backend/src/reader/main.py"
                }
                gate = component "入口、门禁与账户" "应用装配、部署令牌门禁、服务发现与就绪检查；注册、登录、会话与刷新令牌、资料与头像。" "FastAPI / JWT / Argon2" {
                    properties {
                        "sources" "backend/src/reader/main.py; backend/src/reader/core/server_access.py; backend/src/reader/system/; backend/src/reader/accounts/"
                    }
                }
                subscriptions = component "订阅" "订阅用例与路由；添加网站时查找 RSS／Atom，解析 YouTube 频道，中转来源图标。" "FastAPI / SQLAlchemy / HTTPX" {
                    properties {
                        "sources" "backend/src/reader/subscriptions/; backend/src/reader/ingestion/feed_discovery.py; backend/src/reader/ingestion/identity.py; backend/src/reader/ingestion/youtube.py"
                    }
                }
                reading = component "信息流与收藏" "按用户范围查询信息流、更新统计、单篇文章与收藏，附上已完成的标题译文。" "FastAPI / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/reader/reading/"
                    }
                }
                boards = component "榜单" "打开时按需抓取并缓存每个榜单的最新快照；从榜单收藏为没有来源的文章。" "FastAPI / HTTPX / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/reader/boards/"
                    }
                }
                translation = component "翻译服务" "POST /translations 与翻译偏好；缓存、领取、按全局字符额度分批调用模型、写回，最多等待片刻后返回已完成部分。" "Python / asyncio / HTTPX" {
                    properties {
                        "sources" "backend/src/reader/translation/api.py; backend/src/reader/translation/service.py; backend/src/reader/translation/engines.py; backend/src/reader/translation/budget.py; backend/src/reader/translation/codex_auth.py"
                    }
                }
                mcp = component "MCP 端点" "/mcp（Streamable HTTP）：个人令牌选定用户，工具直接调用订阅、信息流和榜单用例；读取公开 YouTube 字幕。" "MCP SDK" {
                    properties {
                        "sources" "backend/src/reader/mcp/"
                    }
                }
            }

            worker = container "Reader Worker" "来源扫描、后台标题翻译与每日清理。" "Python / asyncio" {
                properties {
                    "sources" "backend/src/reader/worker.py"
                }
                loops = component "循环与心跳" "运行三个循环，每次运行后写心跳；一个循环出错不影响其他循环。" "asyncio" {
                    properties {
                        "sources" "backend/src/reader/worker.py; backend/src/reader/system/heartbeats.py"
                    }
                }
                scan = component "来源扫描与入库" "领取到期来源的租约，经适配器读取上游，在一个事务里写入新条目、分配序号并为新标题排队翻译。" "Python / HTTPX / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/reader/ingestion/scanner.py; backend/src/reader/ingestion/adapters/; backend/src/reader/ingestion/url_safety.py"
                    }
                }
                titles = component "后台标题翻译" "领取排队的标题，按语言和引擎分组交给同一个翻译服务。" "Python / asyncio" {
                    properties {
                        "sources" "backend/src/reader/translation/titles.py; backend/src/reader/translation/service.py"
                    }
                }
                cleanup = component "每日清理" "按来源保留最新若干篇文章（更早的只保留有人收藏的）和有限扫描历史，清理久未使用的译文、无人打开的榜单快照和已结束的会话。" "Python / SQLAlchemy" {
                    properties {
                        "sources" "backend/src/reader/ingestion/retention.py; backend/src/reader/translation/titles.py"
                    }
                }
            }

            database = container "Reader 数据库" "14 张表：用户与会话、来源与订阅、文章与收藏、翻译缓存兼任务队列、榜单快照、扫描与额度状态、Worker 心跳。" "PostgreSQL" "Database" {
                properties {
                    "sources" "backend/src/reader/models.py; backend/migrations/"
                }
            }
            codexCredentials = container "Codex 凭据文件" "Reader 专用登录及刷新状态；API 与 Worker 通过文件锁协调并原子更新，Docker 卷跨容器重建保留。" "JSON / File lock / Docker volume" "Database,Optional" {
                properties {
                    "sources" "backend/src/reader/translation/codex_auth.py; compose.yaml"
                }
            }
            admin = container "Reader 管理命令" "reader-admin：重置密码、签发／撤销 MCP 令牌、导入和检查 Codex 登录、查看翻译用量。" "Python / argparse" {
                properties {
                    "sources" "backend/src/reader/cli.py"
                }
            }
            scweet = container "Scweet 配套服务" "可选：采集 X 账号；内部令牌认证，独立管理采集账号和状态。" "Python / FastAPI / Scweet" "Optional" {
                properties {
                    "sources" "services/scweet_service/app.py; services/docker-compose.scweet.yml"
                }
            }
            scweetState = container "Scweet 状态库" "保存 Scweet 采集账号与运行状态；独立于 Reader 数据库。" "SQLite" "Database,Optional" {
                properties {
                    "sources" "services/scweet_service/app.py; services/docker-compose.scweet.yml"
                }
            }
        }

        // C1：系统边界与外部依赖。
        reader -> system "订阅、浏览、收藏、阅读和翻译"
        operator -> system "运维账户、MCP 令牌与翻译"
        reader -> aiHost "请求热点简报与追问"
        aiHost -> system "通过 MCP 读取信息、添加订阅"
        system -> websites "读取 feed 与图标；加载原网页"
        system -> youtube "扫描频道，播放视频并读取字幕"
        system -> x "加载原网页；经 Scweet 采集"
        system -> reddit "读取订阅与榜单；加载原网页"
        system -> hackerNews "读取榜单"
        system -> github "读取 Trending"
        system -> techTwitter "读取 X 技术热门"
        system -> productHunt "按日期读取每日发布（需配置令牌）"
        system -> models "请求翻译"
        system -> codexIdentity "按需刷新 Codex 登录"

        // C2：运行单元与数据存储。翻译任务队列在数据库表里，不是另一套消息服务。
        reader -> system.client "操作应用" "界面交互" "ContainerRelationship"
        operator -> system.admin "执行管理命令" "CLI" "ContainerRelationship"
        aiHost -> system.api "调用 MCP 工具" "MCP / Streamable HTTP / 部署令牌 + 个人令牌" "ContainerRelationship"
        system.client -> system.api "服务发现、账户、业务与翻译请求" "HTTP(S) / JSON" "ContainerRelationship"
        system.client -> system.localState "保存连接、令牌与界面语言" "平台存储 API" "ContainerRelationship"
        system.client -> websites "加载原网页或打开外链" "HTTPS / WebView / 系统浏览器" "ContainerRelationship"
        system.client -> youtube "播放视频并读取字幕" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.client -> x "加载原网页" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.client -> reddit "加载原网页" "HTTPS / 原生 WebView" "ContainerRelationship"
        system.api -> system.database "读写业务数据、会话、快照与翻译" "SQL" "ContainerRelationship"
        system.api -> websites "添加网站时查找 feed；中转来源图标" "HTTPS" "ContainerRelationship"
        system.api -> youtube "解析频道；MCP 读取字幕" "HTTPS" "ContainerRelationship"
        system.api -> reddit "抓取 subreddit 榜单" "HTTPS / RSS" "ContainerRelationship"
        system.api -> hackerNews "抓取榜单" "HTTPS / Firebase JSON API" "ContainerRelationship"
        system.api -> github "抓取 Trending" "HTTPS / HTML" "ContainerRelationship"
        system.api -> techTwitter "抓取技术热门" "HTTPS / JSON API" "ContainerRelationship"
        system.api -> productHunt "抓取日期榜单" "HTTPS / GraphQL / Bearer" "ContainerRelationship"
        system.api -> models "翻译请求" "HTTPS / 模型 API" "ContainerRelationship"
        system.api -> system.codexCredentials "读取并按需原子更新登录" "File / Lock" "ContainerRelationship"
        system.api -> codexIdentity "按需刷新登录" "HTTPS / OAuth" "ContainerRelationship"
        system.worker -> system.database "领取来源与标题，写入文章、译文与心跳" "SQL" "ContainerRelationship"
        system.worker -> websites "读取 RSS／Atom feed" "HTTPS / RSS" "ContainerRelationship"
        system.worker -> youtube "扫描频道" "HTTPS / Data API / Atom" "ContainerRelationship"
        system.worker -> reddit "读取已订阅的 subreddit" "HTTPS / RSS" "ContainerRelationship"
        system.worker -> system.scweet "采集 X 账号" "HTTP / 内部令牌" "ContainerRelationship"
        system.worker -> models "后台标题翻译" "HTTPS / 模型 API" "ContainerRelationship"
        system.worker -> system.codexCredentials "读取并按需原子更新登录" "File / Lock" "ContainerRelationship"
        system.worker -> codexIdentity "按需刷新登录" "HTTPS / OAuth" "ContainerRelationship"
        system.admin -> system.database "重置密码、MCP 令牌、翻译用量" "SQL" "ContainerRelationship"
        system.admin -> system.codexCredentials "导入、检查和刷新专用登录" "File / Lock" "ContainerRelationship"
        system.admin -> codexIdentity "显式刷新登录" "HTTPS / OAuth" "ContainerRelationship"
        system.scweet -> x "请求上游内容" "HTTPS / GraphQL" "ContainerRelationship"
        system.scweet -> system.scweetState "管理采集账号与状态" "SQLite" "ContainerRelationship"

        // C3 客户端：页面注入运行时在 WebView 文档中执行；凭据留在应用侧。
        reader -> system.client.routes "操作应用"
        system.client.routes -> system.client.session "读取会话状态，登录、退出与切换服务器" "Hook / 函数"
        system.client.routes -> system.client.data "读取数据并执行命令" "Hooks / 函数"
        system.client.routes -> system.client.views "交给数据和回调" "React props"
        system.client.routes -> system.client.readerHost "打开阅读页" "React"
        system.client.routes -> system.client.logic "整理数据、校验表单、页面状态机" "函数"
        system.client.views -> system.client.logic "读取界面文字" "t()"
        system.client.data -> system.client.session "取当前身份与访问令牌" "函数"
        system.client.data -> system.client.logic "把 API 数据整理成卡片与行" "函数"
        system.client.readerHost -> system.client.data "读取文章、收藏与翻译偏好" "Hooks"
        system.client.readerHost -> system.client.pageRuntime "注入运行时，请求正文并回送译文" "injectJavaScript"
        system.client.pageRuntime -> system.client.readerHost "提交正文、导航、段落和字幕消息" "postMessage"
        system.client.session -> system.localState "保存连接与令牌" "SecureStore"
        system.client.logic -> system.localState "保存界面语言" "AsyncStorage"
        system.client.session -> system.api "服务发现与账户会话" "HTTP(S) / JSON"
        system.client.data -> system.api "业务请求" "HTTP(S) / JSON"
        system.client.readerHost -> system.api "实时翻译请求" "HTTP(S) / JSON"
        system.client.readerHost -> websites "加载原网页与外链" "HTTPS / WebView / 系统浏览器"
        system.client.readerHost -> youtube "加载嵌入播放器" "HTTPS / 原生 WebView"
        system.client.readerHost -> x "加载原网页" "HTTPS / 原生 WebView"
        system.client.readerHost -> reddit "加载原网页" "HTTPS / 原生 WebView"
        system.client.pageRuntime -> youtube "在页面里读取字幕轨" "HTTPS / timedtext"

        // C3 API：路由直接调用本包用例与 ORM；MCP 工具直接调用其他包的用例。
        system.client -> system.api.gate "服务发现、账户与受保护接口" "HTTP(S) / JSON"
        aiHost -> system.api.mcp "调用工具" "MCP / Streamable HTTP"
        system.api.gate -> system.database "会话、账户、头像与就绪检查" "SQL"
        system.api.subscriptions -> system.database "维护来源和订阅" "SQL"
        system.api.subscriptions -> websites "查找 feed；中转来源图标" "HTTPS"
        system.api.subscriptions -> youtube "解析频道" "HTTPS"
        system.api.reading -> system.database "查询信息流、文章与收藏" "SQL"
        system.api.boards -> system.database "读写快照；保存榜单条目" "SQL"
        system.api.boards -> reddit "抓取 subreddit 榜单" "HTTPS / RSS"
        system.api.boards -> hackerNews "抓取榜单" "HTTPS / Firebase JSON API"
        system.api.boards -> github "抓取 Trending" "HTTPS / HTML"
        system.api.boards -> techTwitter "抓取技术热门" "HTTPS / JSON API"
        system.api.boards -> productHunt "抓取日期榜单" "HTTPS / GraphQL"
        system.api.translation -> system.database "查缓存、领取、写回；预留字符额度" "SQL"
        system.api.translation -> models "调用翻译模型" "HTTPS / 模型 API"
        system.api.translation -> system.codexCredentials "锁内刷新并原子写回登录" "File / Lock"
        system.api.translation -> codexIdentity "按需续期" "HTTPS / OAuth"
        system.api.mcp -> system.api.gate "按个人令牌选定用户" "Python"
        system.api.mcp -> system.api.subscriptions "添加订阅" "Python"
        system.api.mcp -> system.api.reading "更新统计、更新列表与单条内容" "Python"
        system.api.mcp -> system.api.boards "读取榜单" "Python"
        system.api.mcp -> youtube "读取公开字幕，不翻译" "HTTPS"

        // C3 Worker：三个循环各自运行，经数据库协调；不画 API→Worker 的同步调用。
        system.worker.loops -> system.worker.scan "运行扫描循环" "asyncio"
        system.worker.loops -> system.worker.titles "运行标题翻译循环" "asyncio"
        system.worker.loops -> system.worker.cleanup "每日运行清理" "asyncio"
        system.worker.loops -> system.database "记录心跳" "SQL"
        system.worker.scan -> system.database "领取来源租约，写入文章并排队标题" "SQL"
        system.worker.scan -> websites "读取 RSS／Atom feed" "HTTPS / RSS"
        system.worker.scan -> youtube "扫描频道" "HTTPS / Data API / Atom"
        system.worker.scan -> reddit "读取 subreddit" "HTTPS / RSS"
        system.worker.scan -> system.scweet "采集 X 账号" "HTTP / 内部令牌"
        system.worker.titles -> system.database "领取排队标题并写回译文" "SQL"
        system.worker.titles -> models "批量翻译标题" "HTTPS / 模型 API"
        system.worker.titles -> system.codexCredentials "锁内刷新并原子写回登录" "File / Lock"
        system.worker.titles -> codexIdentity "按需续期" "HTTPS / OAuth"
        system.worker.cleanup -> system.database "删除过期数据，保护收藏" "SQL"
    }

    views {
        systemContext system "C1-SystemContext" {
            title "C1 · Reader 系统上下文"
            include *
            autoLayout lr 350 80
        }
        container system "C2-Containers" {
            title "C2 · Reader 运行单元与数据存储"
            include reader operator aiHost system.client system.localState system.api system.worker system.database system.codexCredentials system.admin system.scweet system.scweetState
            autoLayout lr 350 80
        }
        container system "C2-ExternalDependencies" {
            title "C2 · Reader 外部依赖"
            include system.client system.api system.worker system.scweet aiHost websites youtube x reddit hackerNews github techTwitter productHunt models codexIdentity
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
                height 300
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
