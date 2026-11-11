# React 开发规范

> **来源与状态**：原文来自用户的另一个项目（wandrun，2026-11），2026-11-11 复制到本仓库，**尚未按 Reader 改写**。
> 文中的网关、session、`src/lib/...` 路径、ADR 与 `frontend-workflow.md`、`design.md` 等链接属于原项目，本仓库没有；
> 在 Reader 前端重新设计时按实际技术栈改写后再作为约束使用。原则部分（状态归属、reducer 状态机、不直接使用 `useEffect`、
> 统一读取机制、操作结果与检查清单）可以直接参考。

编写、审查 App（`mobile/`）代码时遵守本文。它把[软件设计规范](../../docs/engineering/software-design-guidelines.md)的原则落到
React 与本项目的框架上；通用原则（深模块、信息隐藏、命名、注释）仍以该文为准，本文只写 React 里具体怎么做。
页面开发流程见[前端开发工作流](frontend-workflow.md)，视觉见[设计规范](design.md)，Expo 版本、命令、路由、键盘见 [mobile/AGENTS.md](../AGENTS.md)。

框架版本：React 19.2、React Native 0.86、Expo SDK 57、Expo Router；组件库 React Native Reusables。

## 0. 为什么需要这份规范

React 代码的复杂度主要来自两处：**状态被拆碎**（几个 `useState` 本该一起变化，却能各自改出互相矛盾的组合），
以及**副作用被隐式触发**（`useEffect` 依赖数组决定何时执行，读代码看不出“这件事为什么发生”）。
Agent 尤其容易“随手加一个 `useState`、再加一个 `useEffect` 同步它”。

2026-10-29 的盘点（`mobile/src`，不含测试与 story）：`useState` 332、`useEffect` 146、`useReducer` 0；
effect 内手写 `let active = true` 防竞态 41 处、`mounted.current` 54 处、靠 `setXxxRetry(n + 1)` 触发重读 36 处。
典型页面 `app/agent-editor.tsx` 有 20 个 `useState`，再用 `operation` / `mounted` / `navigationEpoch` 三个 ref 判断“现在能不能做这件事”。

依据：React 官方 [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)；
Factory 禁用 `useEffect` 的做法（[no-use-effect skill](https://gist.github.com/alvinsng/5dd68c6ece355dbdbd65340ec2927b1d)，2026-03）；
Matt Pocock、Sandro Maglione 关于 `useState` / `useReducer` / 状态机的讨论（2026-10-28～29）。

## 1. 状态放在哪里

先判断状态属于谁，再选写法。同一份数据只有一个持有者，其他地方读它或从它算，不复制。

| 状态 | 持有者 | 写法 |
|---|---|---|
| 网关数据（列表、详情） | 网关；App 只缓存 | 统一的读取机制（第 4 节），不在组件里手写请求 |
| 跨页面的会话状态（连接、对话、浏览器任务） | `ChatSession`、`SessionProvider` 等外部 store | `useSyncExternalStore(subscribe, snapshot)` 读取，不复制进 `useState` |
| 页面流程（加载 → 编辑 → 保存 / 删除 / 结果未知） | 页面 | `useReducer` 写成状态机（第 2 节） |
| 一次性操作结果（切换 / 保存 / 测试失败等横幅文字） | 发起操作的那次页面访问 | `useVisitNotice()`（`src/lib/use-focused-visit.ts`）：操作开始时 `begin()`，失败时用返回的 `publish` 发布；**不存进页面 reducer 或设备级 store**。离开（失焦 / 卸载）即撤销，旧访问的迟到结果不发布；流程状态（忙态、成功应用）仍由 reducer / owner 照常结束。弹层内错误随弹层留在 reducer。规则见 [design.md 提醒规则](design.md#提醒的归属与消失2026-11-02-用户确认盘点见-app-提醒盘点) |
| 简单、彼此独立的界面值（输入框文字、展开、当前标签） | 组件 | `useState` |
| 路由参数 | Expo Router | `useLocalSearchParams` 读取，作为输入使用，不同步进 state |
| 能从以上算出来的值 | 无 | 渲染时直接计算，必要时 `useMemo`；**不存成 state** |

判断“算出来”的信号：准备写 `useEffect(() => setX(f(y)), [y])`，或者某个 state 只是另一个 state / prop 的镜像。

## 2. 选 `useState`、`useReducer` 还是状态机

| 情况 | 用 |
|---|---|
| 简单、彼此独立的值 | `useState` |
| 几个值总是一起变化 | `useReducer` |
| 状态按流程走，某些操作只在某些阶段有效 | `useReducer` 写成状态机 |

一个组件出现以下任一信号，就该换成 reducer：`loading` / `error` / `saving` 等布尔值描述的是同一流程的不同阶段；
需要 ref 或 if 判断“当前能不能接受这个操作”；同一次事件要连着调用三个以上 setter。

**状态机写法**（不引入 XState 等库，用 TypeScript 可辨识联合就够）：

```ts
type State =
  | { status: "loading" }
  | { status: "editing"; draft: Draft; error?: string }
  | { status: "saving"; draft: Draft; operation: string }
  | { status: "unknown"; draft: Draft };          // 结果未知，见前端工作流 5.2

function reducer(state: State, event: Event): State {
  switch (state.status) {                          // 先按当前状态分支
    case "editing":
      if (event.type === "change") return { ...state, draft: { ...state.draft, ...event.fields }, error: undefined };
      if (event.type === "save") return { status: "saving", draft: state.draft, operation: event.operation };
      return state;                                // 本状态不接受的事件原样返回
    case "saving":
      if (event.type === "saved" && event.operation === state.operation) return /* … */;
      return state;
    // …
  }
}
```

- reducer **先按当前状态分支，再按事件分支**，不接受的事件返回原状态。只按 `action.type` 分支的 reducer 仍然“任何状态接受任何动作”，
  比如在 `idle` 时收到迟到的 `success` 也会变成 `success`。
- 有[转换表](frontend-workflow.md#43-转换表按需)的页面，reducer 的分支与转换表一一对应：审查时对着表看 reducer，
  每一行转换写成一个 L1 测试（纯函数，毫秒级）。
- reducer 是纯函数：不调用网关、不导航、不读时间和随机数。需要的值（操作 id、时间）由事件带进来。
- 异步操作的结果带上发起时的操作 id，reducer 只接受与当前操作一致的结果。这代替“用 ref 记操作代际”的写法。
- reducer、状态类型和转换的纯逻辑放在 `src/lib/<页面>.ts`（已有先例如 `lib/agent-editor.ts`），页面文件只做装配与渲染。

### 状态机怎样接收外部输入（2026-10-30 起）

> 依据：[外部状态分析](../../docs/research/app-reducer-external-state-2026-10-30.md)；`agent-editor` 样本按此实现，经两轮审查、完整 CI 与用户真机验收（2026-10-30）。样本代码即参考实现：`src/lib/agent-editor.ts`（reducer 与 `selectAgentEditor`）、`src/app/agent-editor.tsx`（容器）、`src/components/agent-editor-view.tsx`（视图）。

**所有页面**

1. **每份数据只有一个持有者。** Query、session、流等外部事实订阅后直接读取；reducer 只持有页面自己的东西（本地交互、草稿、本次操作进行到哪一步）。显示什么、哪些按钮可用，用纯函数 selector 从“reducer 状态 + 外部快照”计算，不在渲染、Effect 或订阅回调里把外部状态复制成第二份。
2. **用户操作在处理函数里发起**，结果带真实操作 id 回到 reducer，只有本地阶段仍匹配时才接受；外部持有者另有最终准入时保留它。
3. **对象切换用 `key` 重置**（session、资源 id）；普通数据更新不作 key。只给真正需要重置的最小子树加 key（例如离开控制），不要让表单与草稿随之重挂。
4. **清理函数只撤销本次订阅与访问权**，不在清理里删除或消费业务结果（开发模式会执行“挂载→清理→再挂载”）；导航不依赖几个 Effect 的书写顺序。跨页面交接带预期身份（操作 id、资源 id），只消费与之匹配的记录。

**编辑服务器数据的草稿页**（页面有可编辑草稿、草稿来自网关数据，且写入可能结果未知或资源可能被别处删除时适用；列表、展示页不需要）

5. **初始化与重建草稿用命名的“采纳”事件**（来源只有快照时，允许在当前组件内、纯、有守卫地在渲染中 dispatch 一次，不得改其他组件或外部持有者）：初次读取形成草稿、核对完成后以实际内容替换草稿，属于无法从外部数据直接算出的语义变化。采纳事件必须校验来源：初次采纳认本次访问的读取身份，核对后采纳认写操作的 operationId；拒绝过期或重复的结果，保证只采纳一次；之后的普通读取不覆盖草稿。也可以等数据就绪后再挂载内层表单、用初始值建草稿，但“已就绪”必须在本次访问内一旦成立就不回退——后续读取失败不能卸载已编辑的表单。
6. **转换表写明外部与本地冲突时谁优先**（例如：资源已删除 > 写入结果未知 > 本次操作进行中 > 本地编辑），并覆盖字段、按钮、返回与弹层；“能否写入”与“能否离开 / 放弃”是两种能力，分开投影——禁写（如离线）不能挡住离开或放弃；reducer 与 selector 都有 L1 测试，其中采纳事件测“待采纳 → 采纳 → 不再待采纳”的闭环；装配层另有整页测试覆盖离线 × 有改动 × 返回（自动保存）等组合。
7. 外部事件很密集的页面（如聊天流）在原持有者里处理事件，页面只读投影，不把页面渲染当事件循环。

### 迁移一个页面流程的步骤

1. 对照实际入口和状态文档，列出状态、事件、转换与各状态持有者（按上节“状态机怎样接收外部输入”区分本地与外部事实）；把读取、会话事实、草稿和导航生命周期分开。
2. 先在 `lib/` 写状态联合与事件接口，建立“状态文档行 / 转换表 → 分支”映射；第一个样本在装配前确认这一倍增点。
3. 写纯 reducer 与逐行 L1 转换测试，先核对非法事件、旧操作结果、重复操作及失败返回；不把网络或导航塞进 reducer。
4. 容器接读取、reducer 和命令 owner：用户事件发起命令，结果携操作身份回到 reducer；核对离开后谁继续跟踪、谁能导航。
5. 用 session / 资源 id 的重挂边界接回原视图，删除被替代的 state、业务 ref、effect 与读取；先走一次正常入口，再补交错场景。
6. 保留网关边界注入的整页 story；难达状态按需给纯视图状态 story。核对原生表面与原有 L2，再清理失效 lint 豁免；同步状态文档并记录实际验证边界。


## 3. 副作用：不直接使用 `useEffect`

**规则（2026-10-29 决定）**：`eslint.config.js` 的 `wandrun/no-use-effect`（复用 ESLint 的 `no-restricted-syntax` 实现）
禁止从 React 具名导入 `useEffect` / `useLayoutEffect`（含别名）、直接调用，以及通过 namespace、静态计算属性（含无插值模板）或解构取用这两个属性；不追踪动态拼接的属性名。
它使用独立的规则名与豁免计数，不能拿已清理的颜色、动效或 `Alert.alert` 豁免抵扣新增 effect。
规则适用于 `src`，其中的测试与 story 不例外；`test/` 等 `src` 外文件沿用原有配置，不增加此限制。
唯一实现例外分别是 [`src/lib/use-mount-effect.ts`](../src/lib/use-mount-effect.ts)（仅 `useEffect`）、
[`src/lib/use-measure-layout.ts`](../src/lib/use-measure-layout.ts)（仅 `useLayoutEffect`）与
[`src/lib/use-runtime-commit.ts`](../src/lib/use-runtime-commit.ts)（仅提交阶段发布 / 撤销跨 runtime 回调）；其他限制不变。
已有代码计入 ESLint 批量豁免基线（`eslint-suppressions.json`），不改变业务行为。

重构到哪一页，就移除该页的直接用法，再在 `mobile/` 运行 `pnpm exec eslint src --prune-suppressions` 清理已失效的计数；
不要手改数字，也不要为新代码重新生成基线。批量豁免按“文件＋规则＋数量”记录，同一 effect 政策内等量替换仍需代码审查；
新文件或超出基线数量的违规会报错，其他规则的旧豁免不会掩盖新增 effect。

先按下表找替代写法，不要用 effect：

| 想做的事 | 改成 |
|---|---|
| 从 state / props 算另一个值 | 渲染时计算（第 1 节） |
| 读取网关数据 | 统一的读取机制（第 4 节） |
| 响应用户操作 | 在事件处理函数里直接做。不要“设一个标志 → effect 看到标志去执行 → 再清掉标志” |
| 让读取重新执行 | 调用读取机制的 `retry()` / `refresh()`。不要用递增计数器当 effect 依赖 |
| 对象（id、session、电脑）变化时重置组件 | 给组件加 `key`，让它重新挂载；不要在 effect 里逐个把 state 设回初值 |
| 订阅外部 store | `useSyncExternalStore` |
| 页面获得 / 失去焦点时开始 / 停止（返回键、轮询、聚焦刷新） | Expo Router 的 `useFocusEffect` |
| 挂载时与外部系统同步一次（订阅原生事件、第三方组件） | `useMountEffect`（唯一 `useEffect` 实现例外） |
| 绘制前测量并据此调整布局 | `useMeasureLayout`（仅几何测量的 `useLayoutEffect` 实现例外） |
| 让异步回调读到最新值 | `useEffectEvent`（React 19.2，项目已有用法）；不要用 `useLayoutEffect` / `useEffect` 写 ref |
| 向外部 UI / JS runtime 发布最新已提交回调，并在隐藏 / 卸载时撤销 | `useRuntimeCommit(callback)` 返回生命周期稳定的远端入口；只允许浏览器跨 runtime 桥，不接受任意副作用 setup |

**例外：根据上一次渲染调整状态（2026-11-05 用户批准）**。需要知道某个 prop / 派生值“刚刚由 A 变成 B”（而不只是当前值）时，
可以用 React 官方的 [Storing information from previous renders](https://react.dev/reference/react/useState#storing-information-from-previous-renders)
写法：把上一次的值存在**本组件自己的 state**里，渲染期间比较并在不同时 `setState` / `dispatch`。条件：

- 只更新本组件自己的 state，不写 ref、不调用外部、不更新父组件；
- 有比较守卫，重复执行（严格模式渲染两次）结果相同，对应的 reducer 事件幂等；
- 先确认纯派生做不到，代码注释写明为什么需要“上一次的值”并链接本条。

当前用法：`src/components/chat/chat-history.tsx` 的回合结束检测（`turn-ended`，见 `screens/chat.md` C9b）。

用 `useMountEffect` 时：

- 前置条件不满足就先不渲染这个组件（父组件早返回），不在 effect 里写 `if (!ready) return`。
- 依赖会变化时，不要改回带依赖数组的 effect，而是让父组件用 `key` 在依赖变化时重新挂载它。
- 必须返回清理函数，并且可以重复执行：开发模式下挂载、卸载可能各发生两次。
- 接口要求 `setup: () => () => void`，由返回值类型强制提供清理；内部唯一一处 `react-hooks/exhaustive-deps` 行级豁免
  用于明确固定空依赖数组，不把输入变化伪装成重新同步。

`useMeasureLayout(measure, dependencies)` 只用于绘制前的测量与据此调整布局，依赖变化时重新执行，可返回清理函数；
不用于同步 state 或写 ref。三个例外 hook 均以 `additionalEffectHooks` 登记，`useEffectEvent` 可在其回调中调用，
`useMeasureLayout` 的依赖由 `react-hooks/exhaustive-deps` 检查；内部行级豁免仅用于转交回调与依赖。

`useRuntimeCommit` 是 2026-10-30 批准的窄例外：原生手势、帧 ACK 与 UI 诊断回调需要提交阶段发布、
Suspense 隐藏时同步撤销；不能在 render 写回调槽，也不能用几何测量 hook 冒充。
被动 `useMountEffect` / `useEffectEvent` 替换方案在 `test/browser_canvas_lifecycle.test.ts` 的
`first zoom, pan, successive frames and viewport resizing preserve canvas recordings`、
`a candidate awaiting UI confirmation survives control state changes and later frames receive credit`、
`Suspense hiding disables the runtime entry and revealing restores it` 三项回归失败。
该 primitive 不执行回调、不接收依赖数组或任意 effect，仅在 layout 提交时替换内部远端回调槽并在 cleanup 置空；
当前调用点仅 `browser/frame-canvas.tsx`、`browser/use-region-gesture.ts`、`browser/diagnostics.tsx`。
本组以外暂不使用；`eslint.config.js` 的 `no-restricted-imports` 限制浏览器目录外导入，扩展用途须先确认。三种例外均登记在 `additionalEffectHooks`，不新增直接 effect 豁免。

**已知 lint 边界（7.1.1 实测）**：`additionalEffectHooks` 不会让 `react-hooks/set-state-in-effect` 分析这两个自定义 hook 的回调，
同步 `setState` 不会报错；仍须按替代表审查，不能借例外 hook 镜像 state。测量 hook 仅保留测量结果驱动的布局调整。

已经开启的 lint（`eslint-plugin-react-hooks` 7.1.1，随 `eslint-config-expo`）会拦住渲染期间同步 `setState`、
原生 effect 中同步 `setState`、渲染时读写 ref、渲染不纯等写法；自定义 hook 回调的边界见上文。
它也拦不住在 `.then()` 回调里异步 `setState`，这正是第 4 节要统一的地方。不要用 `eslint-disable` 绕过这些规则；
确有必要时写明原因并在交付里说明。

## 4. 读取网关数据

决定见 [ADR-0076](../../docs/adr/0076-app-finite-reads-via-tanstack-query.md)：有限、幂等的 JSON 读取统一用 TanStack Query v5，session 是唯一的失效与清理持有者。
迁移按资源逐个进行；尚未迁移的资源仍用 `LoadResource`（`lib/load-state.ts`），但新代码不再新增它的用法，也不手写“effect + `active` 标志 + `setState`”。

**哪些读取进 Query**

1. 只有有限、幂等的 GET 快照（列表、详情、目录、分页）进 Query。以下不进：SSE / WebSocket 与带水位的会话数据（`ChatSession`、`gateway/agents.ts`）、媒体 metadata 与 lease、会创建资源的读取（如 `blankConversation`）、写操作结果未知后的核对、本地编辑草稿、原生媒体资源。
2. 一份服务器数据只有一个持有者：不在 Query 与 session store 里各存一份。

**定义与身份**

3. 同一资源只有一处 queryKey / options 定义，所有消费者共用。queryKey 第一段是 session 身份；不用 URL、token、电脑 id 作身份；跨 session 不用 `placeholderData` / `initialData`。
4. queryFn 必须把 `signal` 传进 `GatewayApi` 并贯穿到响应体读取；读取方法不支持 `signal` 的先补上再迁。
5. 重试、退避、终止型错误（如 404）与 stale / focus / reconnect 策略在集中的默认配置里定义；每类资源的缓存保留边界写在它的 options 里，高基数 key（搜索、分页）必须有界回收。页面不调 `gcTime`、不 `removeQueries`。

**失效、取消与生命周期**

6. 网关事件（revision、connection 变化）只在 session 级 bridge 里按“事件 → key 前缀”映射处理，固定**先 `cancelQueries` 再 `invalidateQueries`**；页面和 hook 不订阅网关事件，不只调 invalidate。
7. session 关闭时由 session 一处清掉该前缀的缓存。
8. 保留在导航栈里但失焦的页面统一用 `subscribed: isFocused` 退订；`enabled: false` 不是取消。聚焦刷新用 `refetch({ cancelRefetch: false })` 合并在途请求；用户主动重试先取消该 exact key 再 refetch，立即抢占自动退避。有限快照用 `useGatewayQuery`，分页用同模块的 `useGatewayInfiniteQuery`；两者共用 `useGatewayReadControls` 的 `refresh` / `retry`。完整目录在集中 options 标记 `fetchAllPages`，共享适配器顺序续页，不在资源 hook 或页面复制这些胶水。
   **搜索访问级例外（2026-10-31，协调者确认）**：`searchOptions` 显式 `subscribed: true`，让同一仍挂载的搜索路由在进入详情期间保留观察者与全部分页；否则 `gcTime: 0` 加失焦退订会清空列表，返回丢页与滚动。仍在换词 / 卸载后以 `gcTime: 0` 即时回收，不改为无限缓存或任意分钟数的过期策略。`refetchOnWindowFocus: false` 同时由分页适配器尊重，不在返回时重拉；`networkMode: always` 保留原离线请求失败 / 就地重试表现。其他资源继续默认按焦点退订；搜索行为与回归见[搜索状态契约](screens/search.md)。
9. session 主动中止的读取（`cancelReads`）不显示为读取失败。
10. App、Storybook、页面测试和独立渲染的 scene 都挂载 QueryClient；原生 focus（AppState）与 online（Expo Network，按 LAN 判断，不要求公网可达）只接一次、带清理。

**页面里怎么用**

11. 不把 query 数据复制进 `useState`，渲染时直接用或派生。`setQueryData` 只写同一 key 的完整 GET 快照。
12. “已加载”用 `isSuccess` 或 `data !== undefined`，不用 `!== null`；错误显示 `error ?? failureReason`；`fetchStatus === "paused"`（离线）不显示成错误或空列表，交给连接横幅；刷新失败保留旧内容。
13. 编辑草稿只用**本次挂载后**的成功读取初始化（`isFetchedAfterMount && isSuccess`，多个依赖都满足），只初始化一次；之后的快照不覆盖草稿。编辑页用 session + 资源 id 作 `key`。
14. 加载指示继续延迟显示（`deferLoadingIndicator`），快速返回的读取不闪转圈。

**迁移**

15. 按资源整体切换：一个资源的列表、摘要、编辑器一起迁，迁完删除旧 hook。
16. 验收以交错行为为准，至少覆盖：初次读取进行中遇到 revision、同 key 多个消费者、session / 参数切换、失焦与最终卸载、首次与刷新失败及重试、离线与前后台、旧缓存重开草稿；分页另验同游标重试 / 去重，带版本的资源另验 CAS 与结果未知。

**单向分页的回收边界**：仅有“加载更多旧记录”、没有双向入口时，不用 `maxPages` 丢弃最新首屏——infinite query 刷新从当前保留的第一个 pageParam 开始，会遗漏最新记录，删除顶部行也会改变用户正在阅读的位置。不设 `maxPages` 时，必须说明服务器保留范围与缓存生命周期，不能把失焦回收误称为访问内固定条数上限；实例见[定时 C6：网关 100 条已结束记录上界与进行中例外](screens/schedule-editor.md#c-最近运行)。若资源必须限制页数，先提供可达的双向分页契约及入口；验收必须含超过回收边界后新 revision 仍能看到新记录、追加与普通刷新不移除已显示顶部行，而不只验证缓存页数。

### 迁移一个资源的步骤

1. 先列出该资源的列表、摘要、详情和编辑器消费者；标出 OAuth 命令排序、未知写核对、领域投影等边界。存在动态持有者交接时先验双向切换、下一次 revision 与重新聚焦，草稿基线不能参与读取路由。
2. 在 `lib/gateway-queries.ts` 定义从 session / 资源前缀派生的 options 与缓存边界，补齐 API signal 并登记 revision。有限快照用 `useGatewayQuery(options)`；分页用 `infiniteQueryOptions` + `useGatewayInfiniteQuery(options)`，完整目录的 `fetchAllPages` 策略也只定义在 options。没有 session / 参数时不挂分页消费者，有限快照传 `skipToken`；不造占位 key 或缓存。
3. 先接一个消费者核对交错与关闭，再批量切换。`SessionOwner` 在发布前装 bridge，独立场景用 `createSceneSession`；漏装时 options 直接报错。宿主使用 `GatewayQueryProvider`。
4. 编辑器按 session / id 重挂，一次初始化草稿；焦点刷新用封装的 `refresh()`，合并在途请求；手动“重试”用 `retry()`，不等退避。删除旧读取 hook、镜像、计数器与失效订阅。
5. 补第 16 条回归并保留原相关 L2；在 `mobile/` 运行 `node scripts/query-smoke.ts`（本地临时 HTTP 网关，验证真实传输中止与关闭清缓存）；同步状态文档、prune 失效豁免，提交后跑沙箱移动端 CI。

## 5. 用户操作与异步结果

- 操作在事件处理函数里发起：先 `dispatch` 进入“进行中”，`await` 网关，再 `dispatch` 结果。
- 防重复提交靠状态（“进行中”状态不接受再次提交），不靠额外的布尔 ref。
- 超时或断线时进入“结果未知”，既不假装成功也不假装失败（[前端工作流 5.2](frontend-workflow.md#52-用户操作的生命周期每个会改变数据的操作)）。
- **Agent 会使用的数据（Agent 配置、记忆、定时任务、连接器等）不在本机保留未确认的草稿**（2026-10-29 用户决定）：这些数据会被 Agent 下一轮运行直接使用，编辑页显示的必须与网关上实际生效的一致。离开编辑页时，未保存或结果未确认的内容如实告诉用户“不会保留”，再次进入一律从网关读取实际内容；不做本机草稿恢复。聊天输入框的草稿不属于此类。
- 记忆写入是领域例外：CAS 命令由页面 reducer 与访问级 `MemorySaveCheck` 持有，不进入普通 `EditorOperations`；未知结果仅按事件只读核对、无计时器，离开不保留本次草稿，见[记忆 F / G / T 状态契约](screens/agent-profile.md#f-共享记忆索引全文编辑memory-editor)。
- `await` 之后要导航时，先确认页面仍然聚焦、操作仍是当前操作。“页面是否仍挂载 / 聚焦”这类与界面生命周期有关的事实可以放在 ref 里；
  业务状态不放 ref。
- 错误在页面内就地显示（`lib/user-error.ts` 的 `userErrorMessage` 转成用户文案），不用 `Alert.alert`（lint 已禁止）；
  确认操作用组件库的对话框。

## 6. 组件与模块边界

- `src/app/` 只放路由页面；业务纯逻辑放 `src/lib/*.ts`，便于 L1 测试；可复用的界面放 `src/components/`。
- 通用组件从真实页面的需要中提取，不先行设计（根 [AGENTS.md](../../AGENTS.md)“不提前加抽象”）。
- 子组件接收数据与语义化回调（`onSave`、`onSelect`），不接收父组件的 `setState`。
- 不把状态提升为全局：只有确实跨页面共享的状态才放进 session 等外部 store。
- `useMemo` / `useCallback` 只在需要稳定引用（作为依赖、传给订阅或列表库）或测量过有性能问题时使用；
  项目未开启 React Compiler（`app.json` 无 `experiments.reactCompiler`），但代码应保持它的要求：渲染纯、不在渲染中读写 ref、不修改 props 和 state。

## 7. 框架约定（细节在链接处，这里只列入口）

| 主题 | 约定 | 出处 |
|---|---|---|
| 路由 | Expo Router，`src/app/` 下每个文件是页面 | [mobile/AGENTS.md](../AGENTS.md#navigation--routing) |
| 键盘 | `react-native-keyboard-controller`，不手算键盘高度 | [mobile/AGENTS.md](../AGENTS.md#keyboard-avoidance) |
| 聊天列表滚动 | LegendList 官方 Chat 示例 | [ADR-0041](../../docs/adr/0041-chat-scroll-legendlist-follow-read.md) |
| 交互机制（滚动、手势、动画） | 先写行为约定，用为该场景设计的库，照官方示例跑通；禁止用帧时序补丁 | 根 [AGENTS.md](../../AGENTS.md) |
| 颜色、动效时长 | 只用主题变量与 `lib/motion.ts`（lint 已强制） | [设计规范](design.md)、[ADR-0058](../../docs/adr/0058-app-motion-feedback-and-visual-baseline.md) |
| 组件选型 | 先查组件库上游目录与官方示例，再查项目封装 | 根 [AGENTS.md](../../AGENTS.md) 前端开发 |
| 文案 | `t()` 包裹完整句子；按界面文案标准写 | [mobile/AGENTS.md](../AGENTS.md#界面文案标准2026-10-16-用户确定) |
| Expo API | 先核对 SDK 版本对应文档，不凭记忆 | [mobile/AGENTS.md](../AGENTS.md) |

## 8. 检查清单

写前端代码的任务，交付时的“规范自查”逐条对照本节，给出结论与 `文件:行`。

### 编写代码

- [ ] 每个新状态的持有者是谁？有没有存了可以算出来的值，或复制了外部 store 的数据？
- [ ] 一起变化的状态是否合并进 reducer？有流程的页面，reducer 是否先按状态分支、与转换表一致，且每条转换有 L1 测试？
- [ ] 是否新增了直接的 `useEffect`？每个副作用是否落在第 3 节表中的某一类？
- [ ] 读取网关数据是否走统一机制？有没有新增 `active` 标志、`mounted` ref 或重试计数器？
- [ ] 异步结果是否只在操作仍为当前操作时生效？结果未知是否如实呈现？
- [ ] 对象切换（id、session）时是否用 `key` 重置，而不是在 effect 里逐个复位？
- [ ] 纯逻辑是否在 `src/lib/` 并有 L1 测试；页面文件是否只做装配与渲染？
- [ ] 是否有为通过 lint 而加的 `eslint-disable`？有的话是否写明原因？

### 代码审查

- [ ] 能否只看 reducer 和事件处理函数，就说清“这件事为什么发生、在什么状态下发生”？
- [ ] 有没有一个操作在不该接受的状态下仍能改变状态（迟到的响应、重复点击、切换 session 后的旧结果）？
- [ ] 改动是否顺手减少了旧写法（effect、ref、计数器），而不是在旧写法旁边并存一种新写法？
