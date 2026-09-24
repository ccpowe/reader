# Reader 部署指南

本文是 Reader 的可执行部署手册。它既面向维护者，也面向按步骤操作的 AI Agent。
除非明确标记为“可选”，否则完成前不得宣布部署成功。

## 1. 部署边界与顺序

### 第一步：先确认部署范围

部署 Agent 不得直接按“最小可运行”配置开始安装，也不得自行假定用户只需要后端。
开始修改系统、安装依赖或创建服务前，先向用户说明下面的选项和缺少依赖时的影响，
等待用户确认范围。只询问选择，不要求用户把 key、Cookie、token 或密码发到聊天中。

可直接使用以下问题模板：

```text
开始部署前，请确认：
1. 部署目标是什么系统？服务通过 HTTP 仅供本机使用、通过 HTTP 供同一局域网设备连接，还是通过域名和 HTTPS 公网访问？
2. 需要哪些体验入口：仅后端、Web 开发预览、安装现有 Android Release APK，还是本地构建 Android APK？
3. 翻译选择 DeepSeek、OpenRouter、Codex 订阅，还是暂不启用？
4. 是否启用 X 内容同步、YouTube Data API 和无 RSS 网站的 Web 规则自动生成？

我会根据你的选择列出需要在本机填写的配置位置；请不要在聊天中发送任何密钥或 Cookie。
```

部署范围及依赖必须逐项确认：

| 能力／入口 | 用户需要选择或提供的资源 | 不启用或不配置时的结果 |
| --- | --- | --- |
| 访问范围 | 本机 HTTP、局域网 HTTP 或公网 HTTPS；公网部署还需域名、证书和反向代理 | 只监听本机时，手机和其他设备无法连接；本机和受信局域网不要求额外配置 HTTPS |
| Web 预览 | 是否启动 `mobile/` 的 Expo Web 开发预览 | 不影响后端和 Android；Web 只是开发预览，不能替代依赖原生 WebView 的 Android 体验 |
| Android | 下载现有 [Release APK](https://github.com/ccpowe/reader/releases)，或用 EAS 重新构建 | 不影响 Web 和后端；未安装客户端时只能通过 API 或 Web 预览验收 |
| 翻译 | 选择 DeepSeek、OpenRouter 或 Codex 订阅，在 `backend/.env` 配置对应凭据及默认引擎 | 翻译不可用，翻译循环失败且 `/worker-ready` 不能通过；只能交付为用户明确接受的受限部署 |
| X 内容 | 专用 X 账号的 `auth_token` Cookie、私有 Scweet 服务及服务间 token | 只有 X 同步不可用，其他来源、阅读和翻译不受影响 |
| YouTube | 可选的 YouTube Data API v3 key | 回退到公开 Atom feed，仍可订阅公开频道，但数据完整性降低 |
| Web 规则自动生成 | 模型 key、Linux、bubblewrap、Lightpanda 0.4.0 和 agent-browser 0.37.1 | 保持关闭；静态 HTTP、RSS 发现和已保存规则仍可工作，需要新规则的无 RSS／动态网站不能自动完成接入 |

用户确认后，Agent 先从 `backend/.env.example` 创建 `backend/.env`，再按所选能力明确告知
用户应填写的字段：DeepSeek 使用 `APP_DEEPSEEK_API_KEY`；OpenRouter 使用
`APP_OPENROUTER_API_KEY`；Codex 订阅使用 `APP_CODEX_SUBSCRIPTION_AUTH_FILE`，三者均设置匹配的 `APP_TRANSLATION_DEFAULT_ENGINE_ID`；YouTube 使用
`APP_YOUTUBE_DATA_API_KEY`；Scweet 的 X Cookie 写入 `services/scweet/cookies.json`，服务 URL
和 token 写入 `backend/.env`；Web 规则 Agent 按第 6 节填写引擎和两个可执行文件路径。
需要用户填写真实秘密时暂停，等用户在部署主机本地保存后再继续，并且检查时不得输出真实值。

Web 预览在 `mobile/` 安装依赖后运行 `pnpm run web --max-workers 1`；Android 可安装 Release
中的 APK，并在运行时输入 Reader 服务地址和连接 token，不需要把服务地址或模型 key 编译进 APK。
本机和受信局域网可以直接使用 HTTP，不应强制用户配置 HTTPS；局域网体验还需确认 API
监听地址和主机防火墙。服务暴露到公网时必须使用 HTTPS。只部署用户选择的
入口和能力，验收也应覆盖这些选择。所选能力与目标系统不兼容时（例如在 Windows 上启用
Web 规则 Agent），先说明限制，让用户改用 Linux 主机或明确关闭该能力，不能静默降级。

Reader 的最小生产形态有三个独立进程／服务：PostgreSQL、Reader API、Reader Worker。
移动 APK 不保存服务端密钥。规则 Agent 运行在现有 Worker 内，普通抓取和阅读不调用它。

```text
Android APK ── HTTP（本机／局域网）或 HTTPS（公网） ──> Reader API ──> PostgreSQL
                              │
Reader Worker ────────────────┘
Reader Worker 内：LangChain 规则作者 → 受控页面工具／真实规则验证 → Crawl4AI
```

推荐按以下顺序进行：确认范围与目标系统 → 告知本地凭证填写位置并等待用户完成 → 安装所选
依赖 → 启动 PostgreSQL → 运行迁移 → 启动 API 和 Worker → 验证所选能力 → 启动 Web 预览
或安装／连接 APK → 按第 7 节总结结果。需要自动解析新 Web 来源时，按第 6 节配置规则引擎。

## Docker Compose 部署

仓库根目录的 `compose.yaml` 提供 PostgreSQL、一次性迁移、Reader API、Worker、Browser
Manager／Reaper 和可选 Scweet 的完整部署。首次启动、全部配置项、密钥目录、升级、备份、
版本化 release 目录和回滚边界统一见[Docker Compose 部署文档](docker-deployment.md)。

最短流程为：

```bash
cp .env.docker.example .env.docker
./docker-init.sh init
# 在服务器本地填写 .docker/inputs/ 中需要的凭证
./docker-init.sh up
./docker-init.sh status
./docker-init.sh token
```

必须使用 `docker-init.sh` 或服务器审核过的包装入口；裸 `docker compose up` 不受支持。

## 2. 凭证清单与获取方式

把所有真实值只写入受限服务器上的 `backend/.env` 或进程环境；不要提交、粘贴到
Issue、放进 APK、截图或实验 workspace。

| 环境变量 | 是否必需 | 获取方式 | 放置位置 |
| --- | --- | --- | --- |
| `APP_SERVER_ID` | 必需 | 自己生成稳定、非敏感的部署标识，例如 `reader-prod-cn-1` | `backend/.env` |
| `APP_SERVER_ACCESS_TOKEN` | 必需 | 随机初始值，提供给获准连接的用户 | 后端 `.env`；用户运行时输入客户端 |
| `APP_AUTH_JWT_SECRET` | 必需 | 独立生成至少 32 字符随机值 | 仅后端 `.env` |
| `APP_DATABASE_URL` | 必需 | `postgresql+asyncpg://USER:PASSWORD@127.0.0.1:PORT/DB` | 仅后端 `.env` |
| `APP_DATABASE_SSL` | 必需 | 本文 loopback Compose 设 `false`；使用提供 TLS 的远端数据库时按实际配置 | 仅后端 `.env` |
| `POSTGRES_USER/PASSWORD/DB/PORT` | Compose 必需 | 数据库账号、随机密码、库名和 loopback 端口 | 仅后端 `.env` |
| `APP_DEEPSEEK_API_KEY` / `APP_OPENROUTER_API_KEY` | 使用 API key 翻译时二选一必需 | 在 DeepSeek 或 OpenRouter 创建 API key，并与默认翻译引擎匹配 | `backend/.env` |
| `APP_CODEX_SUBSCRIPTION_AUTH_FILE` | 使用 Codex 订阅翻译时必需 | 指向 Reader 自有的可写凭据库；使用独立 Codex 登录导入，见下文 | `backend/.env` 保存路径，凭据仅在服务器私有目录 |
| `APP_YOUTUBE_DATA_API_KEY` | 可选 | 在 Google Cloud 创建项目、启用 YouTube Data API v3、创建并限制 API key | `backend/.env` |

连接 token 和 JWT 签名密钥用途不同，不应共用。示例值仅展示配置格式，实际部署生成
随机值并保存在受限 `.env`，不提交到仓库。更换连接 token 后客户端重新输入即可，
无需重建 APK。数据库密码与 JWT 签名密钥始终只保留在后端。

### YouTube Data API key

1. 使用 Google 账号进入 [Google Cloud Console](https://console.cloud.google.com/)。
2. 新建或选择一个专门给 Reader 的项目。
3. 在 API Library 启用 **YouTube Data API v3**。
4. 进入 Credentials，选择 **Create credentials → API key**。
5. 点击 **Restrict key**：API restriction 只选 YouTube Data API v3；应用限制应按
   后端实际的固定出口 IP／网络策略配置。不要把 key 限制为 Android 或 Web referrer，
   因为请求由服务器发出。
6. 将值填为 `APP_YOUTUBE_DATA_API_KEY`，并在 Google Cloud 的 Quotas 页面观察用量。

Reader 只读取公开频道，不需要 OAuth client secret。未设置 key 或额度耗尽时，代码会
退回 YouTube 的公开 Atom feed，但数据完整性会降低。Google 的
[授权凭证说明](https://developers.google.com/youtube/registering_an_application) 和
[Data API 概览／配额](https://developers.google.com/youtube/v3/getting-started) 是该流程的
权威来源。

### 模型 API key

在 [DeepSeek Platform](https://platform.deepseek.com/) 创建 API key，按实际使用量充值，
然后填入 `APP_DEEPSEEK_API_KEY`。Reader 默认实际模型为 `deepseek-flash`（V4.1 Flash），服务地址为
`https://api.deepseek.com`。调用格式和余额／错误处理以
[DeepSeek API 文档](https://api-docs.deepseek.com/) 为准。也可将
`APP_TRANSLATION_DEFAULT_ENGINE_ID` 设为 `openrouter-minimax-m3`，并配置
`APP_OPENROUTER_API_KEY`。

长时间运行的 Worker 要求配置与默认翻译引擎匹配的有效凭据（API key 或下述 Codex 登录文件）。缺少该配置时，
翻译循环会失败，`/worker-ready` 不能通过，不应宣布完整部署成功。
`/worker-ready` 只验证配置可构建且循环正在运行，不会主动请求模型，因此不能证明
key 的鉴权和额度真实可用。部署 Agent 默认只检查 key 已配置、不是示例占位值，
并验证 `/worker-ready`；只有在获得明确授权后，才通过一次真实翻译请求做在线验收。
未执行在线验收时，交付结论应标明“翻译循环已启动，模型凭证未做在线验证”。

### Codex 订阅翻译

引擎 ID 为 `codex-subscription`，默认模型 `gpt-6-luna`，推理强度固定为 `low`。Reader 使用独立的凭据库，按需用 `refresh_token` 续期并保存完整的新令牌；不会自动读取个人的 `~/.codex`，也不依赖宿主机 Codex 常驻。服务端用户共用这份订阅额度，继续受 Reader 翻译配额约束。

**首次登录与导入**：先在有 Codex CLI 的机器上创建专用于 Reader 的登录目录，按 [Codex 官方认证说明](https://developers.openai.com/codex/auth) 完成 ChatGPT 登录。例如：

```bash
mkdir -m 700 /absolute/private/reader-codex-bootstrap
CODEX_HOME=/absolute/private/reader-codex-bootstrap codex -c 'cli_auth_credentials_store="file"' login --device-auth
```

设备授权需要账户允许；不支持时使用同一专用目录下的普通 `codex login` 浏览器流程。API key 登录不适用。导入的是包含 `access_token`、`refresh_token` 等字段的完整 `auth.json`，不要把 token 填入环境变量或发到聊天中。导入后由 Reader 独占管理这条刷新链：不要再用该登录目录运行 Codex，也不要定期把旧文件覆盖回来；个人开发环境应使用另一份独立登录。

**Docker 部署**：在 `.env.docker` 设置：

```dotenv
APP_TRANSLATION_DEFAULT_ENGINE_ID=codex-subscription
APP_CODEX_SUBSCRIPTION_MODEL=gpt-6-luna
APP_CODEX_SUBSCRIPTION_REQUEST_TIMEOUT_SECONDS=60
```

```bash
./docker-init.sh up
./docker-init.sh codex-auth import /absolute/private/reader-codex-bootstrap/auth.json
./docker-init.sh codex-auth status
```

Compose 将专用命名卷 `codex-auth` 挂载给 API 和 Worker，路径固定为 `/var/lib/reader-codex/auth.json`。初始化工具将卷目录设为 UID/GID 10001、权限 0700，凭据文件和锁文件为 0600。卷可写且独立于容器的只读根文件系统；容器重建、升级和普通 `down` 均保留卷，删除卷会丢失登录。无需 `compose.override.yaml` 或宿主机个人 Codex 目录挂载。若之前使用了只读挂载示例，请先移除该覆盖配置再升级。

导入命令通过 stdin 传送凭据，保存成功后重启 API、Worker，让启动时尚未配置的路由生效。首次 `up` 到完成导入之间翻译未就绪，`/worker-ready` 可能返回 503；只配置模型名并不等于登录完成。`status` 仅检查本地凭据，不发送模型请求；`codex-auth refresh` 在即将过期时执行 OAuth 续期，可单独检查刷新链，但不验证模型额度或推理权限。

**非 Docker 部署**：在 `backend/.env` 指定 Reader 自有存储路径，两进程应以同一系统用户运行并访问同一个可写私有目录，然后在 `backend/` 执行：

```dotenv
APP_CODEX_SUBSCRIPTION_AUTH_FILE=/absolute/private/reader-codex/auth.json
APP_CODEX_SUBSCRIPTION_MODEL=gpt-6-luna
APP_TRANSLATION_DEFAULT_ENGINE_ID=codex-subscription
```

```bash
uv run reader-admin codex-auth import --source /absolute/private/reader-codex-bootstrap/auth.json
uv run reader-admin codex-auth status
```

导入后重启 API、Worker。凭据管理命令不依赖数据库。原先指向 Codex 原始文件的配置需迁移到新的 Reader 目录并执行导入；未导入的文件会显示为非 Reader 管理的登录。

**自动续期与恢复**：请求前若访问令牌剩余寿命不足 120 秒，Reader 获取跨进程文件锁，重新读取凭据，再按需刷新。刷新完成后原子替换文件；等待的进程采用新令牌，不重复消费旧刷新令牌。推理返回 401 时最多刷新后重试一次，403 和限流不会触发该强制刷新。锁和 OAuth 请求有界等待；刷新期间取消翻译不会提前释放锁，正在进行的刷新仍完成写回。多机部署不支持复制文件各自刷新，当前方案要求共享同一台主机上支持文件锁和原子替换的文件系统。

连接失败、429 和服务端临时错误会保留凭据并退避 30 秒；若原访问令牌仍有效且未被 401 拒绝，可暂时继续使用。刷新令牌被拒绝时会持久标记为需要重新登录，避免请求风暴。若进程在交换期间崩溃、请求发出后断线、返回的新凭据无效，或刷新后落盘失败，Reader 不能确定旧刷新令牌是否已消费，也会要求新登录，不重放旧令牌。先完成新的独立 Codex 登录，再替换：

```bash
./docker-init.sh codex-auth import /absolute/private/reader-codex-bootstrap/auth.json --replace
# 非 Docker：uv run reader-admin codex-auth import --source /path/to/fresh/auth.json --replace
```

过期但仍可刷新的凭据在进程重启时仍可建立路由；重新导入用于恢复不可刷新或被撤销的登录，不是日常操作。备份中的旧刷新令牌可能已被消费，恢复旧卷后不能假定还能续期，应准备重新授权。

此实现参考 [Hermes 的刷新事务](https://github.com/NousResearch/hermes-agent/blob/51b5c314e9de80b5c0e70b57f227fb1700d6eb2a/hermes_cli/auth_codex.py)、[OpenClaw 的集中凭据管理](https://github.com/openclaw/openclaw/blob/main/src/agents/auth-profiles/oauth-manager.ts) 及 [Codex OAuth 协议实现](https://github.com/openai/codex/blob/f5f08c54cb7a774594d3579c5731ea3e87f01c48/codex-rs/login/src/oauth/client.rs)。订阅推理仍调用 `chatgpt.com/backend-api/codex/responses`，并非公开、稳定的 OpenAI API 契约；模型权限、额度及端点兼容性以真实调用为准。该引擎仅用于翻译，网页规则 Agent 继续使用 DeepSeek／OpenRouter。常规测试只用模拟 OAuth 和模型响应，在线验收需另行明确授权。

### X 内容（可选：Scweet）

Scweet 是 Reader 当前的 X 采集实现。它使用一个专用 X 账号的网页登录 Cookie，服务
只能部署在私有网络中。没有配置它时，只有 X 同步不可用；RSS、网页、Reddit、YouTube、
阅读和翻译不受影响。Reader 固定使用 Scweet v5.5.0 的提交
`0192d1a74a1421c61f75c35bd7917c18d90227fd`；Docker 构建会校验源码归档并安装锁定依赖，
宿主机 Python 安装也必须检出同一提交，具体命令见
[Scweet 服务文档](../services/README.md#clone-and-install-again)。

1. 注册并只用于 Reader 的 X 专用账号；不要使用个人主账号。登录
   `https://x.com` 并完成该账号需要的安全验证。
2. 在已登录浏览器中打开开发者工具：Application（或 Storage）→ Cookies →
   `https://x.com`，复制名为 `auth_token` 的 Cookie 值。Scweet 会用它初始化需要的
   `ct0` 值。Cookie 是等同于登录凭证的秘密，不要放入截图、聊天、Git、APK、
   `backend/.env` 或 GitHub Secret。
3. 将 Cookie 只保存为主机本地的 `services/scweet/cookies.json`：

   ```json
   [
     {
       "username": "reader_dedicated_account",
       "cookies": { "auth_token": "paste-the-local-cookie-here" }
     }
   ]
   ```

4. 生成服务间令牌并以 systemd 启动私有宿主机服务。复制
   [`reader-scweet.service.example`](../services/systemd/reader-scweet.service.example)
   和 [`scweet.env.example`](../services/systemd/scweet.env.example)，按
   [Scweet 服务文档](../services/README.md#recommended-server-deployment-systemd--host-python)
   创建 `/etc/reader/scweet.env`、状态目录并启用 `reader-scweet.service`。
   该服务实际链路为 `systemd → uvicorn → scweet_service/app.py → 本地 Scweet 包`。
   API 和 Scweet 在同一主机时使用：

   ```dotenv
   APP_X_PROVIDER=scweet
   APP_SCWEET_SERVICE_URL=http://127.0.0.1:8090
   APP_SCWEET_SERVICE_TOKEN=<与 /etc/reader/scweet.env 相同的值>
   ```

   启动后用 `/health` 检查进程存活，用 `/ready` 检查当前是否至少有一个可采集账号。
   Cookie 缺失、格式错误或不能导入完整认证资料时服务拒绝启动；账号临时冷却或达到当日
   限额时进程继续运行，`/health` 返回 200，`/ready` 暂时返回 503，恢复后自动变回 200。

   Docker Compose 方案不需要预先克隆 Scweet，详见同一服务文档。Worker 和 Scweet 都运行
   在 Docker 时，把 Worker 加入 `reader-internal` 网络，并将 URL 改为
   `http://scweet:8090`。Cookie 只作为运行时只读 secret 挂载，SQLite 状态保存在命名卷中；
   Scweet 还需要访问 X 的 DNS 和 HTTPS 出站网络。

Cookie 会过期，需要由该专用账号重新登录后更新本机文件，再重启容器。使用前请自行审查
[X 自动化规则](https://help.x.com/en/rules-and-policies/x-automation)、适用条款及当地法律；
保持低频率，不要自动发帖、私信或规避限流。

Scweet 是非官方集成，X 可能随时调整政策、页面结构、登录验证和限制机制，采集可能受到
限流、中断或账号限制。本项目及其维护者无法保证 X 采集持续可用，也无法避免、解除或恢复
账号限制。部署者启用该功能即表示自行评估并承担账号与合规风险；如果不能接受这些风险，
应保持 X 采集关闭。关闭它不会影响其他来源和阅读功能。

## 3. 配置 PostgreSQL

在 `backend/` 从 `.env.example` 创建权限为 600 的 `.env`，设置上表必需值。
本文 loopback Compose 未配置数据库 TLS，保留 `APP_DATABASE_SSL=false`。
APP_DATABASE_URL 须与 POSTGRES_USER/PASSWORD/DB/PORT 一致，然后运行：

```bash
docker compose -f compose.postgres.yml up -d
```

PostgreSQL 18 持久 volume 挂载 `/var/lib/postgresql`，默认数据位于其 `18/docker`
子目录；不能把旧主版本数据目录直接交给新版本启动。数据库端口只绑定 loopback。
容器健康检查之后还须执行 Alembic 并验证 `/ready`。定期备份数据库并验证恢复。

已有 Supabase 部署使用 `scripts/migrate_supabase.py` 只读导出业务表、账号必要字段
和头像，再导入空的 PostgreSQL 目标库。保留 UUID/原密码哈希，校验后再切换 API/Worker。
原 Supabase 保留供回退，旧会话不迁移，用户重新登录。执行参数见
[迁移脚本](../backend/scripts/migrate_supabase.py) 的 `--help`；当前账户与数据归属见
[架构说明](ARCHITECTURE.md#数据归属)。

不再需要 Auth 回调白名单、Storage bucket 或邮件模板。注册立即可用。忘记密码通过
`uv run reader-admin users reset-password <email>` 由管理员重置；已登录用户输入当前密码
即可修改密码或邮箱。

## 4. 启动 API 与 Worker

```bash
cd backend
# 使用第 3 节已配置的 .env，不覆盖已有环境文件。
uv sync --dev
uv run alembic upgrade head
```


Web 规则使用 Crawl4AI 的原生提取能力。需要 JavaScript 的探索、规则试跑与周期扫描
统一使用 Lightpanda；按[统一 Lightpanda 浏览器后端](#统一-lightpanda-浏览器后端)准备
Worker 用户的二进制、Linux／bubblewrap 隔离环境及共享配置。
Playwright 仅作为 CDP 控制库，不需要执行 `playwright install chromium`，也不会自动启动 Chromium。
静态 HTTP 抓取不要求安装浏览器；它的成功不能作为动态执行依赖验收证据。
常规来源扫描不需要模型密钥；Worker 的翻译循环需要默认模型凭证，
启用规则作者后，其生成和修复步骤也使用配置的模型。

在独立的进程管理单元启动 API 和 Worker：

```bash
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
cd backend
uv run reader-worker
```

生产环境应将 API 放在 HTTPS 反向代理之后，设置
`APP_PUBLIC_API_BASE_URL=https://reader.example.com`。将 `/ready` 和
`/worker-ready` 仅提供给编排器或管理网络，公网入口只需要 `/health` 与业务 API。

更新已有 systemd 部署时，代码同步本身不会替换已加载的 Python 进程。完成依赖安装和
迁移后必须显式重启 `reader-api` 与受影响的 worker，并核对新 `MainPID`、启动时间和
journal；否则新版 APK 仍会命中旧进程中相同的未版本化 `/v1` 路由。实时网页／字幕
翻译的后端更新不要求重新构建 APK，只有客户端 bridge、调度或错误文案变化才需要新包。

实时翻译默认保留三条数据库连接、零 overflow 和五秒 checkout timeout。不要为了提高
DeepSeek 并发而按同样数字扩大数据库池：配置与翻译缓存的 session 必须在模型等待前
结束，缓存持久化使用独立短 session。若日志出现 `translation_storage_busy` 或
`QueuePool` timeout，先检查运行代码版本和事务生命周期，再根据 PostgreSQL 的总连接预算
调整池大小。

## 5. 验收

按用户选择的访问范围替换地址后逐项检查。本机和局域网可以使用 HTTP；下面以本机为例，
手机连接时改为部署主机的局域网 IP，公网部署则改为实际的 HTTPS 域名：

```bash
READER_API_BASE_URL=http://127.0.0.1:8000
curl -fsS "$READER_API_BASE_URL/health"
curl -fsS "$READER_API_BASE_URL/ready"
curl -fsS "$READER_API_BASE_URL/worker-ready"
```

通过客户端或从受限配置读取 header 的脚本检查 discovery，避免 token 写入命令历史。
正确 token 返回 200，文档仅含 `protocol_version: 2`、`server_id`、`api_base_url`；
缺少或错误 token 必须拒绝 discovery、注册、登录和业务请求。不得泄露 database URL、
JWT secret 或 provider key。在 APK 输入同一地址和 token，验证注册直接登录、退出、
再次登录、改密、头像和一条来源的同步。

## 6. 后端 Web 规则 Agent

规则作者和正常扫描在同一个 Reader Worker 内运行；探索需安装下文固定版 agent-browser 原生文件；CLI daemon 随任务回收。
（API 也读取引擎配置以显示来源状态）：

```dotenv
APP_WEB_RULE_AGENT_ENGINE_ID=deepseek-v4-flash
APP_INGESTION_BROWSER_ENGINE=lightpanda
APP_INGESTION_LIGHTPANDA_EXECUTABLE_PATH=/opt/reader/lightpanda-0.4.0
APP_WEB_RULE_AGENT_MAX_TOTAL_TOKENS=3000000
APP_WEB_RULE_AGENT_MAX_MODEL_CALLS=80
```

也可选择 `openrouter-minimax-m3`；实际模型读取对应 `DEEPSEEK_MODEL` /
`OPENROUTER_MODEL`（或 `APP_` 别名），凭证和 API base 与翻译共用。
默认 `disabled` 停用自动编写；用户个人翻译偏好不影响这个选择。更改模型配置需要重启
API 和 Worker，已经开始的任务不能静默更换模型。

添加 Web 订阅只写入持久任务，不等待模型。Worker 补建旧来源任务、检查页面、调用
LangChain 工具循环。真实规则执行返回标题、URL、续读与错误事实，由 Agent 判断是否满足发现需求，
并提交绑定同一候选及试跑记录的终稿；宿主核对这份交接后激活规则。
首窗可用但历史续读未确认或失败时，Agent 可以说明限制后交付。
正常同步始终只执行保存的原生 Crawl4AI 列表规则。
旧规则的 `article` 字段仍可解析，但扫描和验证不再逐篇抓取详情；已有正文和 RSS 自带内容保留。
移动端 Web／RSS 阅读模式从当前 WebView 按需提取正文，不需要新的服务端正文任务或部署单元。
全部署同时执行一个规则任务；工具串行。失败候选、消耗与错误阶段保存在数据库；任务
自动重试不会重置预算。只设总 token 和模型调用次数两项累计额度；不再按工具/验证次数、Goal 轮数、attempt、任务时长或上下文字节数停止。
模型请求发送前持久计数，失败/超时、Goal 续跑和重试也计入 80 次，第 81 次不发送；最后一次返回的合法终稿仍可保存。
每次模型请求前检查累计实际输入（含缓存命中）与输出；用量在响应后结算，因此最后一次响应可能跨过阈值，
随后不再发起模型请求。缺失 usage 的已发送请求保守计入未知消耗；字节预估不再提前拒绝正常请求。
单次网络超时、输出与工具返回体积、租约和诊断大小仍属于运行参数，见 `backend/.env.example`。
探索工具以节点索引返回有界结构化证据，规则执行反馈提供可按条目读取的实际结果；
Agent 保存发现与候选并按预算收束到试跑／修正和终稿。历史裁剪和单条输出上限不改变上述累计额度，
也不允许省略与终稿绑定的真实执行记录。
升级时清理已移除的工具/验证次数、Goal、attempt、任务时长、上下文字节数和图步数环境变量；
已有失败任务保留历史预算和结果，重启不自动重试。
本次更新采用 prompt v9／executor v4 和共享浏览器身份。已经开始且仍带旧快照的任务
沿用既有 `config_changed` 阻塞流程；通过下面的管理 `retry --request-id` 新建任务，
不会接续旧 coverage 或重置旧任务预算。无需为此增加数据库迁移；普通已保存规则继续由周期扫描执行。

运维通过 `uv run reader-admin web-rules` 的 list/show/retry/resume-engine 子命令
查看任务与诊断、明确重试失败任务或解除引擎暂停。401/402 会暂停后续规则模型调用；
恢复引擎只放行排队任务，已失败或 blocked 的任务需指定重试，不自动刷新额度。
已有规则同步不受规则引擎停用或暂停影响。

在 `backend/` 目录运行，例如：

```bash
uv run reader-admin web-rules list --status blocked
uv run reader-admin web-rules show JOB_UUID
uv run reader-admin web-rules resume-engine
uv run reader-admin web-rules retry JOB_UUID --request-id REQUEST_UUID
```

最后一步是人工发起的新预算任务；为同一次运维请求生成一个 UUID，重发时沿用该值，
防止重复派单。修复凭证或余额后先解除暂停，再明确重试需要处理的终态任务。

首次启用后核对实际任务从 queued 到 succeeded、规则版本和之后的普通同步结果；
仅 Worker 启动或 Agent 返回文本不代表规则已经可用。当前执行流程与激活边界见
[网页规则编写与激活](ARCHITECTURE.md#网页规则编写与激活)，工具定义与验证实现见
[web_rules.py](../backend/src/app/ingestion/web_rules.py)。

### 统一 Lightpanda 浏览器后端

`APP_INGESTION_BROWSER_ENGINE=lightpanda` 是唯一支持的动态浏览器配置。
Agent 探索、真实规则试跑、管理 CLI 和 Worker 周期性执行已保存规则都使用同一个受控入口。
规则的静态执行仍经 HTTP 获取，真实试跑和周期动态执行结束后关闭各自浏览器。Agent 探索使用独立的持久 CLI／Lightpanda 会话，模型等待和规则试跑期间保留交互状态，任务结束才回收。

```dotenv
APP_INGESTION_BROWSER_ENGINE=lightpanda
APP_INGESTION_LIGHTPANDA_EXECUTABLE_PATH=/opt/reader/lightpanda-0.4.0
```

旧 `APP_WEB_RULE_AGENT_BROWSER_ENGINE` 和 `APP_WEB_RULE_AGENT_LIGHTPANDA_EXECUTABLE_PATH`
仍作为配置别名读取，新键优先；旧路径也会用于周期扫描和真实试跑。升级时迁移到上面的共享键并移除旧键。
仅保留旧 `chromium` 值会产生明确的配置错误，不会恢复 Chromium 路径。

上面的路径仅为示例，由维护者预置并核对对应平台的 Lightpanda **0.4.0** 可执行文件。
Reader 不下载二进制，也不自动切换引擎。Worker 系统用户必须能够读取并执行该绝对路径，
运行在 Linux 上，且 `PATH` 中有 `bwrap`（bubblewrap），能够创建实现要求的 user／PID 等命名空间。
版本命令也在隔离环境内执行；只有输出版本严格匹配时才启动动态页面执行。
缺少二进制、版本不符或隔离环境不可用均直接失败，不能关闭隔离后裸跑。
API 启动与静态 HTTP 不检查本地二进制；动态执行在实际需要它时报告 `browser_unavailable`。

[浏览器运行时](../backend/src/app/ingestion/browser_runtime.py) 使用临时进程与独立文件系统视图：
清理继承环境，仅挂载运行所需系统目录、证书与二进制的只读路径，为 home、tmp、run 提供空临时目录。
真实用户 home、仓库和 Reader 凭据不会提供给浏览器。CDP 只监听宿主控制的 loopback 端口。
这不是网络命名空间隔离：本机 CDP 需要共享宿主网络；浏览器原生流量使用全 CIDR 阻断、不可用代理和零 WebSocket 并发限制，
受支持的页面 GET 请求由 Reader 的 `safe_get` 获取后交给浏览器，沿用 DNS 绑定、公网目的地、TLS、robots 和资源限制。
运行时限制 V8 heap 为 64 MiB，这不等于浏览器总内存上限，也不构成可调高并发的依据。
CDP incoming message 上限固定为 32 MiB，容纳既有最大 20 MiB HTTP 响应的 base64 与 JSON 封装；
HTTP 响应、累计流量与 DOM 大小限制仍独立生效。Lightpanda 默认的 1 MiB CDP 上限不足以接收大页面，
不能把由此造成的连接断开当成规则提取失败。

Worker 将引擎、启动策略和实际二进制 SHA-256 加入任务执行快照；SHA-256 用于识别执行物变化，
不是内置的发行文件哈希白名单。API 的配置展示不读取或执行 Worker 本地二进制。
同一路径更换文件也可能使执行身份变化；运行中的任务不能静默使用新的浏览器。

#### 容器浏览器任务模式

容器部署可以设置 `APP_BROWSER_CONTROLLER_URL` 和控制令牌，使 Worker 通过内部 Browser Task
Manager 申请浏览器任务；未设置 URL 时仍使用上面的本机 bubblewrap 模式。两种模式的
`safe_get`、robots、重定向和大小预算一致，静态规则仍由 Worker 直接请求。容器模式不维护
目标站点域名白名单：新增公开 RSS 或 Web 来源无需修改 Docker 网络配置，adapter 会按通用
公网目的地规则判断每个请求。

每个任务包含两个短期容器和一个只保存 Unix socket 的任务卷。browser 固定使用
Lightpanda 0.4.0 与 agent-browser 0.37.1，设置 `network_mode: none`，以非 root、只读文件系统、
`cap_drop: ALL` 和 no-new-privileges 运行；它不接收 Reader 源码、数据库、模型凭据、X Cookie
或 Docker socket。adapter 通过任务专属 Unix socket 控制 browser，只能以类型化
`cli_command` 或一次性 `render_page` 执行操作；页面响应继续由 adapter 的受控传输提供。
adapter 接入专用 data 与 egress 网络，Worker 接入 control 与 data 网络，Manager 只接入
control 网络。Manager 和独立 Reaper 持有 Docker socket并共享任务状态目录；Reaper 不持有
控制令牌或 capability HMAC key。不得把 Docker socket 或 Manager 管理令牌放入 Worker、
adapter 或 browser。

探索使用 `purpose=explore` 的持久任务；每次动态规则执行使用独立的 `purpose=render` 短任务，
不会复用或关闭探索页面。默认最多两个浏览器任务，其中最多一个 explore，为 render 保留
容量。Manager 只有在 browser、Unix socket、adapter 身份与健康检查全部就绪后才返回 endpoint。
Worker 在模型等待期间独立续租；取消、控制连接丢失、续租失败和正常关闭都会触发有界回收。
SQLite 状态目录由 Manager 与 Reaper 的共享组以 `2770` 访问，数据库文件使用 `0660`；
controller-only HMAC key 单独使用 `0600`，仅 Manager 可读写，不能提供给 Reaper；
任务 capability 只保存在 Worker 内存及目标 adapter，日志和规则快照不得包含它。

Reader 镜像提供 `reader-browser-manager`、`reader-browser-reaper`、`reader-browser-adapter` 和
`reader-browser-adapter-healthcheck` 入口。browser 镜像由
`services/browser_runtime/Dockerfile.browser` 构建；Manager 启动时根据镜像 label 和实际
Image ID 核对固定二进制身份，不能只信任环境变量中的版本声明。内部控制与数据协议分别生成
为 `contracts/browser-controller-openapi.json` 和 `contracts/browser-adapter-openapi.json`，
不暴露在 Reader 公共 API。示例配置键及安全的空值见 `backend/.env.example`；部署层应通过
secret file 注入控制令牌与 HMAC key，并为 Manager 状态、每任务的两个容器和控制卷设置同一
部署标签，使 reaper 只回收本部署拥有的任务资源。data 与 egress 是部署时预先创建的共享网络，
不属于单个任务，reaper 不删除它们。

Worker 使用 `APP_BROWSER_CONTROLLER_TOKEN_FILE` 读取同一 Manager bearer；该文件可为
root 所有、通过 supplemental group 以 `0440` 挂载。不能同时设置
`APP_BROWSER_CONTROLLER_TOKEN`，也不能让该 secret 对 other 可访问或对 group 可写。

| 错误代码 | 运维含义 |
| --- | --- |
| `browser_unavailable` | 二进制、版本、Linux／bubblewrap 条件或启动检查失败；在 Worker 系统用户下核对安装与权限 |
| `browser_configuration_changed` | 实际执行身份与任务快照不一致；核对配置和二进制变更，不修改任务快照绕过检查 |
| `browser_redirect_unsupported` | 当前 Lightpanda 动态传输路径不支持安全处理该 HTTP 重定向；不会放开浏览器直连或自动切换引擎 |
| `browser_cleanup_failed` | 本任务浏览器在终止后仍未退出；检查任务拥有的进程与日志，不把该次检查视为成功 |

可在 `backend/` 中显式运行本地 fixture 检查，使用真实隔离浏览器，但以模拟传输提供页面，
不调用模型或公开网站；未设置变量时该检查会跳过。它验证大于 1 MiB 页面的受控传输、
共享配置下的探索和普通规则扫描、脚本及 click／wait／scroll 动作、网络边界与取消清理，
不能代替目标网站兼容性或生产部署验收：

```bash
PYTHON_DOTENV_DISABLED=1 READER_TEST_LIGHTPANDA_BINARY=/opt/reader/lightpanda-0.4.0 uv run pytest -q tests/test_lightpanda_controlled.py
```

执行仍遵守串行资源调度，并在真实 Worker 系统用户下核对最终规则验证和后续普通扫描。
配置 Lightpanda 成功或一项本地 fixture 通过，不代表新 Web 来源已经可以订阅。

#### Ubuntu 24.04 的 AppArmor 前提

Ubuntu 24.04 的 AppArmor 用户命名空间限制可能使 `bwrap` 报
`setting up uid map: Permission denied`，即使 `kernel.unprivileged_userns_clone=1`。
保留 `kernel.apparmor_restrict_unprivileged_userns=1`，不要通过关闭限制、设置 setuid
或给通用执行器添加 `unconfined` 白名单解决。依据见
[Ubuntu 官方说明](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007)。

先核对已安装 AppArmor、parser／ABI、已加载的 `bwrap`／`unpriv_bwrap`，以及同名文件、
`local/bwrap-userns-restrict`、`local/unpriv_bwrap` 的现有配置。缺少策略时，从
[发行版 apparmor-profiles 包](https://packages.ubuntu.com/noble-updates/all/apparmor-profiles/filelist)
提取 `usr/share/apparmor/extra-profiles/bwrap-userns-restrict`，记录包版本、来源及 SHA-256；
无需安装整包或启用其他 profile。选择与目标发行版和 parser ABI 匹配的版本；已验证的
Ubuntu 包 `4.0.1really4.0.1-0ubuntu0.24.04.7` 使用 ABI 4.0，可由 AppArmor 4.0.1 解析。
不要直接移植使用不同 ABI 的上游 master。保持官方父子策略原样：父 `bwrap` 建立隔离环境，
执行子程序时叠加 `unpriv_bwrap`，后者含 `audit deny capability`。

将提取文件放在受限目录，先仅解析，确认无冲突后以 root 所有权安装并只加载这一份文件
定义的两个 profile；以下 `/受限目录/` 替换为实际路径：

```bash
apparmor_parser -Q -T -I /etc/apparmor.d /受限目录/bwrap-userns-restrict
sudo install -o root -g root -m 0644 /受限目录/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -r -T /etc/apparmor.d/bwrap-userns-restrict
```

这个 `/usr/bin/bwrap` 策略作用于系统中所有 bwrap 使用者。手工提取的文件不会随源包自动
更新；系统安全更新后须重新核对官方策略、ABI 和本机冲突，并重复验收。
验收使用实际 Worker 用户、环境及等效 systemd 限制，经生产
`SourceBrowserSession.inspect(render_js=True)` 和模拟安全传输验证 JS、有界证据、缓存及清理。
同时观察实际 Lightpanda 的 `unpriv_bwrap` 标签与 `CapEff`，并用受控子进程验证命名空间内
需要 capability 的操作被拒绝且有审计记录；启动成功或 `CapEff=0` 单独不足以证明子策略生效。
检查遵守串行资源调度，不调用模型或新建规则任务。通过后，等待规则槽位空闲，再更新共享
浏览器配置并重启 API／Worker，确认探索、真实规则试跑与后续周期扫描使用同一配置。

需要撤销本次安装的隔离策略时，先停止使用它的动态浏览器任务并确认自有 Lightpanda／bwrap 进程已退出；
不切换 Chromium。若这两个 profile 是本次新增且没有其他服务依赖，执行
`sudo apparmor_parser -R /etc/apparmor.d/bwrap-userns-restrict` 卸载二者，再把该持久文件移回
受限备份目录，防止重启后重新加载；保留原有共享策略与全局限制。

### agent-browser CLI 探索

生产 `inspect_web_page(argv)` 使用 [CLI 浏览器组件](../backend/src/app/web_rule_agent/cli_browser.py)。
启用自动规则作者前，设置 `APP_WEB_RULE_AGENT_CLI_EXECUTABLE_PATH` 为绝对原生可执行路径；
API 只保存配置值，不读取 CLI 二进制。Worker 领取任务时独立保存 CLI 版本、binary／runner
内容身份；重试时身份变化会阻止旧任务续跑，既有 Lightpanda 执行身份保持独立。
运行时仅接受 agent-browser **0.37.1** 原生可执行文件，
与上文的 Lightpanda **0.4.0** 配合使用。准备本地验收环境时，可安装固定版 npm 包：

```bash
npm install --prefix /opt/reader/agent-browser-0.37.1 agent-browser@0.37.1
```

Linux x64 原生文件位于该安装目录的
`node_modules/agent-browser/bin/agent-browser-linux-x64`；其他平台需选对应的原生文件。
不运行 `agent-browser install` 下载 Chromium。运行用户需要 Linux、可用的 bubblewrap、
`/usr/bin/python3`，以及上述两个可执行文件的读取与执行权限。

组件为每个任务创建一个临时 CLI PID namespace。namespace 中仅有标准库 argv runner、
逐次执行的 CLI 客户端和 CLI 自己创建的 daemon；命令不用 shell，daemon 不成为额外常驻服务。
宿主只读挂载必要系统目录、原生 CLI 和 runner 文件，提供独立的临时 home／tmp／run，
仅将任务自己的 socket 与空配置目录可写挂载进去，不挂载整个仓库或真实用户 home。
CLI 的版本、会话、namespace、空配置和 CDP 地址由宿主固定，idle timeout 为 0，
有效任务的模型等待与规则试跑不会触发 CLI 闲置关闭。Lightpanda 仍由已有浏览器运行时单独隔离。
HTTP、robots、DNS 绑定、TLS 和累计流量通过 Reader 的受控 CDP bridge 执行；
顶层真实导航更新每页预算，同页交互和子 frame 不重置预算。
同页请求出站前预留读取额度，并将实际剩余额度传给 HTTP 读取上限；其他在途请求可能
归还额度时先等待其结算，避免临时预留使合法响应被误判超限。成功响应归还未使用部分；
读取失败或取消时按该次预留上限保守计入，并释放预留，旧页面的结算不影响新页面预算。

任务结束时先尝试 CLI close，随后终止整个任务 namespace，即使 CLI close 失败也回收其中
detached daemon；取消、任务超时和父进程退出依赖相同的进程所有权与 bubblewrap
`--die-with-parent`／PID namespace。Worker 失去租约会取消任务并经 context.close 回收会话；
普通参数或 JavaScript 错误只返回实际错误，不关闭浏览器。

组件先返回真实 CLI 内容和错误，调用方加入结果 ID 等元数据后，统一调用
`finalize_cli_report`，按完整 JSON 的 UTF-8 字节数限制到 20 KiB。
`truncated`／`truncated_fields` 仅表示 Reader 的捕获或返回裁剪；CLI 自带截断文本和 warning
保留原文，`truncated: false` 不表示已经取得完整网页。小限额下完整 URL 放不下时，
`url` 为 `null` 且 `truncated_fields` 包含 `url`，该报告不派生导航许可；内部仍按实际观察到的
URL 和 DOM 修订区分当前与历史证据。只从最终实际返回的完整链接派发导航授权。

显式运行本地受控检查（使用真实 CLI／Lightpanda、模拟网页 HTTP，不调用模型或公开网站）：

```bash
PYTHON_DOTENV_DISABLED=1 READER_TEST_AGENT_BROWSER_BINARY=/opt/reader/agent-browser-0.37.1/node_modules/agent-browser/bin/agent-browser-linux-x64 READER_TEST_LIGHTPANDA_BINARY=/opt/reader/lightpanda-0.4.0 uv run pytest -q tests/test_cli_browser_controlled.py
```

未设置任意一个显式测试路径时会跳过；普通契约与流式输出检查为 `tests/test_cli_browser.py`。
所有检查遵守仓库串行资源调度，不代表生产模型完成规则交接或目标网站兼容性已经验收。

## 7. 交付前清单

- [ ] 开始安装前已让用户确认目标系统、访问范围、体验入口和四项可选能力。
- [ ] `backend/.env`、私有配置和 provider key 都未提交。
- [ ] API、Worker、数据库迁移和 `/ready` 均通过；完整部署的 `/worker-ready` 也通过，受限部署则已明确记录翻译循环未就绪。
- [ ] 更新部署已重启实际 systemd 进程，并核对新 PID、启动时间和最新 journal。
- [ ] 实时翻译日志未出现 `QueuePool` timeout，provider、quota 与 persistence 耗时可区分。
- [ ] 客户端通过用户选择的本机／局域网 HTTP 或公网 HTTPS 地址完成连接、注册、登录与同步测试。
- [ ] 启用 X 功能前完成条款、政策与账户风险审查；不需要时保持关闭。
- [ ] APK 作为 GitHub Release asset 上传，而不是提交进仓库。

最后必须向用户总结：

- 实际启动的服务、是否开机自启，以及本机／局域网／公网可用的 HTTP 或 HTTPS 地址；
- Web 预览的打开方式，以及 Android 的安装方式、服务地址和连接 token 的本地存放位置；
- 翻译、X、YouTube 和 Web 规则自动生成分别是已启用、降级还是关闭，并说明实际影响；
- 对用户所选能力执行了哪些验收、结果如何，模型 key 是否做过获授权的在线请求验证；
- 尚需用户完成的操作和已知限制。

用户明确放弃的可选能力不算部署失败，但必须在总结中列明。任何用户已选择的必需组件或
验收失败时，不得宣布部署完成；可以准确说明当前已完成部分和剩余阻塞项。
