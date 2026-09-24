# 前端工作约定

适用于 `mobile/`，先读取并遵守[根目录约定](../AGENTS.md)。本文维护前端的目录导航、环境与命令、客户端开发和 UI 验证做法；跨端协作、文档审查和全工作区资源调度由根目录统一维护。

前端使用 Expo、React Native 和 TypeScript。系统职责、数据归属及账户／翻译边界见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)，具体接口见 [OpenAPI 快照](../contracts/openapi.json)，不在本文另写协议或记录迁移历史。

## 目录导航

以下路径相对于 `mobile/`，导航维护到模块与关键入口。

| 路径 | 内容与入口 |
| --- | --- |
| `index.ts`、`App.tsx` | 应用注册、应用壳与页面装配 |
| `screens/`、`components/` | 页面及复用组件；阅读宿主入口为 `components/TranslatableWebView.tsx` |
| `ui/` | 共享样式与布局，入口为 `tokens.ts`、`layout.ts` |
| `domain/`、`hooks/` | 阅读、翻译、导航等客户端逻辑与 Hooks；网页正文提取与净化位于 `domain/webReaderExtraction.ts`，网页和字幕脚本位于 `domain/webTranslationRuntime.ts`、`domain/youtubeTranslation.ts` |
| `lib/api.ts`、`lib/http.ts`、`lib/readerTransport*.ts` | 业务 API、HTTP 处理与平台传输适配 |
| `lib/connection/`、`lib/readerAuth.ts` | 服务发现、连接切换、凭据与账户会话；连接入口为 `lib/connection/runtime.ts` |
| `state/` | 查询客户端、缓存更新与失效处理；入口为 `queryClient.ts`、`cacheUpdates.ts`、`invalidation.ts` |
| `i18n/` | 界面语言、消息与语言包；修改文案时核对 `messages/`、`packs/` |
| `lib/generated/api.ts`、`domain/translationRuntimeSources.generated.ts` | 生成的 API 类型与注入脚本资源，不手改 |
| `scripts/` | 代码生成和行为检查；浏览器场景位于 `scripts/browser/`，测试命令以 `package.json` 为准 |
| `app.json`、`eas.json`、`assets/` | 原生应用配置、构建 profile 与应用资源 |

## 环境与命令

使用 pnpm，版本以 `package.json` 的 `packageManager` 为准；Node.js 与现有 CI 的 24 版本对齐。依赖版本以 `package.json` 和 `pnpm-lock.yaml` 为准，沿用锁文件安装。

以下命令在 `mobile/` 执行；验证与构建遵守[共享资源调度](../AGENTS.md#验证与资源调度)。

| 用途 | 命令 |
| --- | --- |
| 安装依赖 | `pnpm install --frozen-lockfile` |
| 开发客户端预览 | `pnpm run start --dev-client --max-workers 1` |
| Web 预览 | `pnpm run web --max-workers 1` |
| 类型／lint 检查，逐条运行 | `pnpm run typecheck`、`pnpm run lint` |
| 指定行为检查 | 从 `package.json` 选择对应的 `pnpm run test:<名称>` |
| 前端完整检查 | `pnpm test` |
| Android 真机首页返回回归 | 先启动开发客户端 Metro，再运行 `maestro --device <adb-serial> test .maestro/flows/home-article-return.yaml` |
| 更新网页／字幕注入资源 | `pnpm run generate:translation-runtime` |
| 检查翻译运行时，含浏览器场景 | `pnpm run test:translation-runtime` |
| 安装浏览器测试依赖 | `pnpm exec playwright install chromium` |
| 构建可独立安装的 Android APK | `pnpm dlx eas-cli@latest build --platform android --profile preview` |

接口快照与类型的生成、检查命令统一见[根目录接口规则](../AGENTS.md#环境与常用命令)。完整检查包含契约检查和浏览器检查，因此需要已准备的 `../backend/.venv` 与 Playwright Chromium。

## 客户端开发约定

- 修改 Expo／React Native API、原生配置、插件或依赖时，核对安装版本对应的官方文档；当前 Expo SDK 57 的入口为 [版本文档](https://docs.expo.dev/versions/v57.0.0/)。普通业务逻辑或工具脚本修改按相关实现核对，不强制重读整套框架文档。
- 页面和 Hooks 复用现有 API、连接与缓存入口。修改账户请求、服务器切换或异步状态更新时，同时核对会话刷新、缓存失效和 `lib/connection/guard.ts`，避免绕过既有连接与身份检查；边界依据见[连接服务与账户会话](../docs/ARCHITECTURE.md#连接服务与账户会话)。
- 服务器地址及凭据沿用运行时发现、连接存储与平台密钥存储，不写入应用配置、dotenv 或构建脚本。修改传输层时同时核对普通与 `.native.ts` 实现，以及第三方网页请求与 Reader 账户请求的区别。
- 修改网页／字幕逻辑时编辑 TypeScript 源码，再生成注入资源并验证。核对导航、视频切换和偏好变化期间的过期结果处理；职责与执行路径见[翻译架构](../docs/ARCHITECTURE.md#翻译的三条执行路径)。
- 网页缓存清理入口为 `domain/webStorage.ts`，与翻译脚本共用 `generate:translation-runtime` 生成链；修改时运行 `test:web-translation-browser` 中的 `web-storage.spec.mjs`，核对既有缓存回收、持续写入及登录存储保留。Android 与 iOS 的 `clearCache`／`cacheEnabled` 语义不同，不可直接共用会清除站点状态的配置。
- 修改网页阅读模式时，同时验证 Screen 切换状态与真实 DOM／桥接场景：`test:social-web-reader` 覆盖页面挂载，`test:web-translation-browser` 包含正文提取、可信阅读文档净化和页面变更失效。桌面 Chromium 结果不能替代 Android／iOS WebView 的注入、双视图性能与视觉验收。
- 修改界面文案和共享样式时核对 `i18n/`、`ui/` 及已有组件，统一维护可复用内容。新设计不受旧组件样式限制，但采用新的共享样式后应同步受影响页面。
- `app.json` 管理原生配置；当前 Android 明文 HTTP 通过 `expo-build-properties` 配置，iOS 使用 ATS 配置，Web 受浏览器策略约束。修改这些配置时检查各平台影响，不用单个平台结果代表其他平台。

## UI 与验证

- 开始实现前明确有效的草稿版本、页面与资源。用户调整后同步实现依据，旧实验不能混入交付；示例数据标识、调试入口和设计说明放在产品布局之外，内容只使用真实支持的数据字段。
- UI 草稿使用本项目 Expo 技术栈，经 Web 预览探索并尽早在真机检查。多页面实现先完成共享组件和代表性页面，再扩展到其他页面。
- 遵循已确认的图标、文案和布局，技术限制导致的具体差异先说明。对照有效草稿，用相同或可比视口与数据检查受影响页面、长内容、缺图及空／加载／错误状态，检查共享组件有无位置跳动；真机核对文字、安全区、键盘和滚动。
- 行为测试与视觉验收分别报告。尚未完成浏览器对照、真机检查或用户视觉确认时明确待验收，不以测试通过或页面打开代替视觉确认。
- 按根目录要求选择针对性或完整检查，具体脚本以 `package.json` 为准。JS／UI 迭代优先使用现有开发客户端和真机预览；原生配置、依赖、资源或打包行为变化，发布交付或有具体打包风险时，才执行对应平台的导出／构建。导出时限制 Metro worker 为 1。
- APK 构建使用 `eas.json` 的 `preview` profile；`production` 保持商店 AAB 输出，`development` 使用开发客户端，不能作为独立发布包的验收证据。
- `.maestro/flows/` 保存少量发布前真机关键链路。Flow 使用稳定 `testID` 和可观察状态，不使用屏幕坐标；需要保留登录或连接状态的场景不得设置 `clearState: true`。Android 真机由 ADB 连接，应用需预先安装，运行时用 `--device` 明确目标设备。

## 本文件更新时机

前端入口、环境、命令、客户端开发约定或验证方式变化时更新本文。共享规则修改根目录 AGENTS；职责与系统边界修改 ARCHITECTURE；接口按根目录规则重新生成，不在这里累积任务记录和测试报告。
