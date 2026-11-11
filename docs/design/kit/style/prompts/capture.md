# 设计参考采集需求（交给浏览器自动化 agent）

## 目标
用浏览器自动化，从一个已上线的 Web 产品中采集**原始设计数据**：截图、计算后的样式、动画参数、hover/focus 状态。
采集结果会交给另一个 agent，由它分析并生成设计规范。

**只采集，不分析**：不要总结风格，不要写设计规范，也不要评价好坏。没采到的东西要明确写出来，不要推测补齐。

## 本次对象
`TARGET.md` 写了：参考产品的网址、是否需要登录、要采集哪些界面（协调者从目标产品倒推出来的清单）。下面的清单是通用骨架，按 TARGET.md 增删。
- 产品：见 TARGET.md
- 登录方式：用 headed 浏览器（Playwright `launchPersistentContext` 或 CDP 连接用户的 Chrome），由**用户手动登录**，agent 不处理账号密码
- **使用一个只放演示内容的工作区**：截图里会出现工作区内容，不要用真实工作区

## 环境要求
- 视口 1440×900，`deviceScaleFactor: 1`（截图像素 = CSS 像素），浅色模式
- 记录浏览器版本和采集日期
- 可选：深色模式再采一轮，放在 `dark/` 子目录

## 工具
`extract-styles.js`（在本目录）：注入页面后提供 `window.__ds`
- `__ds.scan(label)`：扫描当前画面所有可见元素的计算样式，统计颜色、字体组合、间距、圆角、阴影、边框、transition、控件高度、图标尺寸，以及大块区域
- `__ds.watch()` / `__ds.mark(label)` / `__ds.stop()`：记录期间发生的 CSS transition、CSS animation 和 Web Animations（包括时长、缓动、起止关键帧），以及 hover 前后的样式差异
- `__ds.dump()`：下载 JSON。也可以直接 `page.evaluate(() => __ds.dump())` 拿到返回值，再写入文件

注入方式：`page.addScriptTag({ content })`。可以直接用这个工具，也可以自己写更好的，但输出要包含同样的信息。
注意：SPA 内部导航后 `__ds` 还在；整页刷新后需要重新注入。

## 采集清单

### A. 界面（每个界面：截图 + `__ds.scan`）
| ID | 界面 | 说明 |
|---|---|---|
| V1 | 主界面 | 导航展开，内容区有标题、段落、列表等典型内容 |
| V2 | 列表 / 表格视图 | 至少 10 行，多种字段类型 |
| V3 | 设置页或设置弹窗（两个不同分区） | 有表单、按钮、开关、下拉 |
| V4 | 搜索 / 命令面板 | 空输入、有结果各一张 |
| V5 | 下拉菜单 / 右键菜单 | |
| V6 | 详情面板 / 侧边预览 / 弹窗 | |
| V7… | TARGET.md 补充的界面 | |

截图存为 `screenshots/V1.png` 这样的文件名；如果界面可以滚动，额外存一张整页截图 `V1-full.png`。

### B. 交互动画（每项：`mark` → 操作 → 等待 1 秒 → 下一项）
| ID | 操作 | mark 名称 |
|---|---|---|
| I1 | 打开弹窗；等待；按 Esc 关闭 | `I1-open`、`I1-close` |
| I2 | 打开搜索 / 命令面板；输入 3 个字符；按 ↓ 三次；按 Esc | `I2-open`、`I2-type`、`I2-arrow`、`I2-close` |
| I3 | 导航树展开一个节点，再折叠 | `I3-expand`、`I3-collapse` |
| I4 | 折叠整个侧边栏，再展开（如果有） | `I4-collapse`、`I4-expand` |
| I5 | 打开一个下拉菜单，再关闭 | `I5-open`、`I5-close` |
| I6 | 打开详情面板 / 侧边预览，再关闭 | `I6-open`、`I6-close` |

可选：对每项交互用 Playwright `recordVideo` 录屏，存入 `videos/I1.webm` 等。

### C. hover 与 focus 状态
在 `watch()` 期间，用 `page.hover()` 逐个悬停以下元素，每个停留 300ms 后移开：
- 导航项、列表 / 表格行、表头、设置里的导航项、主按钮和次按钮、图标按钮、菜单项、内容块（有些产品 hover 时出现手柄）

再用 Tab 键在设置弹窗中移动焦点，对**获得焦点的元素**记录 `outline`、`box-shadow`、`border-color` 的计算值。工具不会自动记录 focus 样式，需要单独写一段代码。

### C2. 从目标产品倒推的补充项 `[v0.2]`
列出目标产品需要、而上面没覆盖的内容，在参考产品里找对应的实例来采集（TARGET.md 会列出）。例如目标是知识库产品时：
- 内容块：代码块（含语法高亮）、行内代码、引用、Markdown 表格、公式、列表、文字选中色
- 反馈：toast、错误提示、危险按钮、确认弹窗
- 如果目标产品需要深色模式，深色模式就是必采项
- JS 驱动的动画（侧边详情面板、命令面板、侧边栏折叠）：用 `recordVideo` 录屏，必采

### D. 关键组件的精确样式（补充）
对以下元素，直接读取 `getComputedStyle` 的完整盒模型和文字属性（font-*、line-height、color、background、padding、border、border-radius、box-shadow、height），每个元素都附一张截图：
- 侧边栏 / 导航容器、导航项（普通、选中）
- 弹窗容器（含遮罩层的背景色）
- 主按钮、次按钮、文本输入框、下拉框、开关、勾选框
- 表格的表头单元格和数据单元格
- 搜索面板容器、输入框、选中的结果项
- 菜单容器、菜单项（普通和选中）
- 页面标题、正文段落、H1/H2/H3

## 输出
```
capture/
├── manifest.md        # 必须：每个文件对应什么；哪些项失败了、原因是什么
├── env.json           # 浏览器版本、视口、DPR、日期、配色模式
├── screenshots/       # V1.png ...、D-组件名.png
├── extract.json       # __ds.dump() 的结果（包含 A、B、C）
├── focus.json         # C 部分的 focus 样式
├── components.json    # D 部分的组件样式
└── videos/            # 可选
```

## 已知限制（如实记录到 manifest.md）
- 用 JS 逐帧驱动的动画（requestAnimationFrame）记录不到。如果某个交互明显有动画，但 `animations` 里没有对应记录，在 manifest 里注明"有可见动画但未捕获"
- class 名可能是混淆过的，不要依赖它判断元素用途，结合截图位置来判断
- 遇到反自动化检测、弹窗引导、权限限制时，停下来告诉用户，不要绕过
