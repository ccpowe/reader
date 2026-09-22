# Reader Docker Compose 部署

本文说明 Reader 后端的 Docker Compose 生产部署、配置、升级和日常运维。Docker 部署的维护入口是
仓库根目录的 `docker-init.sh`；不要直接运行裸 `docker compose up`，否则会绕过密钥封装、数据库迁移、
浏览器任务 drain 和镜像身份切换。

本文只覆盖服务端。Android 客户端在运行时填写服务地址和连接 token，不需要把服务端密钥编译进 APK。
宿主机 Python／systemd 部署及其他开发方式见[综合部署指南](deployment.md)。

## 1. 运行组成与前提

启用 X 时，正常运行包含 6 个长期服务：

| 服务 | 职责 | 对宿主机暴露端口 |
| --- | --- | --- |
| `postgres` | 保存账号、订阅、内容、翻译和任务状态 | 无 |
| `api` | Reader HTTP API 和健康检查 | `READER_API_BIND_HOST:READER_API_PORT` |
| `worker` | 来源同步、翻译和网页规则任务 | 无 |
| `browser-manager` | 创建、控制并回收短期浏览器任务 | 无 |
| `browser-reaper` | 回收超时或遗留的浏览器任务资源 | 无 |
| `scweet` | 可选的 X 内容采集 | 无 |

`pg-init`、`migrate`、`state-init` 和 `network-init` 是 `up` 期间运行的一次性任务，不是常驻服务。
关闭 X 后不运行 `scweet`，其余 5 个长期服务不变。

部署主机需要：

- Linux、Git、Bash、Python 3、`flock`、`curl`；
- Docker Engine，以及包含 `docker compose` 子命令的 Compose v2；
- 当前部署用户可访问 `/var/run/docker.sock`；
- 足够的磁盘空间用于源码、镜像、构建缓存和数据卷；
- 能访问所选模型供应商、内容来源及 Docker 构建依赖。

当前方案尚未验收 rootless Docker 和 user namespace remap。构建 Reader、Browser 和可选 Scweet
镜像会消耗较多内存与磁盘；在资源有限的服务器上不要并行执行构建或部署。

## 2. 配置文件与密钥边界

Docker 部署把公开配置、长期输入、运行时 secret 和内部状态分开保存：

```text
.env.docker                 # 非秘密配置，可从 .env.docker.example 复制
.docker/
├── inputs/                 # 管理员填写的长期凭证，0600
├── secrets/                # 容器实际挂载的封装值，0440
├── runtime/                # 部署 ID、镜像 ID 和 X 启用状态，0600
└── operation.lock          # 串行化 init/up/down/status/token
```

`.env.docker`、`.docker/` 都被 Git 忽略。不要删除、提交、通过聊天发送或复制到不受信任的主机。
其中 `.docker/runtime/base.env` 保存 Compose 项目名和部署身份，`.docker/runtime/active.env` 保存当前
镜像的精确 ID；它们虽然不是业务凭证，也必须和该部署一起保留。

### 公开配置 `.env.docker`

先复制示例：

```bash
cp .env.docker.example .env.docker
```

常用配置如下：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `READER_API_BIND_HOST` | `127.0.0.1` | API 在宿主机的监听地址；反向代理同机部署时保留默认值 |
| `READER_API_PORT` | `8000` | API 在宿主机的端口 |
| `APP_PUBLIC_API_BASE_URL` | `http://127.0.0.1:8000` | 客户端可访问的完整 API 根地址；公网部署应填写 HTTPS 地址 |
| `APP_CORS_ORIGINS` | `[]` | JSON 数组；仅加入实际需要访问 API 的 Web origin |
| `APP_LOG_LEVEL` | `INFO` | 后端日志级别 |
| `APP_TRANSLATION_DEFAULT_ENGINE_ID` | `deepseek-v4-flash` | 默认翻译引擎，也可设为 `openrouter-minimax-m3` 或 `disabled` |
| `APP_DEEPSEEK_API_BASE` | `https://api.deepseek.com` | DeepSeek 兼容 API 根地址 |
| `APP_DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek 实际模型名 |
| `APP_OPENROUTER_API_BASE` | `https://openrouter.ai/api/v1` | OpenRouter API 根地址 |
| `APP_OPENROUTER_MODEL` | `minimax/minimax-m3` | OpenRouter 实际模型名 |
| `APP_WEB_RULE_AGENT_ENGINE_ID` | `disabled` | 网页规则 Agent；可设为上述两个引擎 ID |
| `APP_YOUTUBE_DAILY_QUOTA_SOFT_LIMIT` | `8000` | YouTube Data API 每日软限额 |

翻译和网页规则任务的限额使用 `.env.docker.example` 中的默认值：

| 配置 | 默认值 | 作用 |
| --- | ---: | --- |
| `APP_TRANSLATION_QUOTA_REQUESTS_PER_MINUTE` | `30` | 翻译请求分钟限额 |
| `APP_TRANSLATION_QUOTA_USER_MISS_CHARS_PER_MINUTE` | `120000` | 单用户未命中缓存字符分钟限额 |
| `APP_TRANSLATION_QUOTA_GLOBAL_MISS_CHARS_PER_MINUTE` | `500000` | 全局未命中缓存字符分钟限额 |
| `APP_WEB_RULE_AGENT_MAX_OUTPUT_TOKENS` | `4096` | 规则 Agent 单次最大输出 token |
| `APP_WEB_RULE_AGENT_MODEL_TIMEOUT_SECONDS` | `60` | 模型调用超时 |
| `APP_WEB_RULE_AGENT_VALIDATE_TIMEOUT_SECONDS` | `180` | 规则验证超时 |
| `APP_WEB_RULE_AGENT_MAX_TOOL_RESULT_BYTES` | `20480` | 单个工具结果上限 |
| `APP_WEB_RULE_AGENT_MAX_DIAGNOSTIC_BYTES` | `262144` | 单任务诊断信息上限 |

不要把 API key、Cookie、数据库密码、连接 token 或 JWT 密钥写入 `.env.docker`。

### 私密输入 `.docker/inputs/`

运行初始化命令后，脚本会创建以下 `0600` 文件：

| 文件 | 何时填写 | 消费方 |
| --- | --- | --- |
| `deepseek-api-key` | 使用 DeepSeek 翻译或规则 Agent 时 | API、Worker |
| `openrouter-api-key` | 使用 OpenRouter 翻译或规则 Agent 时 | API、Worker |
| `youtube-data-api-key` | 需要 YouTube Data API 完整数据时，可选 | Worker |
| `scweet-cookies.json` | 启用 X 时必需 | Scweet |

用服务器本地编辑器写入实际值，不要让值出现在命令历史或部署日志中。未使用的 key 文件保持为空。
模型 key 必须与所选引擎匹配：`deepseek-v4-flash` 需要 DeepSeek key，
`openrouter-minimax-m3` 需要 OpenRouter key。

`init` 还会自动生成数据库密码、客户端连接 token、JWT 签名密钥、Browser Manager token 和
Scweet 服务 token。生成结果位于 `.docker/secrets/`，无需手工填写。每次 `up` 会重新校验输入文件
的 owner、普通文件类型和 `0600` 权限，再原子封装为容器读取的 `0440` secret。

## 3. 首次部署

在仓库根目录执行：

```bash
cp .env.docker.example .env.docker
# 编辑非秘密配置
./docker-init.sh init
# 在服务器本地编辑 .docker/inputs/ 中需要的凭证
./docker-init.sh up
./docker-init.sh status
./docker-init.sh token
```

`up` 会依次完成：

1. 校验配置、权限和输入，生成或保留部署身份与内部 secret；
2. 从当前工作树构建 Reader、Browser，以及按需构建 Scweet 候选镜像，并把当前 commit 写入镜像 tag；
3. 停止旧 Worker，由旧 Browser Manager drain 本部署的短期浏览器、adapter 和任务卷；
4. 启动 PostgreSQL，执行 `alembic upgrade head`，初始化 Browser 状态卷和网络；
5. 记录候选镜像的精确 Image ID；
6. 依次启动并检查 Browser Manager、API、可选 Scweet，最后启动 Worker。

脚本使用文件锁串行执行。构建、drain、迁移或健康检查失败时应先查看错误，不要同时从另一个
终端再次运行 `up`，也不要用裸 Compose 命令强行覆盖状态。

### 网络暴露

默认 API 只监听 `127.0.0.1:8000`，适合同机反向代理。公网部署应使用域名和 HTTPS，并让
`APP_PUBLIC_API_BASE_URL` 与客户端实际访问地址一致。只有已经配置主机防火墙或明确需要可信局域网
直连时，才把 `READER_API_BIND_HOST` 改为 `0.0.0.0`。

把 `0.0.0.0:8000` 直接暴露到公网只提供明文 HTTP，不适合作为长期生产入口。云安全组和主机
防火墙也必须只开放预期端口；PostgreSQL、Browser Manager 和 Scweet 不应映射到宿主机。

### 客户端连接

`./docker-init.sh token` 会把客户端连接 token 输出到当前终端。只把它提供给获准连接的用户，
不要写入 README、Issue 或日志。客户端填写：

- 服务地址：`APP_PUBLIC_API_BASE_URL` 对应的地址；
- 连接 token：`./docker-init.sh token` 的输出。

更换服务目录或升级镜像时，只要保留 `.docker/`、数据库卷和公开地址，客户端无需重新配置。

## 4. 可选 X / Scweet

X 采集依赖非官方 Scweet 和 X 网页 Cookie，可能触发限流、登录验证或账号限制。只使用专门的小号，
并自行评估平台政策与合规风险。

将 Cookie 写入 `.docker/inputs/scweet-cookies.json`：

```json
[
  {
    "cookies": {
      "auth_token": "在服务器本地填写"
    }
  }
]
```

然后运行：

```bash
./docker-init.sh up --with-x
```

脚本会校验 JSON 至少包含一个非空的 `cookies.auth_token`，构建并健康检查 Scweet。启用状态保存在
`.docker/runtime/x-enabled`，以后普通 `up` 会沿用。关闭 X 但保留 Scweet 数据卷：

```bash
./docker-init.sh up --without-x
```

## 5. 状态、健康和日志

```bash
./docker-init.sh status
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8000/worker-ready
```

- `/ready` 验证 API 和数据库 schema；
- `/worker-ready` 验证必需 Worker 循环。没有配置与默认翻译引擎匹配的 key 时，它会返回 503；
  这表示翻译未就绪，不等于 API 故障；
- `status` 应显示 PostgreSQL、API 和 Browser Manager 为 `healthy`；启用 X 时 Scweet 也应为
  `healthy`。Worker 和 Browser Reaper 没有 Compose healthcheck，但必须处于 `Up`。

查看日志时先由 `status` 确认 Compose 项目和容器名，再按需查看单个容器：

```bash
docker logs --tail 200 <api-container>
docker logs --tail 200 <worker-container>
docker logs --tail 200 <browser-manager-container>
docker logs --tail 200 <scweet-container>
```

日志可能含外部站点 URL、账号标识或错误上下文，不要把未经检查的完整日志公开粘贴。

## 6. 升级、停止与备份

### 原目录升级

在确认工作树没有未审核修改，并核对代码版本、配置和备份后，于同一部署目录执行：

```bash
git fetch --all --prune
# 按发布流程切换到已经审核的 commit 或 tag
./docker-init.sh up
./docker-init.sh status
```

普通 `up` 沿用当前 X 启用状态；需要明确改变时使用 `--with-x` 或 `--without-x`。`up` 会重新构建
候选镜像、drain 旧浏览器任务并执行迁移，因此不是零停机升级。

### 停止与恢复

```bash
./docker-init.sh down
./docker-init.sh up
```

`down` 会先 drain 浏览器任务，再停止并移除 Compose 容器和网络；PostgreSQL、Browser 状态和
Scweet 状态卷以及 `.docker/` 配置都会保留。不要使用 `docker compose down -v`，它会删除数据卷。

### 升级前备份

确认没有其他 `init`、`up`、`down`、`status` 或 `token` 操作正在执行后，至少备份以下内容，
并限制备份文件权限：

1. PostgreSQL 的一致性逻辑备份（例如从当前 PostgreSQL 容器运行 `pg_dump -Fc`）；
2. 整个 `.docker/` 和 `.env.docker`；
3. 启用 X 时的 Scweet 状态卷；
4. 服务器自定义的 Compose override、反向代理和防火墙配置。

Browser 状态卷只保存浏览器任务协调状态，不能代替数据库备份。恢复演练需要验证数据库、连接 token、
JWT 密钥、部署 ID 和 X Cookie 成套对应；只有文件存在但未验证可恢复，不算有效备份。

应用代码回退不能自动撤销数据库迁移。若新版本包含不向后兼容的 schema 变化，必须按该版本迁移说明
恢复相匹配的数据库备份，不能只启动旧镜像。任何回滚前也要先保留故障现场和当前数据库备份。

## 7. 版本化 release 目录与服务器覆盖层

仓库原生流程假定在一个稳定目录中维护 `.env.docker` 和 `.docker/`。生产服务器也可以把每个版本
解包到独立 release 目录，但 release 管理、配置继承和回滚编排不由 `docker-init.sh` 自动完成。

版本化部署必须保持以下不变量：

- 新 release 使用上一版本同一套 `.env.docker`、`.docker/` 和数据库卷；尤其不能重新生成
  `base.env`、数据库密码、连接 token 或 JWT 密钥；
- 切换期间只能从一个 release 目录执行管理命令，不能让不同目录的 operation lock 各自放行；
- 服务器 override 必须随 release 一起审核，且 `reader-docker.sh` 之类包装脚本只负责设置
  `COMPOSE_FILE` 后 `exec docker-init.sh`；
- 新 release 的 `up` 成功并通过功能验收前，保留旧源码、数据库备份和服务配置；
- 回滚时使用能够识别当前活动镜像的最新运行状态，且单独处理数据库 schema 兼容性。

一个典型的服务器包装脚本如下：

```sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export COMPOSE_FILE="$root/compose.yaml:$root/compose.server.yaml"
exec "$root/docker-init.sh" "$@"
```

例如，已有 PostgreSQL 数据卷需要跨 release 复用时，可用服务器专属 override 固定镜像并声明外部卷：

```yaml
services:
  pg-init:
    image: postgres@sha256:<审核过的-digest>
  postgres:
    image: postgres@sha256:<审核过的-digest>

volumes:
  postgres-data:
    external: true
    name: reader-postgres-data
```

这类 override 是服务器资产，不应把某台服务器的地址、卷名或临时兼容配置硬编码进通用
`compose.yaml`。执行运维命令时使用该服务器的包装入口，而不是直接调用根脚本：

```bash
./reader-docker.sh status
./reader-docker.sh up
./reader-docker.sh token
./reader-docker.sh down
```

## 8. 部署验收清单

- [ ] 运行版本是预期 commit 或 tag，服务器 override 已审核；
- [ ] `.env.docker` 只含非秘密配置，公开地址与实际入口一致；
- [ ] `.docker/` 为部署用户拥有的真实目录且权限为 `0700`，inputs 为 `0600`；
- [ ] `status` 中所有预期长期服务均为 `Up`，带 healthcheck 的服务为 `healthy`；
- [ ] `/ready` 为 200，完整翻译部署的 `/worker-ready` 也为 200；
- [ ] 客户端用原地址和连接 token 可以发现服务、登录并读取数据；
- [ ] 实际测试一次所选翻译供应商；启用 X 时实际同步一次 X 内容；
- [ ] 动态网页任务成功，并确认任务结束后没有遗留的 browser/adapter 容器和任务卷；
- [ ] 旧宿主机 API、Worker、Scweet 和 PostgreSQL 已停用，只有一套服务写数据库；
- [ ] 升级前备份、旧版本源码和回滚所需配置仍可用。
