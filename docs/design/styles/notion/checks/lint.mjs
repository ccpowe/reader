#!/usr/bin/env node
// notion lint v0.2 —— 检查项目里的 CSS / TSX / HTML 是否绕过了风格包。
//
// 用法：node lint.mjs <项目目录> [--json] [--config <文件>]
//   默认读取 <项目目录>/nt-lint.config.json（可选）。有问题时退出码 1，参数错误退出码 2。
//
// 规则：
//   color      写死的颜色值（#hex、rgb()/rgba()、hsl()/hsla() 等颜色函数、颜色属性里的颜色名如 red / white）出现在 token 文件之外
//   unit       写死的 px / ms / s 值出现在 token 文件之外（布局几何属性可白名单；0 不算）
//   icon-lib   引入了规定之外的图标库。允许：项目内手绘 inline SVG（DESIGN.md「图标」）或 Lucide（lucide / lucide-react / lucide-static …）
//   webfont    引入了网页字体（@font-face、@import / <link> 字体 CDN）：本包只用系统字体栈 --font-sans / --font-mono
//   nt-override 在 notion.css 之外覆写风格包的 nt- 类，或在 project.css 之外覆写项目的 nt- 类：
//              视觉属性（颜色、背景、边框、阴影、圆角、描边 / 填充）；控件类（按钮、行、输入框、tab…）
//              另外还查高度和内边距（height / min-height / max-height / padding*）——控件高度属于视觉层。
//              容器类（侧栏、Side Peek、弹窗、菜单…）的尺寸算布局，不查。包括 TSX / HTML 里给带 nt- 类的元素写 style
//   nt-unknown  TSX / JSX 里用到的 nt- 类在 notion.css、project.css 和项目自己的 CSS 里都没有定义
//              （常见原因：风格包升级后类被删除或改名，见 CHANGELOG.md；已删除的类会直接给出替代写法）。
//              以 - 结尾的前缀（模板拼接）不检查
//   token-unknown 用 var(--x) 引用了没有定义的自定义属性（风格包 tokens.css、风格包 CSS、项目 CSS、TSX 内联 style 里都没有）
//              项目自带的 *tokens.css 拷贝也算定义，但只在拷贝里有、风格包里没有的会作为 token-local 提示
//   nt-new     （提示，不计入退出码）在 notion.css / project.css 之外新建了 nt- 类：通用类请移进 notion.css 并记入 CHANGELOG.md，
//              产品类请移进 project.css 并记入 DESIGN.project.md「新增类及理由」
//
//   also：.html 文件里的 class="" / style="" / <style> / <link> 也会检查（样板页可以自检）
//
// 查不到的（需要人看）：状态类用错（如菜单键盘行写成 is-active 而不是 is-selected）、vw / vh / 百分比字面量、
//                      一个视图里 primary 按钮的数量、颜色是否只出现在允许的位置等规则层面的用法错误
//
// 配置（nt-lint.config.json，全部可选）：
// {
//   "ignore": ["generated/", "legacy.css"],          // 路径包含这些子串的文件不检查
//   "tokenFiles": ["theme-tokens.css"],               // 额外的 token 文件名（默认：*tokens.css）
//   "allowPx": {
//     "properties": ["grid-template-columns"],        // 额外允许写 px 的属性（CSS 写法）
//     "selectors": ["^\\.legacy-"]                     // 选择器匹配这些正则时允许写 px（布局几何）
//   },
//   "baseCss": "../style/notion.css",             // 风格包 CSS 的位置（默认先在项目里找，再找 lint.mjs 的上一级）
//   "projectCss": "../project/project.css",
//   "tokenCss": ["../style/tokens.css", "../project/project-tokens.css"]  // 默认：两个 CSS 旁边的 *tokens.css
// }

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))

// ---------- 参数 ----------
const args = process.argv.slice(2)
const asJson = args.includes('--json')
const cfgIdx = args.indexOf('--config')
const positional = args.filter((a, i) => !a.startsWith('--') && args[i - 1] !== '--config')
if (positional.length !== 1) {
  console.error('用法：node lint.mjs <项目目录> [--json] [--config <文件>]')
  process.exit(2)
}
const ROOT = path.resolve(positional[0])
if (!fs.existsSync(ROOT) || !fs.statSync(ROOT).isDirectory()) {
  console.error(`不是目录：${ROOT}`)
  process.exit(2)
}
const cfgPath = cfgIdx >= 0 ? path.resolve(args[cfgIdx + 1]) : path.join(ROOT, 'nt-lint.config.json')
let config = {}
if (fs.existsSync(cfgPath)) {
  try { config = JSON.parse(fs.readFileSync(cfgPath, 'utf8')) } catch (e) { console.error(`配置文件解析失败：${cfgPath}\n${e.message}`); process.exit(2) }
}

// ---------- 常量 ----------
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', '.next', 'coverage', '.turbo', '.cache'])
const BASE_NAME = 'notion.css'
const PROJECT_NAME = 'project.css'

// 布局几何：允许写 px 的属性（尺寸本身由产品布局决定，不是视觉规格）
const LAYOUT_PX_PROPS = new Set([
  'width', 'min-width', 'max-width', 'height', 'min-height', 'max-height',
  'inline-size', 'min-inline-size', 'max-inline-size', 'block-size', 'min-block-size', 'max-block-size',
  'flex-basis', 'grid-template-columns', 'grid-template-rows', 'grid-auto-columns', 'grid-auto-rows',
  ...(config.allowPx?.properties || []),
])
// 给布局几何用的自定义属性（如 --nt-table-columns）同样允许 px
const LAYOUT_VAR = /^--[\w-]*-(columns|rows|width|height)$/
const ALLOW_PX_SELECTORS = (config.allowPx?.selectors || []).map(s => new RegExp(s))
const LAYOUT_PX_KEYS_JS = new Set([...LAYOUT_PX_PROPS].map(camel))
// React 里不加 px 的数值属性
const UNITLESS_JS = new Set(['opacity', 'zIndex', 'flex', 'flexGrow', 'flexShrink', 'fontWeight', 'lineHeight', 'order', 'zoom', 'gridRow', 'gridColumn', 'WebkitLineClamp', 'scale'])

const VISUAL_PROP = /^(color|background|background-color|background-image|border|border-(?!collapse$|spacing$)[a-z-]+|box-shadow|outline|outline-color|fill|stroke|text-decoration-color)$/
const VISUAL_KEY_JS = /^(color|background|backgroundColor|backgroundImage|border|border[A-Z]\w*|boxShadow|outline|outlineColor|fill|stroke|textDecorationColor)$/
// 控件的高度 / 内边距也属于视觉层（DESIGN §3、§5-10）；只对控件类检查，容器的尺寸属于布局
const SIZE_PROP = /^(height|min-height|max-height|padding|padding-[a-z-]+)$/
const SIZE_KEY_JS = /^(height|minHeight|maxHeight|padding\w*)$/
const CONTROL_CLASSES = new Set(['nt-btn', 'nt-icon-btn', 'nt-fab', 'nt-input', 'nt-search-field', 'nt-sidebar-search', 'nt-sidebar-tab',
  'nt-row', 'nt-section-label', 'nt-menu__item', 'nt-tab', 'nt-chip', 'nt-nav-item', 'nt-crumb', 'nt-result', 'nt-prop',
  'nt-table__row', 'nt-table__cell', 'nt-table__head-cell', 'nt-table__add', 'nt-table__group', 'nt-quick-action',
  'nt-switch', 'nt-switch__track', 'nt-switch__knob', 'nt-checkbox', 'nt-checkbox__box', 'nt-status', 'nt-tag', 'nt-kbd', 'nt-badge', 'nt-avatar'])
// 已删除的类 → 替代写法（nt-unknown 时直接提示）
const REMOVED_CLASSES = {
  'nt-pill': 'v0.2 删除（来源是第三方翻译插件，不属于原版）：没有替代，需要提示时用 .nt-badge 或一行 .nt-empty-hint',
  'nt-pill__close': 'v0.2 随 .nt-pill 删除：没有替代',
}
function unknownMsg(c) { return REMOVED_CLASSES[c] ? `用到了已删除的 .${c}：${REMOVED_CLASSES[c]}` : `用到了没有定义的 .${c}：风格包里没有这个类（可能已删除或改名，见 CHANGELOG.md）` }

const RE_HEX = /(^|[^\w&#-])(#[0-9a-fA-F]{3,8})(?![\w-])/g
const RE_COLOR_FN = /\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi
const NAMED_COLORS = /(^|[\s,(])(black|white|red|green|blue|gray|grey|silver|maroon|purple|fuchsia|lime|olive|yellow|navy|teal|aqua|orange|pink|brown|cyan|magenta|gold|indigo|violet|beige|coral|crimson|khaki|lavender|salmon|tomato|turquoise|wheat|tan|plum|orchid|ivory|azure|snow|linen|darkgray|darkgrey|lightgray|lightgrey|dimgray|dimgrey|whitesmoke|gainsboro|slategray|slategrey)(?=$|[\s,);!])/gi
const RE_UNIT = /(^|[^\w.#-])(-?\d*\.?\d+)(px|ms|s)(?![\w-])/g

const ALLOWED_ICONS = /^(lucide|lucide-react|lucide-static|lucide-vue-next|lucide-svelte|lucide-preact|lucide-solid|@lucide\/.+)$/
const WEBFONT_URL = /(fonts\.googleapis|fonts\.gstatic|use\.typekit|fonts\.bunny|rsms\.me\/inter|fontsource|\.woff2?(\?|$)|\.ttf(\?|$)|\.otf(\?|$))/i
const ICON_LIBS = /^(@tabler\/icons[\w-]*|react-icons(\/.*)?|heroicons|@heroicons\/.+|@mui\/icons-material(\/.*)?|@material-ui\/icons(\/.*)?|@fortawesome\/.+|font-awesome|feather-icons|react-feather|@phosphor-icons\/.+|phosphor-react|@radix-ui\/react-icons|ionicons|@ionic\/icons|@iconify\/.+|@iconify-icons\/.+|bootstrap-icons|react-bootstrap-icons|remixicon|@remixicon\/.+|boxicons|material-icons|material-symbols|@material-symbols\/.+|@primer\/octicons(-react)?|octicons|@ant-design\/icons|@carbon\/icons(-react)?|@heroicons\/react)$/
const ICON_URL = /(lucide|heroicons|font-?awesome|feather|phosphor|ionicons|iconify|bootstrap-icons|remixicon|boxicons|material-(icons|symbols)|octicons)/i

function camel(s) { return s.replace(/-([a-z])/g, (_, c) => c.toUpperCase()) }
function isTokenFile(f) {
  const b = path.basename(f)
  return /(^|[-._])tokens\.css$/.test(b) || (config.tokenFiles || []).includes(b)
}
function shown(f) { const r = path.relative(process.cwd(), f); return r.startsWith('..') ? f : r }
function rel(f) { return path.relative(ROOT, f) || path.basename(f) }

// ---------- 收集文件 ----------
const files = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) walk(p); continue }
    if ((config.ignore || []).some(s => p.includes(s))) continue
    if (/\.(css|tsx|jsx|html)$/.test(e.name) || e.name === 'package.json') files.push(p)
  }
})(ROOT)

// ---------- 风格包类名 ----------
function findSystemCss(name, explicit) {
  if (explicit) return path.resolve(ROOT, explicit)
  const inProject = files.find(f => path.basename(f) === name)
  if (inProject) return inProject
  const besideLint = path.resolve(HERE, '..', name)
  return fs.existsSync(besideLint) ? besideLint : null
}
const baseCssPath = findSystemCss(BASE_NAME, config.baseCss)
const projectCssPath = findSystemCss(PROJECT_NAME, config.projectCss)
function classesDefinedIn(file) {
  const set = new Set()
  if (!file) return set
  const { rules } = parseCss(stripCssComments(fs.readFileSync(file, 'utf8')))
  for (const r of rules) for (const m of r.selector.matchAll(/\.(nt-[\w-]+)/g)) set.add(m[1])
  return set
}
const BASE_CLASSES = classesDefinedIn(baseCssPath)
const PROJECT_CLASSES = classesDefinedIn(projectCssPath)
for (const c of BASE_CLASSES) PROJECT_CLASSES.delete(c)

// ---------- 已定义的自定义属性（token-unknown 用） ----------
function tokenCssFiles() {
  if (config.tokenCss) return config.tokenCss.map(f => path.resolve(ROOT, f))
  const out = new Set()
  for (const css of [baseCssPath, projectCssPath]) {
    if (!css) continue
    const dir = path.dirname(css)
    for (const f of fs.readdirSync(dir)) if (isTokenFile(f)) out.add(path.join(dir, f))
  }
  return [...out]
}
const PKG_PROPS = new Set()        // 风格包 tokens.css / notion.css / project.css
const LOCAL_TOKEN_PROPS = new Set() // 项目自带的 *tokens.css（常见：风格包的旧拷贝）
const APP_PROPS = new Set()        // 项目自己的 CSS / TSX / HTML 里定义的
function collectProps(src, set) { for (const m of src.matchAll(/(?:^|[;{\s'"])(--[\w-]+)\s*['"]?\s*:/g)) set.add(m[1]) }
const pkgFiles = [...tokenCssFiles(), baseCssPath, projectCssPath].filter(f => f && fs.existsSync(f)).map(f => path.resolve(f))
for (const f of pkgFiles) collectProps(stripCssComments(fs.readFileSync(f, 'utf8')), PKG_PROPS)
for (const f of files) {
  if (pkgFiles.includes(path.resolve(f)) || !/\.(css|tsx|jsx|html)$/.test(f)) continue
  collectProps(f.endsWith('.css') ? stripCssComments(fs.readFileSync(f, 'utf8')) : fs.readFileSync(f, 'utf8'), isTokenFile(f) ? LOCAL_TOKEN_PROPS : APP_PROPS)
}
const tokenLocal = new Map()  // 只在项目 token 拷贝里有定义的变量 → 引用次数
// 项目自己的 CSS 里新建的 nt- 类（nt-new 已提示），TSX 用到它们不算 nt-unknown
const APP_CLASSES = new Set()
for (const f of files) if (f.endsWith('.css') && !isTokenFile(f)) for (const c of classesDefinedIn(f)) APP_CLASSES.add(c)
function checkVarRefs(file, line, text, ctx) {
  for (const m of text.matchAll(/var\(\s*(--[\w-]+)\s*([,)])/g)) {
    if (m[2] === ',') continue  // 带回退值的引用不算
    if (PKG_PROPS.has(m[1]) || APP_PROPS.has(m[1])) continue
    if (LOCAL_TOKEN_PROPS.has(m[1])) { tokenLocal.set(m[1], (tokenLocal.get(m[1]) || 0) + 1); continue }
    report('token-unknown', file, line, `引用了没有定义的 ${m[1]}：确认 token 名（风格包升级后可能已改名，见 CHANGELOG.md）`, ctx)
  }
}

// ---------- 结果 ----------
const problems = []
const notes = []
function report(rule, file, line, msg, snippet) {
  ;(rule === 'nt-new' || rule === 'token-local' ? notes : problems).push({ rule, file: rel(file), line, msg, snippet: (snippet || '').trim().slice(0, 120) })
}

// ---------- CSS ----------
function stripCssComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, ' '))
}
// 极简 CSS 解析：返回每条声明及其所在规则的选择器、外层 @规则
function parseCss(src) {
  const rules = []            // { selector, line }
  const decls = []            // { prop, value, line, selector, atRules }
  const imports = []          // { text, line }
  const stack = []            // { prelude, isAt }
  let buf = '', bufStart = -1
  const lineAt = (() => { const nl = []; for (let i = 0; i < src.length; i++) if (src[i] === '\n') nl.push(i); return i => { let lo = 0, hi = nl.length; while (lo < hi) { const m = (lo + hi) >> 1; if (nl[m] < i) lo = m + 1; else hi = m } return lo + 1 } })()
  const curSelector = () => { for (let i = stack.length - 1; i >= 0; i--) if (!stack[i].isAt) return stack[i].prelude; return '' }
  const atRules = () => stack.filter(s => s.isAt).map(s => s.prelude)
  const flushDecl = () => {
    const text = buf.trim()
    if (text) {
      const line = lineAt(bufStart)
      if (text.startsWith('@')) { if (/^@import/i.test(text)) imports.push({ text, line }) }
      else {
        const c = text.indexOf(':')
        if (c > 0) decls.push({ prop: text.slice(0, c).trim().toLowerCase(), value: text.slice(c + 1).trim(), line, selector: curSelector(), atRules: atRules() })
      }
    }
    buf = ''; bufStart = -1
  }
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (ch === '"' || ch === "'") {
      const j = src.indexOf(ch, i + 1); const end = j < 0 ? src.length - 1 : j
      if (bufStart < 0) bufStart = i
      buf += src.slice(i, end + 1); i = end; continue
    }
    if (ch === '{') {
      const prelude = buf.trim(); const line = lineAt(bufStart < 0 ? i : bufStart)
      const isAt = prelude.startsWith('@')
      stack.push({ prelude, isAt })
      if (!isAt && !atRules().some(a => /^@(-webkit-)?keyframes/i.test(a))) rules.push({ selector: prelude, line })
      buf = ''; bufStart = -1; continue
    }
    if (ch === ';') { flushDecl(); continue }
    if (ch === '}') { flushDecl(); stack.pop(); continue }
    if (bufStart < 0 && !/\s/.test(ch)) bufStart = i
    buf += ch
  }
  flushDecl()
  return { rules, decls, imports }
}
function splitSelectors(sel) {
  const out = []; let depth = 0, cur = ''
  for (const ch of sel) {
    if (ch === '(' || ch === '[') depth++
    if (ch === ')' || ch === ']') depth--
    if (ch === ',' && depth === 0) { out.push(cur); cur = '' } else cur += ch
  }
  out.push(cur)
  return out.map(s => s.trim()).filter(Boolean)
}
// 选择器真正命中的那个复合选择器（最后一段），去掉 :not()/:has() 等括号内容
function subjectClasses(sel) {
  const flat = sel.replace(/\((?:[^()]|\([^()]*\))*\)/g, '').replace(/\[[^\]]*\]/g, '')
  const parts = flat.split(/\s*[\s>+~]\s*/).filter(Boolean)
  const last = parts[parts.length - 1] || ''
  return [...last.matchAll(/\.(nt-[\w-]+)/g)].map(m => m[1])
}
function scrubValue(v) {
  // 字符串、url() 里的内容不算数值
  return v.replace(/url\([^)]*\)/gi, 'url()').replace(/"[^"]*"|'[^']*'/g, '""')
}
function checkColorAndUnits(file, line, value, ctx) {
  const v = scrubValue(value)
  for (const m of v.matchAll(RE_HEX)) report('color', file, line, `写死的颜色 ${m[2]}：改用 --color-* token`, ctx)
  for (const m of v.matchAll(RE_COLOR_FN)) report('color', file, line, `写死的颜色函数 ${m[1]}()：改用 --color-* token`, ctx)
  const units = []
  for (const m of v.matchAll(RE_UNIT)) if (Number(m[2]) !== 0) units.push(m[2] + m[3])
  return units
}
function lintCss(file, src) {
  const token = isTokenFile(file)
  const base = path.resolve(file) === (baseCssPath && path.resolve(baseCssPath))
  const proj = path.resolve(file) === (projectCssPath && path.resolve(projectCssPath))
  const { rules, decls, imports } = parseCss(stripCssComments(src))
  for (const m of stripCssComments(src).matchAll(/@font-face/g)) report('webfont', file, lineOf(src, m.index), '定义了 @font-face：本包只用系统字体栈 --font-sans / --font-mono', '@font-face')

  for (const imp of imports) {
    const m = imp.text.match(/["']([^"']+)["']|url\(\s*["']?([^"')]+)/i)
    const spec = m && (m[1] || m[2])
    if (spec && ICON_URL.test(spec) && !/lucide/i.test(spec)) report('icon-lib', file, imp.line, `引入了规定之外的图标库：${spec}（只用 inline SVG 或 Lucide）`, imp.text)
    if (spec && WEBFONT_URL.test(spec)) report('webfont', file, imp.line, `引入了网页字体：${spec}（本包只用系统字体栈）`, imp.text)
  }
  if (token) return

  for (const d of decls) {
    const ctx = `${d.selector || d.atRules.join(' ')} { ${d.prop}: ${d.value} }`
    checkVarRefs(file, d.line, d.value, ctx)
    const units = checkColorAndUnits(file, d.line, d.value, ctx)
    if (VISUAL_PROP.test(d.prop) || d.prop === 'fill' || d.prop === 'stroke') for (const m of scrubValue(d.value).matchAll(NAMED_COLORS)) report('color', file, d.line, `写死的颜色名 ${m[2]}：改用 --color-* token`, ctx)
    if (units.length) {
      const pxOnly = units.every(u => u.endsWith('px'))
      const allowed = pxOnly && (LAYOUT_PX_PROPS.has(d.prop) || LAYOUT_VAR.test(d.prop) || ALLOW_PX_SELECTORS.some(r => r.test(d.selector)))
      if (!allowed) report('unit', file, d.line, `写死的 ${units.join(' ')}：改用 --space-* / --size-* / --radius-* / --duration-* 等 token`, ctx)
    }
    const visual = VISUAL_PROP.test(d.prop), size = SIZE_PROP.test(d.prop)
    if (!base && (visual || size) && d.selector) {
      for (const sel of splitSelectors(d.selector)) {
        const owned = c => BASE_CLASSES.has(c) || (!proj && PROJECT_CLASSES.has(c))
        const hit = subjectClasses(sel).find(c => owned(c) && (visual || CONTROL_CLASSES.has(c)))
        if (hit) { report('nt-override', file, d.line, `重新定义了 .${hit} 的 ${d.prop}：外观（含控件高度 / 内边距）以风格包为准；需要新外观请组合现有类或新建 nt- 类并记录`, `${sel} { ${d.prop}: ${d.value} }`); break }
      }
    }
  }
  if (!base && !proj) {
    const seen = new Set()
    for (const r of rules) for (const sel of splitSelectors(r.selector)) for (const c of subjectClasses(sel))
      if (!BASE_CLASSES.has(c) && !PROJECT_CLASSES.has(c) && !seen.has(c)) { seen.add(c); report('nt-new', file, r.line, `新建了 .${c}：产品类请放进 project.css 并在 DESIGN.project.md「新增类及理由」记一行；通用类放进 notion.css 并记入 CHANGELOG.md`, r.selector) }
  }
}

// ---------- TSX / JSX ----------
// 去掉注释，保留字符串（用于后续扫描），返回 { code, strings: [{ value, index, quote }] }
function scanJs(src) {
  let code = '', i = 0
  const strings = []
  while (i < src.length) {
    const ch = src[i], nx = src[i + 1]
    if (ch === '/' && nx === '/') { const j = src.indexOf('\n', i); const end = j < 0 ? src.length : j; code += ' '.repeat(end - i); i = end; continue }
    if (ch === '/' && nx === '*') { const j = src.indexOf('*/', i + 2); const end = j < 0 ? src.length : j + 2; code += src.slice(i, end).replace(/[^\n]/g, ' '); i = end; continue }
    if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1
      while (j < src.length && src[j] !== ch) { if (src[j] === '\\') j++; if (ch !== '`' && src[j] === '\n') break; j++ }
      strings.push({ value: src.slice(i + 1, j), index: i, quote: ch })
      code += src.slice(i, j + 1); i = j + 1; continue
    }
    code += ch; i++
  }
  return { code, strings }
}
function lineOf(src, idx) { let n = 1; for (let i = 0; i < idx && i < src.length; i++) if (src[i] === '\n') n++; return n }
// 从 "{{" 开始找到匹配的 "}}"
function matchBraces(code, start) {
  let depth = 0
  for (let i = start; i < code.length; i++) {
    const ch = code[i]
    if (ch === '"' || ch === "'" || ch === '`') { let j = i + 1; while (j < code.length && code[j] !== ch) { if (code[j] === '\\') j++; j++ } i = j; continue }
    if (ch === '{') depth++
    if (ch === '}') { depth--; if (depth === 0) return i }
  }
  return code.length - 1
}
function lintJs(file, src) {
  const { code, strings } = scanJs(src)

  // icon-lib：import / require / 动态 import
  for (const m of code.matchAll(/(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|import\s+)(['"])([^'"]+)\1/g)) {
    const spec = m[2]
    if (ALLOWED_ICONS.test(spec.split('/').slice(0, spec.startsWith('@') ? 2 : 1).join('/')) || /^[./]/.test(spec)) continue
    if (ICON_LIBS.test(spec) || /(^|[/@-])icons?($|[/-])/i.test(spec)) report('icon-lib', file, lineOf(src, m.index), `引入了规定之外的图标库：${spec}（只用 inline SVG 或 Lucide）`, m[0])
    if (/^@fontsource|^typeface-/.test(spec) || WEBFONT_URL.test(spec)) report('webfont', file, lineOf(src, m.index), `引入了网页字体：${spec}（本包只用系统字体栈）`, m[0])
  }

  // style={{ ... }}：写死的数值、颜色、对 nt- 元素的视觉覆盖
  const styleSpans = []
  for (const m of code.matchAll(/style\s*=\s*\{\s*\{/g)) {
    const open = m.index + m[0].length - 1
    const close = matchBraces(code, open)
    const body = code.slice(open + 1, close)
    styleSpans.push([open, close])
    const line = lineOf(src, m.index)
    // 所在 JSX 标签的 className
    const tagStart = code.lastIndexOf('<', m.index)
    let tagEnd = close
    for (let k = close + 1; k < code.length; k++) { if (code[k] === '>' && code[k - 1] !== '=') { tagEnd = k; break } }
    const tag = code.slice(tagStart, tagEnd + 1)
    const cls = (tag.match(/className\s*=\s*(?:\{\s*)?[`'"]([^`'"]*)/) || [])[1] || ''
    const ntOnEl = [...cls.matchAll(/(?:^|\s)(nt-[\w-]+)/g)].map(x => x[1]).filter(c => BASE_CLASSES.has(c) || PROJECT_CLASSES.has(c))
    // 顶层 key: value
    for (const kv of body.matchAll(/(?:^|[,{\s])(['"]?)([\w-]+)\1\s*:\s*([^,]+?)(?=,\s*['"\w-]+\s*:|,?\s*$)/gs)) {
      const key = kv[2], raw = kv[3].trim()
      const snippet = `${key}: ${raw}`
      if (key.startsWith('--')) continue
      if (VISUAL_KEY_JS.test(key) && /^['"`]/.test(raw)) for (const m of raw.slice(1, -1).matchAll(NAMED_COLORS)) report('color', file, line, `写死的颜色名 ${m[2]}：改用 --color-* token`, snippet)
      if (ntOnEl.length && VISUAL_KEY_JS.test(key)) report('nt-override', file, line, `给 .${ntOnEl[0]} 写了内联 ${key}：外观以风格包为准`, snippet)
      else if (SIZE_KEY_JS.test(key) && ntOnEl.some(c => CONTROL_CLASSES.has(c))) report('nt-override', file, line, `给 .${ntOnEl.find(c => CONTROL_CLASSES.has(c))} 写了内联 ${key}：控件高度 / 内边距以风格包为准`, snippet)
      if (/^-?\d+(\.\d+)?$/.test(raw) && Number(raw) !== 0 && !UNITLESS_JS.has(key) && !LAYOUT_PX_KEYS_JS.has(key))
        report('unit', file, line, `内联 style 写死了 ${raw}（React 按 px 处理）：改用 token`, snippet)
    }
  }

  // 字符串里的 nt- 类名、var() 引用
  for (const s of strings) {
    if (/^(https?:|data:|\.{0,2}\/)/.test(s.value) || /\.(json|css|mjs|js|ts|tsx)$/.test(s.value)) continue
    const line = lineOf(src, s.index)
    checkVarRefs(file, line, s.value, s.value)
    for (const m of s.value.matchAll(/(?<![\w-])(nt-[\w-]*[\w])(?![\w-])/g)) {
      const c = m[1]
      if (s.value[m.index + c.length] === '$') continue  // 模板拼接：nt-tag--${hue}
      if (!BASE_CLASSES.has(c) && !PROJECT_CLASSES.has(c) && !APP_CLASSES.has(c))
        report('nt-unknown', file, line, unknownMsg(c), s.value)
    }
  }

  // 字符串里的颜色 / px / ms
  for (const s of strings) {
    const before = code.slice(Math.max(0, s.index - 12), s.index)
    if (/(href|to|id|htmlFor|key)\s*=\s*\{?\s*$/.test(before)) continue
    if (/^(https?:|data:|\.{0,2}\/)/.test(s.value)) continue
    const line = lineOf(src, s.index)
    const v = s.value.replace(/var\(--[\w-]+\)/g, 'var()')
    for (const m of v.matchAll(RE_HEX)) report('color', file, line, `写死的颜色 ${m[2]}：改用 --color-* token`, s.value)
    for (const m of v.matchAll(RE_COLOR_FN)) report('color', file, line, `写死的颜色函数 ${m[1]}()：改用 --color-* token`, s.value)
    const units = [...v.matchAll(RE_UNIT)].filter(m => Number(m[2]) !== 0).map(m => m[2] + m[3])
    if (units.length) {
      // 在 style 对象里、属于布局几何属性的 px 值放行
      const inStyle = styleSpans.find(([a, b]) => s.index > a && s.index < b)
      const keyBefore = (code.slice(Math.max(0, s.index - 40), s.index).match(/([\w-]+)['"]?\s*:\s*$/) || [])[1]
      const pxOnly = units.every(u => u.endsWith('px'))
      if (inStyle && pxOnly && keyBefore && LAYOUT_PX_KEYS_JS.has(keyBefore)) continue
      report('unit', file, line, `写死的 ${units.join(' ')}：改用 token`, s.value)
    }
  }
}

// ---------- HTML ----------
function lintHtml(file, src) {
  const blank = src.replace(/<!--[\s\S]*?-->/g, m => m.replace(/[^\n]/g, ' '))
  // <style> 块按 CSS 检查（保持行号）
  for (const m of blank.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)) {
    const start = m.index + m[0].indexOf('>') + 1
    lintCss(file, blank.slice(0, start).replace(/[^\n]/g, ' ') + m[1])
  }
  // <link href> / <script src>：图标库、网页字体
  for (const m of blank.matchAll(/<(link|script)\b[^>]*?(href|src)\s*=\s*["']([^"']+)["'][^>]*>/gi)) {
    const url = m[3], line = lineOf(src, m.index)
    if (ICON_URL.test(url) && !/lucide/i.test(url)) report('icon-lib', file, line, `引入了规定之外的图标库：${url}`, m[0])
    if (WEBFONT_URL.test(url)) report('webfont', file, line, `引入了网页字体：${url}`, m[0])
  }
  // class="" 里的 nt- 类
  for (const m of blank.matchAll(/\bclass\s*=\s*"([^"]*)"/g)) {
    const line = lineOf(src, m.index)
    for (const c of m[1].split(/\s+/)) if (/^nt-/.test(c) && !BASE_CLASSES.has(c) && !PROJECT_CLASSES.has(c) && !APP_CLASSES.has(c))
      report('nt-unknown', file, line, unknownMsg(c), m[0])
  }
  // style="" 里的写死值、变量引用、对 nt- 元素的视觉覆盖
  for (const m of blank.matchAll(/<[a-z][\w-]*\b([^>]*?)\bstyle\s*=\s*"([^"]*)"([^>]*)>/gi)) {
    const line = lineOf(src, m.index)
    const cls = ((m[1] + m[3]).match(/\bclass\s*=\s*"([^"]*)"/) || [])[1] || ''
    const ntOnEl = cls.split(/\s+/).filter(c => BASE_CLASSES.has(c) || PROJECT_CLASSES.has(c))
    for (const d of m[2].split(';')) {
      const c = d.indexOf(':'); if (c < 0) continue
      const prop = d.slice(0, c).trim().toLowerCase(), value = d.slice(c + 1).trim()
      if (prop.startsWith('--')) continue
      const ctx = `style="${prop}: ${value}"`
      checkVarRefs(file, line, value, ctx)
      const units = checkColorAndUnits(file, line, value, ctx)
      if (units.length && !(units.every(u => u.endsWith('px')) && LAYOUT_PX_PROPS.has(prop))) report('unit', file, line, `内联 style 写死了 ${units.join(' ')}：改用 token`, ctx)
      if (VISUAL_PROP.test(prop)) for (const n of value.matchAll(NAMED_COLORS)) report('color', file, line, `写死的颜色名 ${n[2]}：改用 --color-* token`, ctx)
      if (ntOnEl.length && VISUAL_PROP.test(prop)) report('nt-override', file, line, `给 .${ntOnEl[0]} 写了内联 ${prop}：外观以风格包为准`, ctx)
      else if (SIZE_PROP.test(prop) && ntOnEl.some(c => CONTROL_CLASSES.has(c))) report('nt-override', file, line, `给 .${ntOnEl.find(c => CONTROL_CLASSES.has(c))} 写了内联 ${prop}：控件高度 / 内边距以风格包为准`, ctx)
    }
  }
}

// ---------- package.json ----------
function lintPkg(file, src) {
  let pkg; try { pkg = JSON.parse(src) } catch { return }
  for (const field of ['dependencies', 'devDependencies', 'peerDependencies']) {
    for (const name of Object.keys(pkg[field] || {})) {
      if (ALLOWED_ICONS.test(name)) continue
      if (/^@fontsource\//.test(name)) report('webfont', file, lineOf(src, src.indexOf(`"${name}"`)), `依赖了网页字体：${name}`, `"${name}"`)
      if (ICON_LIBS.test(name)) {
        const idx = src.indexOf(`"${name}"`)
        report('icon-lib', file, lineOf(src, idx), `依赖了规定之外的图标库：${name}`, `"${name}"`)
      }
    }
  }
}

// ---------- 运行 ----------
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8')
  if (f.endsWith('.css')) lintCss(f, src)
  else if (f.endsWith('package.json')) lintPkg(f, src)
  else if (f.endsWith('.html')) lintHtml(f, src)
  else lintJs(f, src)
}

for (const [name, n] of [...tokenLocal].sort()) report('token-local', ROOT, 0, `${name} 只在项目自带的 tokens 拷贝里有定义（${n} 处引用）：风格包 tokens.css 没有这个变量，换成风格包里的 token`, name)
let ntUsed = 0
for (const f of files) if (/\.(tsx|jsx|html)$/.test(f)) ntUsed += (fs.readFileSync(f, 'utf8').match(/(?<![\w-])nt-[\w-]+/g) || []).length
if (!ntUsed && files.some(f => /\.(tsx|jsx|html)$/.test(f))) notes.push({ rule: 'nt-adoption', file: '.', line: 0, msg: 'TSX / HTML 里没有用到任何 nt- 类：项目还在用自己的组件样式，迁移见 MIGRATION.md', snippet: '' })

const byRule = {}
for (const p of problems) byRule[p.rule] = (byRule[p.rule] || 0) + 1

if (asJson) {
  console.log(JSON.stringify({ root: ROOT, baseCss: baseCssPath, projectCss: projectCssPath, total: problems.length, byRule, problems, notes }, null, 2))
} else {
  const RULE_NAME = { color: '写死颜色', unit: '写死 px/ms', 'icon-lib': '规定之外的图标库', webfont: '网页字体', 'nt-override': '覆盖 nt- 视觉属性', 'nt-unknown': '未定义的 nt- 类', 'token-unknown': '未定义的 token', 'nt-new': '新建 nt- 类（提示）', 'token-local': '只在本地 tokens 拷贝里的变量（提示）' }
  console.log(`notion lint · ${ROOT}`)
  console.log(`风格包：${baseCssPath ? shown(baseCssPath) : '（未找到 notion.css，nt-override 规则不生效）'}${projectCssPath ? ' + ' + shown(projectCssPath) : ''}`)
  console.log(`检查了 ${files.length} 个文件\n`)
  const groups = {}
  for (const p of problems) (groups[p.file] ||= []).push(p)
  for (const [file, list] of Object.entries(groups)) {
    console.log(file)
    for (const p of list.sort((a, b) => a.line - b.line)) console.log(`  ${String(p.line).padStart(5)}  [${p.rule}] ${p.msg}\n         ${p.snippet}`)
    console.log('')
  }
  if (notes.length) {
    console.log('提示（不计入总数）：')
    for (const n of notes) console.log(`  ${n.line ? n.file + ':' + n.line + '  ' : ''}${n.msg}`)
    console.log('')
  }
  const summary = Object.entries(byRule).map(([r, n]) => `${RULE_NAME[r] || r} ${n}`).join('，')
  console.log(problems.length ? `共 ${problems.length} 个问题：${summary}` : '没有问题。')
}
process.exit(problems.length ? 1 : 0)
