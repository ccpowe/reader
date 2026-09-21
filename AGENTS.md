# 仓库工作约定

Reader 是前后端分离的信息聚合与阅读应用。本文供人和 AI 共同使用，负责全仓库共享约定、跨端协作、资料入口及交付要求。

## 适用范围与分工

| 文件 | 适用范围 | 负责维护 |
| --- | --- | --- |
| 本文件 | 整个仓库和所有参与任务的 Agent | 仓库级导航、跨端协作、共享资源限制、接口生成与文档审查规则 |
| [mobile/AGENTS.md](mobile/AGENTS.md) | `mobile/` 前端 | 前端目录与关键入口、环境与命令、界面和客户端开发约定、前端验证 |
| [backend/AGENTS.md](backend/AGENTS.md) | `backend/` 后端，包含 API 与 Worker | 后端目录与关键入口、环境与命令、接口与持久化开发约定、后端验证 |

修改前先读本文件，再读目标目录实际存在的 `AGENTS.md`；跨前后端任务同时读取两侧约定。子目录文件继承共享规则，只补充本目录的具体做法。共享规则变化在本文件统一修改，不在子目录复制或另设一套；发现冲突时指出并核对适用范围及已确认要求。

三份 AGENTS 都记录开发约定。系统职责与数据归属由 `docs/ARCHITECTURE.md` 维护，协议字段由接口定义维护。

## 目录导航

| 路径 | 内容与入口 |
| --- | --- |
| `mobile/` | Expo / React Native 前端；详细导航和命令见 [前端约定](mobile/AGENTS.md) |
| `backend/` | Python / FastAPI 后端，API 与 Worker 分别运行；详细导航和命令见 [后端约定](backend/AGENTS.md) |
| `contracts/` | 系统边界协议，当前包含生成的 Reader API `openapi.json` |
| `architecture/` | C4 DSL 源文件；`html/` 为不提交的本地预览产物 |
| `docs/` | 部署说明、系统架构及 README 所需素材 |
| `services/` | 核心进程的 systemd／Caddy 模板及可选 Scweet 服务 |
| `compose.yaml`、`docker-init.sh` | 完整 Docker 部署拓扑与受限的初始化、升级、停止和状态入口 |
| `.github/workflows/` | CI 配置，目前包含网页翻译运行时检查 |

根目录导航维护到主要工作区域；模块与关键入口由子目录约定维护，不建立逐文件说明清单。

## 按任务读取资料

| 要了解或修改什么 | 从哪里开始 |
| --- | --- |
| 项目介绍与基本使用 | [README.md](README.md) |
| 开发约定与命令 | 本文件和目标目录的 AGENTS |
| 模块职责、数据归属、依赖约束或关键运行流程 | [ARCHITECTURE.md](docs/ARCHITECTURE.md)，随后核对相关代码 |
| 系统、运行单元、重要组件及其关系 | [architecture/workspace.dsl](architecture/workspace.dsl)，包含 C1–C3 视图；C4 直接查看源码 |
| 前后端接口 | [contracts/openapi.json](contracts/openapi.json) 查看协议；修改入口为后端路由及请求／响应模型 |

历史文档是检索线索。文档与代码不一致时，区分当前实现、已确认的目标和未知项，判断是文档过期、实现偏离还是目标变化；不要把旧方案当作当前约束，也不要通过修改文档把实现缺陷变成预期行为。

## 公开仓库边界

公开树保留产品代码与必需配套资料：`mobile/`、`backend/`、`contracts/`、
`architecture/`、`docs/`、`services/`、`.github/`、`.codex/` 和 `.serena/`。
`workspaces/` 的网页规则实验不是产品运行或发布所需内容，不纳入公开树；
后续实验应放在独立私有仓库或本地目录。新增顶层目录前先确认它是产品、构建、部署或维护的必需输入。

## 开发与审查

1. 根据需求明确本次目标和可观察的验收条件，定位负责模块，读取相关实现、接口、架构约束和测试后再修改。优先复用已有概念与组件。
2. 涉及模块边界、数据归属、跨模块依赖或基础设施的变化，先说明影响与取舍；需要用户决定且尚未授权的事项先讨论。已获授权的工作继续执行，不重复请求确认。
3. 行为跨越前后端接口时，同时检查调用方与被调用方，包括错误处理、认证和兼容性，不能只让单侧测试通过。
4. 保留用户及其他任务已有的修改。使用 Subagent 时明确任务、文件所有权和验证范围，主 Agent 负责整合及共享执行资源。

交付前同时审查两项：**实现是否完成已确认目标并遵守相关约束；文档、架构模型和接口定义是否与最终实现同步。** 对需要更新的资料随实现一起修改；无需更新时说明依据，不为每次改动修改全部文档。交付说明包含结果、验证证据、文档同步情况和未解决问题；测试通过不能代替目标验收，链接有效不能代替内容一致性审查。

代码定位遵循工作区的 [code-retrieval](.codex/skills/code-retrieval/SKILL.md) 约定：未知入口按行为检索；符号定义、引用和声明优先用 Serena；路径、字符串和配置用 `rg`。首次使用 Serena 时核对 `active_project_path`，需要时激活当前工作区并读取 `initial_instructions`。不自动全量索引或启动额外 Serena 服务。

不要把密钥、令牌、Cookie 或私有连接信息写入代码、文档和日志。

## 环境与常用命令

安装、启动和本地检查命令分别维护在 [前端约定](mobile/AGENTS.md#环境与命令) 和 [后端约定](backend/AGENTS.md#环境与命令)。本文件只维护涉及跨端产物和仓库架构模型的命令。

| 工作目录 | 用途 | 命令 |
| --- | --- | --- |
| `mobile/` | 更新接口快照和前端类型 | `pnpm run generate:api-types` |
| `mobile/` | 检查接口快照和前端类型 | `pnpm run test:api-contract` |
| 仓库根目录 | 校验 C4 模型 | `java -jar /path/to/structurizr.war validate -workspace architecture/workspace.dsl` |
| 仓库根目录 | 导出可浏览的架构图 | `java -jar /path/to/structurizr.war export -workspace architecture/workspace.dsl -format static -output architecture/html` |
| 仓库根目录 | 初始化 Docker 私有配置 | `cp .env.docker.example .env.docker && ./docker-init.sh init` |
| 仓库根目录 | 构建或升级 Docker 服务 | `./docker-init.sh up`；可选 X 使用 `--with-x`，明确关闭使用 `--without-x` |
| 仓库根目录 | 查看或停止 Docker 服务 | `./docker-init.sh status` / `./docker-init.sh down` |

接口以 FastAPI 路由及请求／响应模型中的声明为编写入口。生成命令通过 `backend/scripts/export_openapi.py` 导出 `contracts/openapi.json`，并从同一份 OpenAPI 生成 `mobile/lib/generated/api.ts`；需要已安装的前端依赖和 `backend/.venv`。两份生成文件均纳入版本控制，不要手改或另写一份协议。路径、参数、响应、认证、错误声明或说明变化后，重新生成、运行契约检查，并将生成变化随实现一起提交。检查命令只校验是否同步，不会修复文件，也不代替兼容性审查与行为测试。

架构图命令使用 [Structurizr 官方二进制](https://docs.structurizr.com/binaries) 和 Java 21，`/path/to/structurizr.war` 替换为本地工具路径；导出后用浏览器打开 `architecture/html/index.html`。修改 DSL 后重新导出并刷新页面。只维护 DSL 源文件，不手改或提交导出物；`architecture/html/` 已加入 Git 忽略。

## 验证与资源调度

工作站曾因并发验证内存耗尽而卡死或终止 IDE。以下约束覆盖整个工作区和所有 Subagent：

- **重任务串行执行**：浏览器测试、Expo 导出／构建、Metro 打包、TypeScript、lint、完整测试和 Docker 数据库集成检查不能彼此重叠。多 Agent 共用执行预算；Subagent 启动重任务前向主 Agent 报告命令并等待调度许可，完成后报告实际结果。
- Playwright 固定一个 worker；Metro／Expo 使用 `--max-workers 1`。启动其他重验证前，停止本任务启动的 Metro，需要设备预览时再恢复。
- 重任务前检查可用内存和本任务残留进程。存在内存压力或 IDE 被终止的情况时，暂停启动重任务，先检查并清理本任务确认拥有的进程；不结束用户 IDE、浏览器或其他会话服务，不通过加 worker 或堆内存强行推进。
- 从受影响行为的针对性检查开始。共享翻译、导航、连接或 API 行为变化时，按前端约定运行一次完整检查；失败后先重跑受影响检查，只在新变化或未解决回归需要时重复完整检查。
- 仅文档调整核对路径、命令来源、链接和 diff，无需启动应用、完整测试或构建。进程退出、内存中断和不完整日志都不能算验证通过；说明实际结果和未验证项。

## 文档维护

- 默认文档范围是 README、根目录及按需设置的前后端 AGENTS、ARCHITECTURE、架构模型和接口定义。其他文档类别按用户明确需要再增加，不为任务自动生成计划、总结或验收 Markdown。
- README 在用户可见能力、使用方式或支持范围变化时更新；ARCHITECTURE 在职责、数据归属、依赖约束或关键运行流程变化时更新。
- 本文件在共享规则、跨端约定、仓库级入口或共享命令变化时更新；前后端各自的环境、命令、导航和开发约定变化只更新对应子目录 AGENTS。同一详细规则只在负责它的位置维护，其他资料引用。
- 架构结构变化更新模型，接口变化更新契约，重要决定才记录 ADR；普通实现修改不要求更新全部文档。
- ADR 草稿可持续修改；已接受决定发生实质变化时新增记录并关联被替代项。只保留有依据的决策理由，不根据现有代码补造历史决定或用户批准。
- 日常开发由适用的 AGENTS 指导，不再使用旧 `development-workflow` 编排流程。
