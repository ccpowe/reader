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
4. 是否启用 X 内容同步和 YouTube Data API？

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

用户确认后，Agent 先从 `backend/.env.example` 创建 `backend/.env`，再按所选能力明确告知
用户应填写的字段：DeepSeek 使用 `APP_DEEPSEEK_API_KEY`；OpenRouter 使用
`APP_OPENROUTER_API_KEY`；Codex 订阅使用 `APP_CODEX_SUBSCRIPTION_AUTH_FILE`，三者均设置匹配的 `APP_TRANSLATION_DEFAULT_ENGINE_ID`；YouTube 使用
`APP_YOUTUBE_DATA_API_KEY`；Scweet 的 X Cookie 写入 `services/scweet/cookies.json`，服务 URL
和 token 写入 `backend/.env`。
需要用户填写真实秘密时暂停，等用户在部署主机本地保存后再继续，并且检查时不得输出真实值。

Web 预览在 `mobile/` 安装依赖后运行 `pnpm run web --max-workers 1`；Android 可安装 Release
中的 APK，并在运行时输入 Reader 服务地址和连接 token，不需要把服务地址或模型 key 编译进 APK。
本机和受信局域网可以直接使用 HTTP，不应强制用户配置 HTTPS；局域网体验还需确认 API
监听地址和主机防火墙。服务暴露到公网时必须使用 HTTPS。只部署用户选择的
入口和能力，验收也应覆盖这些选择。所选能力与目标系统不兼容时，先说明限制，让用户更换主机或明确关闭该能力，不能静默降级。

Reader 的最小生产形态有三个独立进程／服务：PostgreSQL、Reader API、Reader Worker。
移动 APK 不保存服务端密钥。

```text
Android APK ── HTTP（本机／局域网）或 HTTPS（公网） ──> Reader API ──> PostgreSQL
                              │
Reader Worker ────────────────┘
```

推荐按以下顺序进行：确认范围与目标系统 → 告知本地凭证填写位置并等待用户完成 → 安装所选
依赖 → 启动 PostgreSQL → 运行迁移 → 启动 API 和 Worker → 验证所选能力 → 启动 Web 预览
或安装／连接 APK → 按第 6 节总结结果。

## Docker Compose 部署

仓库根目录的 `compose.yaml` 提供 PostgreSQL、一次性迁移、Reader API、Worker 和可选
Scweet 的完整部署。首次启动、全部配置项、密钥目录、升级、备份、
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
| `APP_PRODUCT_HUNT_TOKEN` | 可选 | 在 [Product Hunt 开发者后台](https://www.producthunt.com/v2/oauth/applications)创建 Confidential 应用并生成 Developer Token；默认非商业用途 | `backend/.env` 或私密进程环境；Docker 使用 `.docker/inputs/product-hunt-token` |

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

已有部署升级到候选载荷迁移 `20260925_30` 时，**先停止旧 API 和全部 Worker，再迁移，
最后启动新版进程**；API 的排行路径同样可以生产候选，不能只停扫描器。该迁移将队列中的
可用性与列表观察标记改为显式字段，保留原哈希、租约和基线属性；新版要求
`reader-runtime-v26`，不兼容旧、新候选写入进程并行运行。Docker 的 `./docker-init.sh up`
已按停止旧进程、迁移、启动新进程的顺序执行。需要回退本次载荷变更时，也先停止所有
写入者，再于 `backend/` 执行 `uv run alembic downgrade 20260922_29`，随后切回旧版代码；
不要让旧消费者读取未回退的新版载荷。

```bash
cd backend
# 使用第 3 节已配置的 .env，不覆盖已有环境文件。
uv sync --dev
uv run alembic upgrade head
```


来源扫描只发起普通 HTTP 请求，不需要浏览器或模型密钥；Worker 的翻译循环需要默认模型凭证。
迁移历史已合并为单一基线：2026-11-10 之前创建的数据库无法升级，需要新建数据库后再执行迁移。

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

## 6. 交付前清单

- [ ] 开始安装前已让用户确认目标系统、访问范围、体验入口和可选能力。
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
- 翻译、X 和 YouTube 分别是已启用、降级还是关闭，并说明实际影响；
- 对用户所选能力执行了哪些验收、结果如何，模型 key 是否做过获授权的在线请求验证；
- 尚需用户完成的操作和已知限制。

用户明确放弃的可选能力不算部署失败，但必须在总结中列明。任何用户已选择的必需组件或
验收失败时，不得宣布部署完成；可以准确说明当前已完成部分和剩余阻塞项。
