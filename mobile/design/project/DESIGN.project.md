# DESIGN.project.md — Reader（手机端）

> 本产品对 quietpaper v0.7 的补充与覆盖。通用规则看 `../style/DESIGN.md`；冲突时以 `../DECISIONS.md` 为准。
> 实现位置：主题 `mobile/global.css`；组件 `mobile/src/ui/`（通用）与 `mobile/src/components/`（产品）。

## 平台换算（React Native）
- CSS 类在 React Native 里用不了：每个用到的 `qp-` 类在 `src/ui/` 有一个外观相同的组件（`Button` = qp-btn，`IconButton` = qp-icon-btn，`Row` = qp-row，`Tabs` = qp-tab，`Message` = qp-msg，`Callout`，`Tag` / `Count` / `Badge` / `Dot`，`Input` / `SearchField`，`Switch`，`Sheet`，`Skeleton`，`EmptyState`，`Toast`）。页面只用这些组件和 Tailwind 的布局类，不自己写颜色。
- token 搬进 `global.css`（uniwind 主题，CSS 变量）；类名去掉角色前缀：`--color-text-primary` → `text-primary`，`--color-bg` → `bg-canvas`。对照表在 `global.css` 开头。
- 间距用 Tailwind 刻度 0.5 / 1 / 1.5 / 2 / 3 / 4 / 6 / 8 / 12（= quietpaper 的 2 / 4 / 6 / 8 / 12 / 16 / 24 / 32 / 48）。
- `cn()` 必须登记自定义字号（`src/lib/cn.ts`），否则 `text-h2` 会被当成颜色合并掉。

## 触屏覆盖
- **hover → pressed**：没有悬停；所有可点元素按下时叠加 `--color-state-pressed`（`active:bg-pressed`），其余附带变化同 DESIGN 1.3。
- **点击区域**：外观尺寸按 quietpaper（文字按钮 28、图标按钮 24 / 32），点击区域用 `hitSlop` 补到 `--size-touch-target` 44。
- **常驻的行内操作**：卡片右上角的书签（P19）。其余行内操作没有。
- **输入框**：高 40、字号 16（`--font-input-size-touch`），标签在输入框上方。

## 手机端新增（quietpaper 没有，值为本项目选定，待校准）
| 东西 | 做法 | 数值 |
|---|---|---|
| 底部标签栏 | `bg-sidebar` + 顶部 1px `border-subtle`；当前项 = 图标 strong + 文字 primary（不用强调色、不加粗、不加条） | 高 56 + 安全区；图标 22；文字 caption |
| 页面标题 | 列表页页头 = h2 + 右侧图标按钮（DESIGN 3.4 的"列表页页头"） | h2 24 / 31 |
| 推入页顶栏 | 返回 + 来源 + 操作，底部 1px `border-subtle` | 高 44（`--layout-topbar-height`） |
| 阅读页悬浮按钮 | 圆形 `bg-raised` + `shadow-md`；开启时 `bg-selected` | 直径 44，图标 20 |
| 表单大按钮 | `Button large`，用于登录、添加订阅等表单的主操作 | 高 40 |
| 工具栏图标 | 页头、顶栏的图标按钮 | 图标 20，按钮框 32 |
| 卡片缩略图 | `rounded-md` + `bg-sunken` 占位 | 信息流 72、榜单 56 |
| 列表行 | 最小高 48，左右 16 | — |

## 新增组件及理由
- `FeedCard`（`src/components`）：信息流卡片；现有类只有行，没有"来源行 + 标题 + 摘要 + 缩略图"的卡片。X 帖子的引用用 `Callout` 外观（浅底无边框），不用描边卡片。
- `BoardEntry`：榜单条目（排名 + 标题 + 描述 + 元信息 + 缩略图 + 书签）。
- `SubscriptionRow`：订阅行；出问题时第二行换成原因（P23 R4-B），**状态色只放在行首小图标上，文字保持 secondary**（DESIGN 4 禁止脱离配套底色给状态文字上色）。
- `Badge` 用于"共 N 条"（qp-badge）。

## 深色（P20）
quietpaper 只有浅色。`global.css` 里的 dark 值是按角色推导的**草稿**，第 6 步校准后替换。
