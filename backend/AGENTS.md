# 后端工作约定

适用于 `backend/`，包含 API、Worker、管理命令和数据库迁移。先读取并遵守[根目录约定](../AGENTS.md)。本文维护后端目录导航、环境与命令、后端开发及验证做法；跨端协作、接口生成、文档审查和共享资源调度由根目录统一维护。

后端使用 Python、FastAPI、SQLAlchemy 和 PostgreSQL。API 与 Worker 的职责、数据归属和运行边界见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)；本文不另写架构说明、完整协议或部署历史。

## 目录导航

以下路径相对于 `backend/`，导航维护到模块与关键入口。

| 路径 | 内容与入口 |
| --- | --- |
| `src/reader/main.py` | FastAPI 应用装配（只做装配，不含业务）；`create_app` 为工厂 |
| `src/reader/worker.py`、`src/reader/cli.py` | `reader-worker` 后台循环（来源扫描、标题翻译、每日清理），`reader-admin` 运维命令（重置密码、MCP 令牌、Codex 凭据、翻译用量） |
| `src/reader/core/` | 配置 `settings.py`、数据库 `db.py`、统一错误 `errors.py`、部署令牌门禁 `server_access.py`、分页游标与限流 |
| `src/reader/models.py` | 全部 14 张表；迁移位于 `migrations/versions/` |
| `src/reader/accounts/` | 注册、登录、会话与刷新令牌、资料与头像；`auth.py` 提供各路由共用的 `current_user` |
| `src/reader/subscriptions/` | 订阅用例与路由、来源图标中转 |
| `src/reader/ingestion/` | 来源扫描 `scanner.py`、适配器 `adapters/`（RSS、YouTube、X 经 Scweet、Reddit）、添加网站时的 RSS 发现 `feed_discovery.py`、公网目的地校验与有界读取 `url_safety.py`、每日保留清理 `retention.py` |
| `src/reader/reading/` | 信息流、更新统计、单篇文章与收藏的查询和路由 |
| `src/reader/boards/` | 榜单快照（按需抓取）与上游读取 `fetchers.py`、从榜单收藏 |
| `src/reader/translation/` | 唯一的翻译服务 `service.py`（缓存、领取、调用模型、写回）、引擎目录 `engines.py`、全局字符额度 `budget.py`、后台标题翻译 `titles.py`、Codex 凭据 `codex_auth.py`、`POST /translations` 与翻译偏好 `api.py` |
| `src/reader/mcp/` | API 进程内的 `/mcp` 端点；`server.py` 定义工具，`transcripts.py` 读取公开 YouTube 字幕 |
| `src/reader/system/` | 服务发现、健康与就绪检查、Worker 心跳 |
| `tests/reader/`、`tests/` | 新后端测试；顶层只剩 Docker 入口、Codex 凭据卷和 Scweet 协议检查 |
| `scripts/` | 接口导出 `export_openapi.py`、临时 PostgreSQL 测试 `test-postgres-migrations.sh` |
| `pyproject.toml`、`uv.lock`、`.env.example`、`compose.postgres.yml` | 依赖与命令注册、直接运行时的配置样例、本地开发数据库 |

## 环境与命令

使用 Python 3.12+ 和 uv，依赖及 CLI 入口以 `pyproject.toml` 为准，安装沿用 `uv.lock`；Python lint／format 使用 Ruff。

启动 API 和 Worker 前，按 `.env.example` 与 `src/reader/core/settings.py` 准备运行配置及已迁移的 PostgreSQL。需要部署操作时参阅[部署说明](../docs/deployment.md)。以下命令在 `backend/` 执行；验证与数据库操作遵守[共享资源调度](../AGENTS.md#验证与资源调度)。

| 用途 | 命令 |
| --- | --- |
| 安装后端与开发依赖 | `uv sync --dev` |
| 启动本地 PostgreSQL，先核对本地配置 | `docker compose -f compose.postgres.yml up -d` |
| 将选定数据库迁移到当前版本，先核对目标 | `uv run alembic upgrade head` |
| 启动 API | `uv run uvicorn reader.main:create_app --factory --host 127.0.0.1 --port 8000` |
| 启动 Worker | `uv run reader-worker` |
| 签发／撤销某个账户的 MCP 令牌 | `uv run reader-admin mcp-token create <邮箱>` / `uv run reader-admin mcp-token revoke <邮箱>`；令牌只显示一次，用法见 [README](../README.md) |
| 指定测试 | `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/<测试文件>.py -m 'not postgres and not live_provider'` |
| 常规后端测试 | `PYTHON_DOTENV_DISABLED=1 uv run pytest -q -m 'not postgres and not live_provider'` |
| lint | `uv run ruff check src tests` |
| 导入／检查 Codex 订阅登录，不依赖数据库 | `uv run reader-admin codex-auth import --source /path/to/dedicated/auth.json` / `uv run reader-admin codex-auth status` |
| 格式化本次修改的 Python 文件 | `uv run ruff format <文件路径>` |
| 临时 PostgreSQL 集成检查（不带参数时跑全部标记为 `postgres` 的测试） | `PYTHON_DOTENV_DISABLED=1 bash scripts/test-postgres-migrations.sh [tests/reader/<测试文件>.py]` |

接口快照与前端类型的生成、检查统一见[根目录接口规则](../AGENTS.md#环境与常用命令)，从仓库的 `mobile/` 执行对应命令，不手改生成文件。

## 后端开发约定

- 修改 HTTP 行为时一起核对路由、请求／响应模型、业务服务和调用方。复用已有认证依赖、用户范围过滤和错误处理；接口字段只在源码声明中维护，按根目录规则生成契约。
- 修改 API 与 Worker 共用的业务模块时核对两侧调用、异常传播和资源生命周期。当前模块调用和事务范围以代码为准，不仅凭目录名假定独立服务或严格分层；架构依据见[模块职责与依赖](../docs/ARCHITECTURE.md#模块职责与依赖)。
- 改动持久化行为时核对 `src/reader/models.py`、数据库约束、迁移与 `/ready` 的 schema 版本检查。迁移从单一基线 `migrations/versions/0001_baseline` 开始，之后的结构变化通过新迁移演进；去重、删除或归并同时检查相关数据引用，依据见[数据归属](../docs/ARCHITECTURE.md#数据归属)。
- 修改后台任务时检查领取条件、事务、租约、重试与重启恢复，以及 Worker 心跳和错误传播；不要只验证正常执行一次。外部调用涉及连接或锁占用时，核对等待期间的资源生命周期。
- 修改来源抓取时，同时核对 URL／传输限制与真实 scanner 路径；复用 `url_safety.py` 的公网校验与有界读取，依据见[必须保持的边界](../docs/ARCHITECTURE.md#必须保持的边界)。
- 新增或修改运行配置时核对 `src/reader/core/settings.py`、`.env.example`、Docker 部署的 `compose.yaml` 与 `.env.docker.example`、使用方及配置测试。翻译模型统一经 `src/reader/translation/engines.py` 和 `service.py`，不另建模型客户端。

## 验证与审查重点

- 从受影响的 `tests/` 行为检查开始；涉及前端可见行为时，按根目录约定同时验证调用方和契约。共同完成目标验收与文档一致性审查，不能用后端测试通过代替跨端验收。
- 常规测试排除 `postgres` 与 `live_provider`。前者使用明确的临时数据库和专用集成脚本；后者按测试的显式启用条件运行。不要为常规检查连接真实供应商或用户数据库。
- `scripts/test-postgres-migrations.sh` 需要 Docker，会创建并清理自己的临时数据库。可以指定测试文件；不得绕过脚本的测试数据库身份检查。
- 普通 pytest 命令设置 `PYTHON_DOTENV_DISABLED=1`，避免第三方导入隐式加载本地 dotenv；应用自身显式配置的设置文件仍按实现读取，不能把该标志当作所有运行配置都已隔离。
- 数据库相关行为需要对应 PostgreSQL 检查作为证据；没有执行时明确未验证。外部模型、采集与部署验收也分别报告实际执行范围，单元测试不能证明线上服务可用。

## 本文件更新时机

后端入口、环境、命令、开发约定或验证方式变化时更新本文。共享规则修改根目录 AGENTS；模块职责、数据归属与运行边界修改 ARCHITECTURE；重要决定记录 ADR，不在这里累积迁移叙事或任务验收报告。
