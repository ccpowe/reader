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
- **点击区域**：外观尺寸按 quietpaper（文字按钮 28、图标按钮 24 / 32），点击区域用 `hitSlop` 补到 `--size-touch-target` 44；`hitSlop` 超不出父级边界，所以页头、标签条本身至少 44 高。
- **常驻的行内操作**：卡片底行的书签（P19、P26）、订阅行的 ⋮（旧版）。
- **输入框**：高 44、字号 16（`--font-input-size-touch`），标签在输入框上方。
- **加载**：按钮的转圈超过 `--delay-loading`（300ms）才出现，出现时文字隐藏但宽度不变；骨架屏在系统"减少动态效果"时不闪动。

## 手机端新增（quietpaper 没有；尺度按旧版真机截图，P25 / P26）
| 东西 | 做法 | 数值 |
|---|---|---|
| 页面边距 | 页面内容、分隔线离屏幕边缘 | 24（弹层内 16） |
| 页头 | READER 标识行（标识 32 + 字标 + 头像 36）+ 大标题行（右侧日期或工具按钮） | 标识行 48、标题 `text-page` 34 / 40 粗体 |
| 底部标签栏 | 只有图标；页面底色 + 顶部 1px `border-subtle`；当前项 = 图标 strong（不用强调色） | 高 56 + 安全区；图标 22 |
| 分段标签 | quietpaper 胶囊，整格 44 高可点 | 胶囊高 32、字 14 |
| 信息流卡片 | 来源行（22 方块 + 名称）、标题 `text-card` 18 / 28 600、摘要 `text-read` 14 / 22、缩略图 88×96、底行时间 + 书签 | 上 24 下 12；卡片间 1px 线 |
| 榜单条目 | 两位排名 + 常规字重标题 18 / 28；统计行带小图标 | 上下 24 |
| 分类卡片 | `bg-panel` 大圆角，无边框无阴影；图标簇最多 4 个 + N | 圆角 20、左右 16 |
| 悬浮添加按钮 | 圆形强调色 + `--shadow-lg` | 直径 56，距边 24 |
| 推入页顶栏 | 返回 + 页面名 + 操作，`bg-sidebar` 底，无分隔线 | 高 56 |
| 阅读页悬浮条 | 右缘竖条 `bg-raised` + `--shadow-md`；开启时 `bg-selected` | 按钮 48，图标 20 |
| 底部面板 | 顶角 `rounded-3xl`，标题 h2 + 说明 + ✕；`--shadow-sheet` | — |
| 搜索框 | qp-search 放大 | 高 44、圆角 12 |
| 设置行 | 行首图标 20、名称 16、值 13 在名称下方 | 最小高 64 |
| 勾选框 | 选中 = 强调色底 + 勾 | 20 |
| 表单大按钮 | `Button large` | 高 44 |
| 阴影 | `useShadow()`：tokens.css 的分层阴影 + `--color-subtle` 外环，转成 React Native `boxShadow` | — |

**强调色（待确认）**：暂按旧版用近黑（开关、勾选框、+ 按钮、主按钮、数字角标），链接仍是蓝。深色下强调色是浅灰、其上文字深色；危险按钮的字用单独的 `on-danger`。

## 新增组件及理由
- `FeedCard`（`src/components`）：信息流卡片；现有类只有行，没有"来源行 + 标题 + 摘要 + 缩略图"的卡片。X 帖子的引用用 `Callout` 外观（浅底无边框），不用描边卡片。
- `BoardEntry`：榜单条目（两位排名 + 标题 + 描述 + 署名 + 统计行 + 缩略图；行上不放书签，同旧版）。
- `FolderCard`：订阅页的分类卡片（旧版结构）。
- `SolidCount`：强调色底的数字角标（频道选择、筛选按钮），旧版样式；其余数字仍用灰色 `Count`。
- `Checkbox`、`Fab`：quietpaper 有勾选框但没有手机尺寸；悬浮按钮是旧版的添加入口。
- `SubscriptionRow`：订阅行；出问题时第二行换成原因（P23 R4-B），**状态色只放在行首小图标上，文字保持 secondary**（DESIGN 4 禁止脱离配套底色给状态文字上色）。
- `Badge` 用于"共 N 条"（qp-badge）。

## 深色（P20）
quietpaper 只有浅色。`global.css` 里的 dark 值是按角色推导的**草稿**，第 6 步校准后替换。
