# MIGRATION：草稿内容去了哪里（v0.1 建包时；v0.2 修正了过期描述，v0.2 自身的改名见 CHANGELOG）

去向：**CSS** = `tokens.css` / `notion.css` · **DESIGN** = `DESIGN.md` · **lint** = `checks/lint.mjs` · **删除**（写明理由）。
优先级：DECISIONS.md > 原版采集 / analysis > apps 的一致做法 > DESIGN-draft.md。（v0.1 写成"apps 优先于草稿"，和实际取舍不符，v0.2 改正。）

## 1. `tokens.css`（草稿）

| 内容 | 去向 | 说明 |
|---|---|---|
| §1–15 全部 token（颜色、字体、间距、版式、尺寸、圆角、阴影、焦点、动效、z-index） | CSS `tokens.css` | 名字和值都不变，三个 app 引用的变量全部还在。来源标签统一成 4 种，新增 `[选定 D1/D2]` |
| 头部标签说明 | CSS `tokens.css` 头注释 | 补上 `[选定 Dn]` 的含义 |
| `--color-fill-selected` = hover、`--color-text-placeholder` = tertiary | CSS | 改为 `var()` 引用，把"同值"这条规则写进代码 |
| `--layout-*` | CSS（保留） | 属于 `[模式]`。组件用到：顶栏高度、菜单 / 插入菜单宽度、设置弹窗尺寸、属性标签宽度、确认弹窗宽度（v0.2），以及借用的 `--layout-search-preview-w`（toast 文字最大宽）和 `--layout-search-list-w`（设置说明最大宽）；其余给产品布局用 |

## 2. `DESIGN-draft.md`

| 草稿章节 | 去向 | 说明 |
|---|---|---|
| 顶部说明（`[模式]` 定义） | DESIGN §3 | 规则分层 |
| §1 Design intent 1–5 | DESIGN §2（意图）+ §4（颜色位置） | 能用代码保证的部分（标题 700、hover = selected、阴影带细圈）已经写进 CSS；需要判断的部分留在 DESIGN |
| §2 颜色用法表 | CSS（各组件已绑定对应 token）+ DESIGN §4「颜色只能出现在这里」 | 表格里的"Use for"现在由类来体现；"Never for"变成 DESIGN 的判断规则和禁止事项 |
| §2 Tags and status | CSS `.nt-tag--*` `.nt-status--*` + DESIGN §4 | 状态 pill 只做有实测圆点色的 gray / blue / green |
| §3 字体与排版表 | CSS（每个组件类自带字号、行高、字重、颜色） | 外观描述删掉，不再用文字重复 |
| §3 "No webfont, letter-spacing normal" | DESIGN §1 + lint `webfont` | |
| §4 间距（只用 `--space-*`、半步） | CSS + lint `unit` | 写死的 px 由 lint 拦截；布局几何属性在白名单里 |
| §4 App shell / Document / Database / Settings / Search / Side Peek 布局 | DESIGN §7 `[模式]` 默认值；尺寸留在 `--layout-*` | 视觉部分（侧栏底色、边缘阴影、顶栏无边框）进了 `.nt-sidebar` `.nt-topbar` |
| §4 块间距（只在上方留白） | CSS `.nt-block--*`、`.nt-prose` | |
| §5 开头 "默认 `--radius-md` + `--size-button`" | CSS | |
| §5.1 Sidebar（容器、头部、搜索框、tab 行、行、分组标题、推广卡、底栏、通知点） | CSS `.nt-sidebar*` `.nt-sidebar-search` `.nt-sidebar-tab` `.nt-row` `.nt-section-label` `.nt-promo` `.nt-dot` | 推广卡的插画属于品牌资产，**删除** |
| §5.2 Top bar（面包屑、动作、状态文字、pill、打字时淡出） | CSS `.nt-topbar` `.nt-crumb` `is-faded` | 提示 pill 在 v0.1 做成了 `.nt-pill`；v0.2 删除：来源是第三方翻译插件（和 §3 删除翻译按钮同理） |
| §5.3 Editor blocks（段落、列表、标题、callout、子页面链接、大纲条、行内代码、拖拽把手） | CSS `.nt-block*` `.nt-h1–3` `.nt-text` `.nt-callout` `.nt-page-link` `.nt-toc` `.nt-code` `.nt-block-handle` | |
| §5.4 Database table（视图栏、表头、单元格、标题格、OPEN、pill、标签、人员、日期、新建行、行勾选框、打开行） | CSS `.nt-viewbar` `.nt-tab--view` `.nt-table*` `.nt-quick-action` `.nt-person` | 冻结首列分隔线：只保留 token，没有做类（需要 JS 滚动状态），记入 DESIGN「已知不足」 |
| §5.5 Popover menus（外壳、分区、分组标签、菜单项及状态、限定词、徽标、快捷键、底栏、元信息、动效、无遮罩） | CSS `.nt-popover` `.nt-menu*` `.nt-group-label` `.nt-badge--new` | 键盘当前行统一为 `is-selected`（apps 用的是 `is-active`） |
| §5.6 Search panel（外壳、输入行、筛选 chip、结果、预览卡、底栏、加载） | CSS `.nt-search*` `.nt-chip` `.nt-result*` `.nt-preview` `.nt-progress` | 预览卡里的"缩小版页面"排版（20/24、13/18、17/15/14）**删除**：只在这一处出现，属于视图形态，产品可以用现有类组合 |
| §5.7 Buttons 表 | CSS `.nt-btn--*` `.nt-split` `.nt-icon-btn` + DESIGN §4「按钮怎么选」 | 危险按钮的 hover 没采到，所以不提供 hover 变化。pressed 用根变量（在 CSS 里实现，DESIGN 标为未实测） |
| §5.8 表单（输入框、导航搜索、开关、下拉、帮助图标） | CSS `.nt-input` `.nt-search-field` `.nt-switch` `.nt-help`；下拉 = outline 按钮 + 菜单 | **图片 / 图标选择器（72px）删除**：只在设置里出现一次，而且圆角是估计值。需要时用现有类组合 |
| §5.9 Settings modal（遮罩、弹窗、关闭、左栏、内容、People 子页、骨架、动效） | CSS `.nt-modal` `.nt-dialog--settings` `.nt-nav*` `.nt-settings-*` `.nt-setting*` `.nt-tabs` `.nt-table--simple` `.nt-skeleton` | 骨架颜色 `rgba(55,53,47,.06)` 原本写在正文里，已收进 token `--color-skeleton` |
| §5.10 Side Peek（面板、顶栏、属性列表、添加属性、评论、空页面提示） | CSS `.nt-peek` `.nt-prop*` `.nt-comments*` `.nt-empty-hint--page` | 封面图和内容缩进属于布局，留给产品 |
| §5.11 浮动帮助按钮 | CSS `.nt-fab`；默认位置（右下角）写在 DESIGN §7（v0.2 补上） | 草稿里写的"`--color-fill-hover-solid-on-page`-like #efefee"已收进 token `--color-fill-hover-floating` |
| §6 图标（风格、尺寸、颜色、实心字形、页面图标） | CSS `.nt-icon--*` + DESIGN §1 + lint `icon-lib` | 允许 inline SVG 或 Lucide（见 CHANGELOG 的取舍） |
| §7 动效表 | CSS（每个组件自带 transition） | |
| §7 Side Peek / 搜索的进场动效估计 | CSS `--duration-peek`（`[选定 D2]`）、`.nt-peek` `.nt-search` | |
| §7 reduced-motion | CSS §19 + DESIGN §6 | |
| §8 Prohibitions 1–10 | DESIGN §5 | 第 1 条（颜色）由 lint `color` 检查；第 4、8 条在 CSS 里已经保证；另外新增"不覆写 nt- 类"（lint `nt-override`） |
| §9 `[模式]` 默认值 | DESIGN §7 | |
| §10 已知缺口 | DESIGN §9 | 有 D2 估计的写明"待补采" |

## 3. `analysis.md` 用到的部分

- §0 品牌资产替换 → DESIGN §8 文案（不出现品牌名）、样板页用通用文案。第三方翻译插件按钮、搜索水印：**删除**，它们不属于产品。
- D2 缺口、草稿里没有组件的：勾选态、toast、引用（analysis 写 can't tell，取 apps 的一致做法）；代码块（底色取根变量，排版取 apps）；tooltip（按 popover 配方缩小一档）；确认弹窗（取 Settings 弹窗配方）。`[选定 D2]` 标在 `tokens.css` 或 `notion.css` 对应的注释里（`.nt-dialog` 的标注 v0.2 补上）。

## 4. 三个 app 的写法 → 本包的类

| app 里的写法 | 本包 | 差异 |
|---|---|---|
| `button--primary` / `btn--primary` | `nt-btn nt-btn--primary` | 相同 |
| `button--secondary` / `btn--soft` | `nt-btn--secondary` | 统一名字 |
| `button--ghost`（notes / pomodoro 灰字）、`btn--ghost`（music 正文色） | `nt-btn`（正文色）/ `nt-btn--muted`（灰字） | 两种都保留，分成两个类 |
| `button--crumb` / `crumb` / `breadcrumb` | `nt-crumb` | 图标和文字间距按草稿取 5px |
| `icon-button` / `icon-btn` | `nt-icon-btn` | 相同 |
| `sidebar-row` + `is-menu-open` | `nt-row` + `is-open` | 状态类统一 |
| `sidebar-section.is-collapsed` | 父元素 `is-open` + `.nt-disclosure` | 默认是折叠角度，展开时加 `is-open` |
| `menu__item.is-active` / `menu-item.is-active` | `nt-menu__item.is-selected` | 状态类统一 |
| `menu__item--danger`（notes 红色） | **不提供** | 原版菜单里的删除不带红色 |
| `checkbox.is-checked`（notes）/ `input:checked`（pomodoro） | `nt-checkbox` + `:checked` 或 `is-on` | 描边取草稿的单根细线（notes 是 1.5 倍） |
| `popover.is-visible` / `modal.is-visible`（pomodoro） | `is-open` | 状态类统一 |
| `table__row.is-open`（pomodoro）、`track.is-current`（music） | `nt-table__row.is-current` | 状态类统一 |
| `track:hover` 加底色（music） | 不加 | 原版表格行 hover 没有底色 |
| `code-block`（notes，callout 底色） | `nt-code-block`（`--color-bg-code-block`） | 换成根变量的值 |
| `toast`（notes / music） | `nt-toast-region > nt-toast` | 定位改由 region 负责，toast 本身只管外观 |
| `slider`、`play-btn`、`tile`（music）、`timer` / `progress` / `stat`（pomodoro） | 不进包 | 只有一个产品用到，是产品组件，留在各自的 `project.css` |

## 5. lint 检查结果（2026-11-07，`node checks/lint.mjs apps/<name>`）

| app | 文件数 | 问题数 | 退出码 | 提示 |
|---|---|---|---|---|
| notes | 8 | 0 | 0 | TSX 里没有用到任何 nt- 类 |
| music | 9 | 0 | 0 | 同上 |
| pomodoro | 8 | 0 | 0 | 同上 |
| `style/`（样板页 + notion.css 自检） | 3 | 0 | 0 | — |

怎么理解：三个 app 本来就严格只用 token。它们的 `tokens.css` 和本包 §1–15 相同（§16 是本包新增，apps 的拷贝里没有），所以颜色、写死数值、变量引用、图标库、字体这几项都通过了。只有 `max-width: 480px` 和 `360px` 两处 px，属于布局几何白名单。三个 app 都**还没有迁到 nt- 类**，所以 nt-override / nt-unknown 两项暂时检查不到东西。迁移时按第 4 节换类名，删掉各自的 `components.css` 里对应的部分，然后再跑一次 lint。

lint 自身的验证：用一个故意违规的测试项目逐条触发过全部规则（color、颜色名、unit、icon-lib、webfont、nt-override（CSS 和内联两种）、nt-unknown、token-unknown、nt-new）。

lint 查不到、需要人看的各 app 问题，属于各自项目层（`DESIGN.project.md`），v0.2 从风格包里移出。
