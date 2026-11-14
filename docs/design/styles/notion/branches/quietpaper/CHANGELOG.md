# CHANGELOG — Quietpaper 风格包

每版一节，新版在上。决策本身记在 `DECISIONS.md`（随包附带）；这里只记风格包文件的变化。逐条的问题来源和处理、以及示例产品项目层的变化，见同一次交付的 `CHANGES.md`（不在风格包目录里）。

## v0.7（2026-11-07）

来源：`CONSISTENCY-v0.6.md`（35 条）、校准第五轮（D17 / D18 / D19）。

### 必须跟着改的地方（改名）
| 旧 | 新 | 说明 |
|---|---|---|
| `.qp-code-line.is-active` | `.qp-code-line.is-current` | `is-active` 不在通用状态类清单里，含义和 `is-current`（键盘 / 光标当前项，外观 = hover）重合。**lint 查不到状态类改名**，请搜索代码里的 `is-active` |

没有删除的类和 token。

### 外观变化（类名不变）
- 带行号的整行（代码行等）的整行评论态（D17）：`--color-line-thread-hover` 从 `--color-state-hover` 改为 `--color-state-selected-hover`，`--color-line-thread-active` 从 `--color-state-selected-hover` 改为 `--color-state-pressed`。三档分开：普通 hover < 指向线程 < 当前线程。
- `.qp-thread.is-selected`（当前评论线程）：从 `--color-state-selected` 改为 `--color-state-selected-hover`，比 hover 的线程深一档（D5 + D17，待用户确认）。
- `.qp-row__hover`（行内 hover 操作）：从只切 `visibility` 改为 `opacity` + `visibility` 按 `--duration-base` 淡入淡出（DESIGN §3.5 的"行附件显隐"）。
- `.qp-modal__nav`：内边距从 `--space-md 0` 改为 `--space-md`，里面的导航行改用 `qp-row--menu`（DESIGN §3.4 早就这样规定，样板原来用错了 `qp-row--nav`）。

### 新增
- token：`--color-line-thread-bar`、`--color-line-thread-bar-stale`（D17：评论竖线 / 引文条的灰色）、`--layout-toast-max-width`（原 `.qp-toast` 里的 `80vw` 字面量）。
- 类：`.qp-modal--sm`（单列表单 / 确认弹窗，原来只能写行内 width）、`.qp-row__hint--strong`（行尾需要读的状态文字，D2）。

### token 整理（值不变）
- 派生值改成引用：`--color-text-link: var(--color-accent-button-hover)`、`--size-icon-box: calc(var(--size-icon-sm) + 2 * var(--space-2xs))`。
- `[估计]` 的定义补上"沿用早期原型、未经采集或评审的值"；`--layout-modal-width-sm` 的注释不再引用某个产品的原型。
- 用途注释改正：`--size-control`（文字按钮、下拉；顶栏里的 ghost 按钮也是它）、`--size-control-sm`（独立图标按钮，不是"顶栏按钮"）、`--motion-row-affordance-opacity`（只给表格行附件）、`--color-state-selected` / `-selected-hover`（当前线程见上）。

### 文档
- `DESIGN.md`：事实来源改为 D1–D19；§1 加 D19（阅读区不加装饰色）和 D17（行上的评论标记全中性）；§3.2 计数规则按 D18 / D2 改写、加 `qp-row__hint--strong`、状态圆点不占图标位、标签栏按每个标签算一行；§3.3"最多 ui + 一个辅助级别"改成按行 / 子组件计算，键帽和徽标不算级别；§3.5 hover 时长分行类（instant）和独立控件（fast）；§3.1 split 按钮算一个 primary；§4 渐变的例外（骨架 shimmer、同色叠底）、引文不加彩色边条、lint 规则补 `token-unknown`；§6 中文字数不计夹在里面的英文词；§7 补命令面板预览区没有类、`[估计]` 含早期原型占位值；§0 补 vw / vh / 百分比上限也要提成 token。
- `quietpaper.css`：去掉对某个产品参考实现的引用（`(ref)`、"提取依据 reference-app"）；过期的章节号 "§9" 改为指向 DESIGN §7；`.qp-btn--inline` 注明要和 `--ghost` 一起用。
- `components.html`：示例文案换成通用内容（搜索快捷键 ⌘K、键帽 `/`、图注、作者名、按钮 aria-label）；树行样板改为演示风格包默认（hover 时图标换成 chevron）；设置弹窗左栏用 `qp-row--menu`；确认弹窗用 `qp-modal--sm`、关闭按钮用独立 `qp-icon-btn`；表格 Open 按钮补 `--ghost`；Drawer 补「»」「↗」；命令面板注明已简化；按钮 / 错误 / 说明文案按 §6 改；插入菜单的 Close menu 行去掉 desc 级。
- `checks/lint.mjs`：版本号 v0.7；`qp-new` 的提示改为指向 `CHANGELOG.md` / `DESIGN.project.md`「新增类及理由」；头部注明 lint 查不到的东西（状态类改名、vw / vh / %）。
- 随包附带 `DECISIONS.md`（事实来源）；不再附带 `MIGRATION-v0.4-to-v0.5.md`。

## v0.6（2026-11-07）

来源：`CONSISTENCY-v0.5.md`（24 条）、A2 / A3 执行报告（这些报告和 v0.6 的 `CHANGES.md` 随 v0.6 交付，不在本包内）。原则：CSS 和意图对不上就修 CSS，两条文字规则冲突就改写成能同时成立的一条，产品专属内容一律移出风格包（D16）。

### 必须跟着改的地方（删除 / 改名）
| 旧 | 新 | 说明 |
|---|---|---|
| `.qp-dot--blue` | 删除，没有替代；用 `.qp-dot--gray` / `.qp-dot--green` | 值和强调色相同，违反"状态圆点不用强调色" |
| `--color-tag-dot-blue` | 删除 | 同上 |
| `--layout-drawer-width` | `--layout-sidebar-drawer-width` | 它是移动端文件树抽屉 `.qp-sidebar--drawer` 的宽度，不是 `.qp-drawer` 的宽度 |
| `.qp-title-input`（原在项目层） | `.qp-title-input`（风格包 `quietpaper.css`），类名不变 | 空文档标题是通用的文档能力；项目里不用改代码 |

新版 lint 会把仍在使用的旧类报成 `qp-unknown`、把旧 token 报成 `token-unknown`。

### 外观变化（类名不变）
- 命令面板 `.qp-command` 入场从弹窗的缩放动画改为 `qp-fade`：命令面板的运动方式没有采集，不再借用弹窗的实测值。
- 整行评论态改为中性 token `--color-line-thread-hover` / `--color-line-thread-active`（原来用评论锚点黄色）。正文里的 `.qp-comment-anchor` 仍是黄色。

### 新增
- token：`--breakpoint-comment`（评论栏折叠的视口宽度）、`--layout-modal-width-sm`（单列表单 / 确认弹窗）、`--color-line-thread-hover`、`--color-line-thread-active`。
- 类：`.qp-sidebar__heading--strong` `.qp-menu__heading--strong`（必须读的分组标题升到 secondary）、`.qp-figcaption`（以及 `.qp-prose figcaption`）、`.qp-thread__reply` `.qp-thread__reply-actions`、`.qp-title-input`（从项目层移入）。
- 样板页：工作区切换和列表页页头的组合示例、标题输入框、图注、线程回复、强分组标题。
- lint：`qp-unknown`（TSX 里用了风格包和项目里都没有定义的 qp- 类）、`token-unknown`（`var()` 引用了没有定义的自定义属性）；配置项新增 `tokenCss`。

### token 整理（值不变）
- 注释里写"= --x"的派生值改成 `var(--x)`：`--color-lang-other`、`--color-code-line-number`、`--color-code-line-active`、`--shadow-ring`。
- `--color-text-disabled` 标签 `[推导]` → `[测量]`（和 `--color-icon-disabled` 同为实测值）。
- 注释补充：图标语义色只管对象 / 功能图标，状态图标用状态色；`--breakpoint-wide` 的推导前提和收缩顺序；`--layout-peek-width` 就是 `.qp-drawer` 的宽度；`--layout-menu-max-height` 也给其他长菜单用；`--focus-ring` 内环色、`--color-border-edge` 是独立的实测值。

### 文档
- `DESIGN.md`：去掉"本产品"一节（移到项目的 `DESIGN.project.md`）；改写互相矛盾的规则（primary 落在哪一行、一行一个有色元素的范围、状态文字着色、ui / body 混用、动效时长、删除反馈、确认弹窗的层级、遮罩的层级、收缩顺序、交互反馈与行附件、已知不足的措辞）；"+ New sub-item" → "+ Add sub-item"。
- `components.html`：只放通用类，不再引用项目文件，示例文案里的产品内容换成通用内容。产品样板移到项目自己的 `components.html`。
- 新增本文件，代替 `MIGRATION-v0.4-to-v0.5.md` 记录版本。
- 版本号统一为 v0.6（`tokens.css` 原来写的是 "v0.4"，`DESIGN.md` 原来没写版本号）。

### 项目层
风格包本身不含产品内容（D16）。示例产品项目层的变化（包括原来写在这里的 `.qp-risk-dot` 删除、`.qp-approval__cmd--clip` 修正）记在交付的 `CHANGES.md` 里，不进本文件。

## v0.5

文字规范拆成"组件 CSS + 精简 DESIGN.md（≤150 行）+ lint 脚本"：外观移进 `quietpaper.css` / `project.css` / `project-tokens.css`，判断留在 `DESIGN.md`，机械约束交给 `checks/lint.mjs`（color / unit / icon-lib / qp-override / qp-new）。逐条去向见当时的 `MIGRATION-v0.4-to-v0.5.md`（随 v0.5 交付；里面有产品层内容，v0.7 起不再随风格包附带）。

## v0.4

文字版规范：`DESIGN.md`（463 行）+ `DESIGN.project.md`（479 行）+ `tokens.css`。
