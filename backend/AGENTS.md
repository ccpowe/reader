# 后端工作约定

适用于 `backend/`，包含 API、Worker、管理命令和数据库迁移。先读取并遵守[根目录约定](../AGENTS.md)。本文维护后端目录导航、环境与命令、后端开发及验证做法；跨端协作、接口生成、文档审查和共享资源调度由根目录统一维护。

后端使用 Python、FastAPI、SQLAlchemy 和 PostgreSQL。API 与 Worker 的职责、数据归属和运行边界见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)；本文不另写架构说明、完整协议或部署历史。

## 目录导航

以下路径相对于 `backend/`，导航维护到模块与关键入口。

| 路径 | 内容与入口 |
| --- | --- |
| `src/app/main.py`、`src/app/api/router.py` | FastAPI 应用与路由装配 |
| `src/app/api/`、`src/app/services/` | HTTP 路由、请求／响应模型与业务服务 |
| `src/app/core/` | 配置和认证；入口为 `settings.py`、`auth.py`、`server_access.py` |
| `src/app/storage/` | 数据库会话、业务与账户模型、锁和 schema 就绪检查；入口为 `database.py`、`models.py`、`auth_models.py`、`schema.py` |
| `src/app/workers/` | 后台循环，入口为 `main.py`；`sync.py` 扫描来源，`candidates.py` 处理候选，`health.py` 维护健康状态 |
| `src/app/ingestion/` | 内容源接入、抓取与提取；`sources/` 为来源适配，`web_rules.py` 为规则 schema 与真实执行反馈，`feed_discovery.py` 为 feed 探测，`browser_runtime.py` 为可选浏览器进程与隔离入口 |
| `src/app/web_rule_agent/`、`src/app/workers/web_rules.py` | 网页规则 Agent 与 Worker 交接；`runtime.py` 为执行入口，`cli_browser.py` 运行任务内持久 CLI 探索，`memory.py` 管理工作证据、执行结果与读取进度 |
| `src/app/browser_tasks/` | 可选容器浏览器任务的内部协议、Worker 客户端、adapter、控制器和回收器；公共 DTO 入口为 `protocol.py` |
| `src/app/translation/`、`src/app/llm/` | 翻译需求、缓存与执行，以及模型客户端构造；实时入口为 `translation/realtime.py`，公共模型入口为 `llm/factory.py` |
| `src/app/admin/cli.py`、`src/app/ingestion/web_rule_cli.py` | 管理与网页规则诊断命令 |
| `migrations/` | Alembic 迁移，版本位于 `versions/` |
| `tests/`、`scripts/` | 后端测试及接口导出、数据库验证等脚本；标记与测试配置见 `pyproject.toml` |
| `pyproject.toml`、`uv.lock`、`.env.example`、`compose.postgres.yml` | 依赖、命令注册、环境示例与本地数据库配置 |

## 环境与命令

使用 Python 3.12+ 和 uv，依赖及 CLI 入口以 `pyproject.toml` 为准，安装沿用 `uv.lock`；Python lint／format 使用 Ruff。

启动 API 和 Worker 前，按 `.env.example` 与 `src/app/core/settings.py` 准备运行配置及已迁移的 PostgreSQL。需要部署操作时参阅[部署说明](../docs/deployment.md)。以下命令在 `backend/` 执行；验证与数据库操作遵守[共享资源调度](../AGENTS.md#验证与资源调度)。

| 用途 | 命令 |
| --- | --- |
| 安装后端与开发依赖 | `uv sync --dev` |
| 启动本地 PostgreSQL，先核对本地配置 | `docker compose -f compose.postgres.yml up -d` |
| 将选定数据库迁移到当前版本，先核对目标 | `uv run alembic upgrade head` |
| 启动 API | `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| 启动 Worker | `uv run reader-worker` |
| 指定测试 | `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/<测试文件>.py -m 'not postgres and not live_provider'` |
| 常规后端测试 | `PYTHON_DOTENV_DISABLED=1 uv run pytest -q -m 'not postgres and not live_provider'` |
| lint | `uv run ruff check src tests` |
| 导入／检查 Codex 订阅登录，不依赖数据库 | `uv run reader-admin codex-auth import --source /path/to/dedicated/auth.json` / `uv run reader-admin codex-auth status` |
| 格式化本次修改的 Python 文件 | `uv run ruff format <文件路径>` |
| 生成容器浏览器内部契约 | `uv run python scripts/export_browser_openapi.py` |
| 检查容器浏览器内部契约 | `uv run python scripts/export_browser_openapi.py --check` |
| 临时 PostgreSQL 集成检查 | `bash scripts/test-postgres-migrations.sh` |

接口快照与前端类型的生成、检查统一见[根目录接口规则](../AGENTS.md#环境与常用命令)，从仓库的 `mobile/` 执行对应命令，不手改生成文件。

## 后端开发约定

- 修改 HTTP 行为时一起核对路由、请求／响应模型、业务服务和调用方。复用已有认证依赖、用户范围过滤和错误处理；接口字段只在源码声明中维护，按根目录规则生成契约。
- 修改 API 与 Worker 共用的业务模块时核对两侧调用、异常传播和资源生命周期。当前模块调用和事务范围以代码为准，不仅凭目录名假定独立服务或严格分层；架构依据见[模块职责与依赖](../docs/ARCHITECTURE.md#模块职责与依赖)。
- 改动持久化行为时核对 ORM、数据库约束、迁移与 `src/app/storage/schema.py` 的兼容要求。已用于部署的迁移通过新迁移演进，不为新行为改写旧迁移；去重、删除或归并同时检查相关数据引用，依据见[数据归属](../docs/ARCHITECTURE.md#数据归属)。
- 修改后台任务时检查领取条件、事务、租约、重试与重启恢复，以及 Worker 心跳和错误传播；不要只验证正常执行一次。外部调用涉及连接或锁占用时，核对等待期间的资源生命周期。
- 修改来源抓取、网页规则或模型工具时，同时核对 URL／传输限制、规则验证和 Worker 激活路径；复用现有抓取、真实 scanner 和版本／租约交接入口，依据见[必须保持的边界](../docs/ARCHITECTURE.md#必须保持的边界)。
- 修改浏览器路径时同时核对 Agent 探索、规则试跑和 Worker 周期扫描共用的 Lightpanda 传输、进程清理及环境身份；保留静态 HTTP，正常路径不回退 Chromium。引擎安装及配置见[部署说明](../docs/deployment.md#6-后端-web-规则-agent)，执行遵守根目录的[共享资源调度](../AGENTS.md#验证与资源调度)。
- 修改容器浏览器任务的控制或数据接口时，以 `browser_tasks/protocol.py` 和两个 FastAPI app 为单一来源，重新生成 `contracts/browser-controller-openapi.json` 与 `contracts/browser-adapter-openapi.json`；它们是 Reader 内部部署接口，不并入公开 Reader API。
- `inspect_web_page(argv)` 使用 `web_rule_agent/cli_browser.py` 的任务内持久 CLI 会话；其固定版 CLI 和 Lightpanda 要求见[部署说明](../docs/deployment.md#6-后端-web-规则-agent)。修改该组件时一起检查 namespace 内客户端／daemon、受控 CDP bridge 与 Lightpanda 的回收，以及最终输出限长后的可见链接授权。
- 新增或修改运行配置时核对 `src/app/core/settings.py`、`.env.example`、使用方及配置测试。模型客户端的公共配置复用 `src/app/llm/factory.py`，调用方的职责与限额仍按架构和实际实现核对。

## 验证与审查重点

- 从受影响的 `tests/` 行为检查开始；涉及前端可见行为时，按根目录约定同时验证调用方和契约。共同完成目标验收与文档一致性审查，不能用后端测试通过代替跨端验收。
- 常规测试排除 `postgres` 与 `live_provider`。前者使用明确的临时数据库和专用集成脚本；后者按测试的显式启用条件运行。不要为常规检查连接真实供应商或用户数据库。
- `scripts/test-postgres-migrations.sh` 需要 Docker，会创建并清理自己的临时数据库。需要指定测试文件时，保留 `tests/test_postgres_migrations.py` 作为新数据库初始化入口；不得绕过脚本的测试数据库身份检查。
- 普通 pytest 命令设置 `PYTHON_DOTENV_DISABLED=1`，避免第三方导入隐式加载本地 dotenv；应用自身显式配置的设置文件仍按实现读取，不能把该标志当作所有运行配置都已隔离。
- 数据库相关行为需要对应 PostgreSQL 检查作为证据；没有执行时明确未验证。外部模型、采集与部署验收也分别报告实际执行范围，单元测试不能证明线上服务可用。

## 本文件更新时机

后端入口、环境、命令、开发约定或验证方式变化时更新本文。共享规则修改根目录 AGENTS；模块职责、数据归属与运行边界修改 ARCHITECTURE；重要决定记录 ADR，不在这里累积迁移叙事或任务验收报告。
