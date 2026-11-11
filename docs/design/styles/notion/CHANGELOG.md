# CHANGELOG

## v0.2 · 2026-11-07

依据：`reports/CONSISTENCY-v0.1.md`（42 条）和一份延续验证报告（S8，`reports/S8-*-REPORT.md`）里的「① ② ③」。产品专属的部分留在项目层，不进本包。逐条处理记录见 `out/CHANGES.md`。DECISIONS.md 原样保留（仍是 D1、D2）。

### 删除 / 改名（旧名 → 新名）

| 旧 | 新 | 说明 |
|---|---|---|
| `.nt-pill` | 无替代（需要提示时用 `.nt-badge` 或一行 `.nt-empty-hint`） | 采集里的顶栏 pill 是第三方翻译插件注入的，不属于原版（CONSISTENCY M13）。lint 遇到会直接提示 |
| `.nt-pill__close` | 无替代 | 同上 |
| token `--color-fill-accent-pill` | 无替代 | 同上 |
| `.nt-sidebar-tab`（未选中一律变淡） | `.nt-sidebar-tab`（不变淡）+ `.nt-sidebar-tab--icon`（只有图标的才变淡） | 类名没变，但**行为变了**：只有图标的 tab 要补上 `--icon` 修饰类，否则不再变淡（L6） |

其余类名全部保留。

### 新增类（共 15 个；notion.css 现在 251 个 `nt-` 类）
- `.nt-table__group` + `__group-count`：表格分组头（推导，套侧栏分组标题的配方）。
- `.nt-comment-list`、`.nt-comment` + `__main __head __author __time __text`：评论线程（D2 估计）。
- `.nt-dialog--confirm`：确认弹窗宽度。
- `.nt-editable`：可编辑文字（contenteditable），没有焦点环，空时显示 `data-placeholder`。
- `.nt-layer` + `__catcher __anchor`：浮层定位层（只管位置和"点外面关闭"，没有外观）。
- `.nt-sidebar-tab--icon`：见上表。

### 新增 token
- `--layout-dialog-confirm-w`（`[选定 D2]`）、`--space-popover-offset`（`[推导]`，待补采）、`--icon-stroke-width`（`[估计]`）。

### CSS 修正（外观或状态和规则对不上）
- `.nt-menu__item:focus-visible` 不再去掉焦点环（H2）。菜单项禁用改为只变灰，不再同时挂"整体 0.4"（L5）。
- 焦点环列表补上 `.nt-row__toggle` `.nt-block-handle` `.nt-promo__link` `.nt-setting__more`；树行展开箭头、块拖拽把手在键盘聚焦时可见（M10）。
- `.nt-prose a` 改成和 `.nt-link` 一致（蓝字、无下划线）（M8）。**渲染 Markdown 的页面链接颜色会变**。
- `.nt-prose table` 去掉外框，和 `.nt-content-table` 一致（M7）。
- reduced-motion 补上开关圆钮的位移（L20）。
- 注释：来源说明（D2 的估计取自哪里）、圆角借用、tooltip 配方、danger 使用范围、通用说法替换产品概念（M2、M5、L4、L9、L10、L17）。

### lint
- `nt-override` 对控件类（按钮、行、输入框、tab、菜单项、表格行 / 格……）另外检查 `height` / `min-height` / `max-height` / `padding*`，CSS 和内联 style 都查（M16）。容器类（侧栏、Peek、弹窗、菜单）的尺寸仍算布局。
- `nt-unknown` 遇到 v0.2 删除的类，直接给出替代写法。
- 头注释和实际行为对齐（L18）。

### DESIGN.md（137 行）
- 改写的规则：蓝色白名单（补框选 halo、在详情里打开的行、搜索进度微光；删顶栏 pill）、"每个**面板**一个 700 标题 / 一个 primary"、primary 和 secondary 的判断改成"看是不是面板存在的理由"、细线的使用范围、阴影 / 边框的两个实测例外、焦点环的裸文字输入例外、两种勾选框、toast 可以没有撤销、`is-current` 的定义。
- 新增：分组表格、评论、可编辑标题、浮层定位、零结果搜索、预览卡内容、层级、浮动按钮默认位置、Esc 逐层关闭。
- §9 改写：D2 范围（M1、M3）、pressed（M4）、搜索模糊待补采、细线待核（M17）、待写进 DECISIONS 的取舍清单（H4）。

### MIGRATION.md
- 优先级改成"DECISIONS > 原版采集 / analysis > apps > 草稿"（H4）；修正 D2 来源、apps 的 tokens "完全相同"、`--layout-*` 用途、浮动按钮位置、自检路径（M2、M14、M15、L16）；移出各 app 的问题清单（L17）。

### 样板页
- 新增：分组头、数据勾选框、评论线程、确认弹窗 `--confirm`、无撤销 toast、`.nt-layer`、零结果搜索、`.nt-editable`、菜单项 `is-focus`。
- 修正：占位文字加省略号、空值写 Empty、快捷键放进键帽、按钮选型、图标含义（新增 gear / sliders）、danger / tag 说明、整块空状态示例改成中性内容（L11–L14、L19）。
- 页首说明"样板页按组件分区，不算一个视图"（M11）。

## v0.1 · 2026-11-07

第一版。原来的文字规范（`DESIGN-draft.md` + `tokens.css`）改成"外观交给代码，文字只写判断"。依据：DECISIONS D1、D2；三个 app（notes / music / pomodoro）的实际实现；以及 `analysis.md`。

### 新增
- `tokens.css`：保留草稿里的全部 token 名和值，每个值都带一个来源标签（`[测量]` `[估计]` `[推导]` `[选定 Dn]`）。新增第 16 节，放组件要用、但草稿只在正文里写过的数值：
  - 颜色：`--color-skeleton`、`--color-fill-hover-floating`、`--color-bg-code-block`（D2）、`--color-quote-bar`（D2）。
  - 透明度：`--opacity-disabled`、`--opacity-dim`。
  - 间距：`--space-5`、`--space-7`，以及 `--pad-cell-y`、`--pad-pill-*`、`--pad-chip-start`、`--pad-inline-code-*`。
  - 尺寸：`--size-dot*`、`--size-checkbox`、`--size-round-button`、`--size-split-toggle`、`--size-view-bar`、`--size-sidebar-footer`、`--size-search-footer`、`--size-member-*`、`--size-icon-box`、`--size-avatar-workspace` / `-comment`、`--size-indent`、`--size-bullet`、`--size-drag-handle-*`、`--size-toc-bar-*`、`--size-row-open-ring`、`--size-skeleton-*`、`--size-help-icon`、`--size-quote-bar`、`--size-progress-bar`、`--size-scrim-inset-*`。
  - 文字：`--text-input-*`（15/26，草稿写在正文里）、`--text-quick-action-*`、`--letter-spacing-quick-action`、`--text-promo-title-*`。
  - 动效：`--scale-tooltip-from`、`--rotate-collapsed`、`--duration-tooltip`、`--duration-peek`（D2）。
- `--color-fill-selected` 和 `--color-text-placeholder` 改为引用 `--color-fill-hover` / `--color-text-tertiary`。值不变，只是把"实测同值"这条规则写进了代码。
- `notion.css`：238 个 `nt-` 类（含 `.nt-app`；v0.1 原记录写成 237，v0.2 更正），覆盖按钮、图标按钮、浮动按钮、链接、键帽、徽标、数据标签、状态 pill、通知点、头像、输入框、导航搜索框、开关、勾选框、列表行 / 树行、分组标题、侧栏、顶栏、面包屑、tab / 视图 tab / chip / 设置导航、popover / 菜单 / 插入菜单、tooltip、弹窗、toast、空状态、骨架屏、进度条、数据表 / 简单表、设置页内容、搜索面板 / 结果行 / 预览卡、Side Peek / 属性行 / 评论输入、文档块、callout、行内代码、代码块、引用、内容表、大纲条、`.nt-prose`。同时写入了焦点环、禁用、reduced-motion。
- 统一状态类：`is-selected` `is-current` `is-open` `is-on` `is-error` `is-working` `is-faded`；样板页专用 `is-hover` `is-active` `is-focus`。
- `components.html`：样板页，无需联网就能打开。（v0.1 原记录写"展示每个类的每种状态"；实际 `nt-dialog--settings` 和 `nt-toast-region` 没有展示，v0.2 更正。）
- `checks/lint.mjs`：由 example 的 lint 改造。规则有 color / unit / icon-lib / webfont / nt-override / nt-unknown / token-unknown；另有三种只提示、不计数的输出：nt-new、token-local，以及项目没用 nt- 类时的提醒。能检查 `.html`，样板页可以自检。
- `DESIGN.md`（126 行）、`MIGRATION.md`、`DECISIONS.md`（原样复制）。

### 本版做出的取舍（v0.2 起列在 DESIGN §9「还没写进 DECISIONS 的取舍」，等用户拍板）
- 图标允许"inline SVG 或 Lucide"。analysis §0 建议的是 Lucide / Phosphor，这里只选了 Lucide，避免混用两套字形。
- 菜单里不提供红色"删除"项。notes 用了红色，但 analysis 观察到原版"Move to Trash"不带红色，以原版为准。
- 代码块底色用根变量 `#f7f6f3`。notes 用的是 callout 底色，这里选了更贴近采集的值。
- 表格行 hover 不加底色（原版行为）。music 加了，本包不采纳。
