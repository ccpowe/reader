# React 开发规范

编写、审查 Reader App（`mobile/src/`）代码时遵守本文。它把[软件设计规范](software-design-guidelines.md)的原则落到
React 与本项目的框架上；通用原则（深模块、信息隐藏、命名、注释）以该文为准，本文只写 React 里具体怎么做。
视觉与组件规则见 [`mobile/design/project/DESIGN.project.md`](../../mobile/design/project/DESIGN.project.md)，
产品决定见 [`mobile/design/DECISIONS.md`](../../mobile/design/DECISIONS.md)，命令与验证见 [`mobile/AGENTS.md`](../../mobile/AGENTS.md)。

> 来源：原则部分改写自用户另一个项目（wandrun）的同名规范，2026-11-11 按 Reader 改写。标「待建」的设施在实现对应部分时建立，
> 建好后把本文的「待建」改成实际路径。

框架版本：React 19.3、React Native 0.88、Expo SDK 58、Expo Router；样式 uniwind（Tailwind 4）；通用组件在 `src/ui/`
（按 quietpaper 风格包实现，需要时参考 React Native Reusables 的写法）；数据读取 TanStack Query v5。

**新旧代码**：`mobile/` 顶层的 `App.tsx`、`screens/`、`components/`、`hooks/`、`lib/`、`state/`、`domain/` 是旧版，按 P2
只迁移翻译、正文提取、YouTube 字幕和连接 / 账户中有价值的部分，其余不再修改，新版完成后删除。本文只约束 `src/`。
从旧代码迁移时按本文改写，不把旧写法原样搬进来。

## 0. 为什么需要这份规范

React 代码的复杂度主要来自两处：**状态被拆碎**（几个 `useState` 本该一起变化，却能各自改出互相矛盾的组合），
以及**副作用被隐式触发**（`useEffect` 的依赖数组决定何时执行，读代码看不出“这件事为什么发生”）。
Agent 尤其容易“随手加一个 `useState`、再加一个 `useEffect` 同步它”。

旧版就是这样长出来的：页面里大量 `useEffect` + `let active = true` 防竞态、`mounted` ref、靠计数器触发重读，
翻译相关的 hook（`useSegmentTranslationQueue`、`useRealtimeTranslationScheduler`）尤其难读。新版从一开始就按本文写。

依据：React 官方 [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)；
Factory 禁用 `useEffect` 的做法（[no-use-effect](https://gist.github.com/alvinsng/5dd68c6ece355dbdbd65340ec2927b1d)）；
状态机与 `useReducer` 的社区讨论（Matt Pocock、Sandro Maglione）。

## 1. 状态放在哪里

先判断状态属于谁，再选写法。同一份数据只有一个持有者，其他地方读它或从它算，不复制。

| 状态 | 持有者 | 写法 |
|---|---|---|
| 服务器数据（信息流、文章、榜单、订阅、收藏、我的资料与偏好） | Reader 服务器；App 只缓存 | TanStack Query（第 4 节），不在组件里手写请求 |
| 连接与账户会话（服务器地址、身份、令牌、登录状态） | 会话 store：状态机 `src/lib/connection/session.ts`，持有者 `session-store.ts`，实例与 `useSession()` 在 `src/lib/app-session.ts` | `useSyncExternalStore(subscribe, snapshot)` 读取，不复制进 `useState` |
| 页面流程（加载 → 操作中 → 成功 / 失败 / 结果未知） | 页面 | `useReducer` 写成状态机（第 2 节） |
| 一次性操作结果（“收藏失败”“已取消订阅，撤销”） | 发起操作的那次页面访问 | 就地提示或 Toast；离开页面即失效，迟到的旧结果不显示；不存进全局 store |
| 设备上的偏好（文件夹与频道筛选、列表位置） | 本机存储（AsyncStorage） | 一个模块持有读写，页面读它的快照 |
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

出现以下任一信号就换成 reducer：`loading` / `error` / `saving` 等布尔值描述的是同一流程的不同阶段；
需要 ref 或 if 判断“当前能不能接受这个操作”；同一次事件要连着调用三个以上 setter。

**状态机写法**（不引入 XState 等库，用 TypeScript 可辨识联合就够）：

```ts
type State =
  | { status: 'editing'; draft: Draft; error?: string }
  | { status: 'saving'; draft: Draft; operation: string }
  | { status: 'unknown'; draft: Draft }; // 结果未知（第 5 节）

function reducer(state: State, event: Event): State {
  switch (state.status) { // 先按当前状态分支
    case 'editing':
      if (event.type === 'change') return { ...state, draft: { ...state.draft, ...event.fields }, error: undefined };
      if (event.type === 'save') return { status: 'saving', draft: state.draft, operation: event.operation };
      return state; // 本状态不接受的事件原样返回
    case 'saving':
      if (event.type === 'saved' && event.operation === state.operation) return /* … */;
      return state;
    // …
  }
}
```

- reducer **先按当前状态分支，再按事件分支**，不接受的事件返回原状态。只按 `event.type` 分支的 reducer 仍然
  “任何状态接受任何事件”，比如在 `editing` 时收到迟到的 `saved` 也会生效。
- reducer 是纯函数：不发请求、不导航、不读时间和随机数。需要的值（操作 id、时间）由事件带进来。
- 异步结果带上发起时的操作 id，reducer 只接受与当前操作一致的结果。这代替“用 ref 记操作代际”。
- reducer、状态类型和其他纯逻辑放在 `src/lib/<主题>.ts`，每条转换有 L1 测试（第 8 节）；页面文件只做装配。

**外部数据与 reducer**

1. 每份数据只有一个持有者。Query 和连接会话里的事实直接读；reducer 只持有页面自己的东西（本地交互、草稿、操作进行到哪一步）。
   显示什么、哪些按钮可用，用纯函数 selector 从“reducer 状态 + 外部快照”计算，不在渲染、Effect 或订阅回调里复制外部状态。
2. 用户操作在处理函数里发起，结果带操作 id 回到 reducer，只有本地阶段仍匹配时才接受。
3. 对象切换（服务器、账户、文章 id）用 `key` 重置需要重置的最小子树，不在 effect 里逐个复位。
4. 清理函数只撤销本次订阅，不在清理里删除或消费业务结果（开发模式会“挂载 → 清理 → 再挂载”）。

**编辑服务器数据的表单**（改密码、改资料、翻译设置、添加订阅）：草稿只用本次进入页面后第一次成功读到的数据初始化一次，
之后的读取不覆盖草稿；也可以等数据就绪后再挂载内层表单、用初始值建草稿。保存中、保存失败、结果未知分别是状态，不是布尔值。

## 3. 副作用：不直接使用 `useEffect`

**规则**：`src/` 内不直接使用 `useEffect` / `useLayoutEffect`，由 `eslint.config.js` 对 `src/**` 的
`no-restricted-imports` / `no-restricted-syntax` 强制（故事和测试也不例外，`pnpm run lint:new`）；唯一的实现例外是
`src/lib/use-mount-effect.ts`。延迟出现、闪动这类纯视觉的时间效果用 Reanimated 4 的 CSS 动画（例：`src/ui/Button.tsx` 的延迟转圈），不用 effect 加 state。
新代码没有历史包袱，不设豁免基线。

先按下表找替代写法：

| 想做的事 | 改成 |
|---|---|
| 从 state / props 算另一个值 | 渲染时计算（第 1 节） |
| 读取服务器数据 | TanStack Query（第 4 节） |
| 响应用户操作 | 在事件处理函数里直接做。不要“设一个标志 → effect 看到标志去执行 → 再清掉标志” |
| 让读取重新执行 | 调用 query 的 `refetch()` 或封装的 `retry()`。不要用递增计数器当 effect 依赖 |
| 对象（服务器、账户、文章 id）变化时重置组件 | 给组件加 `key` 让它重新挂载 |
| 订阅外部 store（连接会话、本机偏好） | `useSyncExternalStore` |
| 页面获得 / 失去焦点时开始 / 停止（返回键、聚焦刷新） | Expo Router 的 `useFocusEffect` |
| 挂载时与外部系统同步一次（原生事件、AppState、网络状态、第三方组件） | `useMountEffect`（`src/lib/use-mount-effect.ts`，唯一的 `useEffect` 例外） |
| 让异步回调读到最新值 | `useEffectEvent`；不要用 effect 写 ref |
| WebView 注入脚本与桥接消息（阅读页翻译、阅读模式、YouTube 字幕） | 一个桥接模块持有消息收发与当前文章身份；页面只发命令、读它的快照。不在页面里散布处理消息的 effect |

**例外：根据上一次渲染调整状态**。需要知道某个值“刚刚由 A 变成 B”时，可以用 React 官方的
[Storing information from previous renders](https://react.dev/reference/react/useState#storing-information-from-previous-renders)：
把上一次的值存在本组件自己的 state 里，渲染期间比较并在不同时更新。条件：只更新本组件自己的 state；有比较守卫，
重复执行结果相同；先确认纯派生做不到，并在注释里写明为什么需要“上一次的值”。

用 `useMountEffect` 时：

- 前置条件不满足就先不渲染这个组件（父组件早返回），不在 effect 里写 `if (!ready) return`。
- 依赖会变化时，不改回带依赖数组的 effect，而是让父组件用 `key` 在依赖变化时重新挂载它。
- 必须返回清理函数，并且可以重复执行。

`eslint-config-expo` 带的 `eslint-plugin-react-hooks` 会拦住渲染期间同步 `setState`、渲染时读写 ref 等写法；
它拦不住 `.then()` 回调里的异步 `setState`，这正是第 4、5 节要统一的地方。不用 `eslint-disable` 绕过规则；
确有必要时写明原因并在交付里说明。

## 4. 读取服务器数据

有限、幂等的 GET（信息流、文章详情、榜单、订阅、收藏、我的资料与偏好）统一用 TanStack Query v5。

**定义与身份**

1. 同一资源只有一处 queryKey / options 定义（各资源随实现放进 `src/lib/queries/`），所有消费者共用；默认配置与 `key()` 在 `src/lib/query.ts`。
2. queryKey 第一段是**连接身份**（服务器标识 + 账户 id）：换服务器或换账户时旧缓存自然不再命中，退出登录时按前缀清掉。
   不用 URL、令牌作身份；不跨身份使用 `placeholderData` / `initialData`。
3. 请求函数把 query 的 `signal` 传进 API 客户端（`session.api().request(path, { signal })`，`src/lib/api/client.ts`），离开页面或换参数时真正取消请求；超时与取消覆盖到读完响应体。
4. 请求和响应类型只用从 OpenAPI 生成的类型（`contracts/openapi.json` → 生成文件），不手写一份接口类型。
5. 重试、退避、哪些错误不重试（401、404、限流）以及 stale / focus / reconnect 策略在一处默认配置里定义；
   页面不各自调 `gcTime`、不 `removeQueries`。

**失效与生命周期**

6. 写操作（收藏、订阅、改偏好）成功后，在该操作的模块里按“操作 → 受影响的 key 前缀”失效或写回，页面不各自决定刷新哪些列表。
7. 退出登录、换服务器时由连接会话一处清掉该身份的缓存。
8. 留在导航栈里但失焦的页面不继续自动刷新；聚焦刷新用 `useFocusEffect` 调 `refetch({ cancelRefetch: false })` 合并在途请求。
9. App、Storybook 故事与测试都挂载 QueryClient；原生 focus（AppState）与 online（expo-network，连上任何网络即算在线，局域网服务器也算）只在根布局接一次、带清理（`connectAppLifecycle`）。

**页面里怎么用**

10. 不把 query 数据复制进 `useState`，渲染时直接用或派生。`setQueryData` 只写同一 key 的完整快照。
11. “已加载”用 `data !== undefined`；离线（`fetchStatus === 'paused'`）不显示成错误或空列表，交给页面顶部的连接横幅；
    刷新失败保留旧内容，就地提示（对应原型「加载失败，显示的是之前的内容」）。
12. 加载指示延迟显示（`--delay-loading` 300ms），快速返回的读取不闪骨架。

**分页**（信息流、收藏用游标分页）：用 `useInfiniteQuery`，只有“加载更多旧内容”一个方向时**不设 `maxPages`**——
从中间丢页会让刷新漏掉最新内容、改变正在读的位置。缓存回收靠失焦后的 `gcTime`，并在 options 里写明。

## 5. 用户操作与异步结果

- 操作在事件处理函数里发起：先进入“进行中”，`await` 请求，再把结果交回 reducer（或 mutation 的回调）。
- 防重复提交靠状态（“进行中”不接受再次提交），不靠额外的布尔 ref。
- 超时或断线时进入“结果未知”，既不假装成功也不假装失败；能核对的（收藏、订阅）重新读取一次确认。
- 收藏、取消收藏这类可撤销的轻操作可以先在界面上生效（乐观更新），失败时回滚并就地提示。
- `await` 之后要导航时，先确认页面仍然聚焦、操作仍是当前操作。“页面是否仍挂载 / 聚焦”这类界面生命周期事实可以放在 ref 里；业务状态不放 ref。
- 错误就地显示，转成用户能懂的文案（待建 `src/lib/user-error.ts`），不用 `Alert.alert`；需要确认的操作用 `src/ui` 的对话框或弹层。

## 6. 组件与模块边界

- `src/app/`：只放 Expo Router 路由文件，负责装配——读路由参数、接 query 与 reducer、把数据和回调传给视图。
- `src/screens/`：页面视图，只接收数据与语义化回调，不读取数据、不导航（设计原型的故事直接渲染它们）。
- `src/components/`：Reader 专用的可复用界面（`FeedCard`、`BoardEntry`、`SubscriptionRow`……）。
- `src/ui/`：通用组件，即 quietpaper 在 React Native 上的实现；页面只用它们和 Tailwind 布局类，不自己写颜色（qp lint 检查）。
- `src/lib/`：纯逻辑、API 客户端、query 定义、连接会话、WebView 桥接；纯逻辑都有 L1 测试。
- 通用组件从真实页面的需要中提取，不先行设计。子组件接收数据与语义化回调（`onSave`、`onSelect`），不接收父组件的 `setState`。
- 只有确实跨页面共享的状态才放进外部 store。
- `useMemo` / `useCallback` 只在需要稳定引用（作为依赖、传给订阅或列表）或测量过有性能问题时使用；代码保持
  React Compiler 的要求：渲染纯、不在渲染中读写 ref、不修改 props 和 state。

## 7. 框架约定（只列入口）

| 主题 | 约定 | 出处 |
|---|---|---|
| 路由 | Expo Router，`src/app/` 下每个文件是一个页面；5 个底部标签 + 页面栈（P14） | `mobile/design/DECISIONS.md` |
| 样式 | uniwind 类名 + `global.css` 主题变量；`cn()` 合并类名 | `mobile/design/project/DESIGN.project.md` |
| 视觉检查 | qp lint、真机对齐检查 `pnpm run check:story-alignment` | `mobile/AGENTS.md` |
| 列表 | FlatList；长列表的滚动位置与“返回不跳动”按旧版行为实现，先写行为约定再选库 | 旧版 `hooks/useListPositionMemory.ts` |
| 交互机制（滚动、手势、动画、底部弹层） | 用为该场景设计的库并照官方示例跑通（弹层用 `@gorhom/bottom-sheet`），不用帧时序补丁 | — |
| 文案 | 界面文字走 i18n（界面语言可切换）；`t()` 包裹完整句子，不拼接 | 旧版 `i18n/`（迁移） |
| Expo API | 先核对 SDK 58 对应文档，不凭记忆 | `mobile/AGENTS.md` |

## 8. 测试

- **L1（纯逻辑）**：reducer、selector、格式化、翻译分段等纯函数，放在被测文件旁的 `*.test.ts`，`pnpm run test:unit`
  （`node --test`，Node 24 直接运行 TypeScript）。被测模块不能依赖 React Native，互相导入用带 `.ts` 扩展名的相对路径。毫秒级，每次改动都跑。
- **L2（页面与交互）**：Storybook 故事覆盖页面的各个状态（原型即测试数据）；需要真实交互的关键链路用 Maestro 在真机上跑。
- **WebView 注入脚本**：沿用旧版的 Playwright 浏览器检查（`test:translation-runtime`），迁移时一起迁。
- 行为测试与视觉验收分开报告；测试通过不能代替真机上的视觉确认（`mobile/AGENTS.md`）。

## 9. 检查清单

写前端代码的任务，交付时逐条对照本节，给出结论与 `文件:行`。

### 编写代码

- [ ] 每个新状态的持有者是谁？有没有存了可以算出来的值，或复制了 Query / 连接会话的数据？
- [ ] 一起变化的状态是否合并进 reducer？有流程的页面，reducer 是否先按状态分支，且每条转换有 L1 测试？
- [ ] 是否新增了直接的 `useEffect`？每个副作用是否落在第 3 节表中的某一类？
- [ ] 读取服务器数据是否走 Query？queryKey 是否以连接身份开头？有没有 `active` 标志、`mounted` ref 或重试计数器？
- [ ] 异步结果是否只在操作仍为当前操作时生效？结果未知是否如实呈现？
- [ ] 对象切换（服务器、账户、文章）时是否用 `key` 重置，而不是在 effect 里逐个复位？
- [ ] 纯逻辑是否在 `src/lib/` 并有 L1 测试；路由文件是否只做装配，视图是否不读数据？
- [ ] 是否有为通过 lint 而加的 `eslint-disable`？有的话是否写明原因？

### 代码审查

- [ ] 能否只看 reducer 和事件处理函数，就说清“这件事为什么发生、在什么状态下发生”？
- [ ] 有没有一个操作在不该接受的状态下仍能改变状态（迟到的响应、重复点击、换账户后的旧结果）？
- [ ] 从旧代码迁移的部分，是否顺手去掉了旧写法（effect、ref、计数器），而不是原样搬进来？
