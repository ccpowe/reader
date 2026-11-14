/*
 * extract-styles.js：在浏览器 DevTools Console 中运行，从任意网页提取设计参数。
 * 只读取样式和元素结构（标签、class、role、尺寸），不读取页面文字内容。
 *
 * 用法（详见 tools/README.md）：
 *   1. 把本文件全部内容粘贴到 Console，回车。之后页面内导航都有效，刷新页面需要重新粘贴
 *   2. __ds.scan('标签')   扫描当前画面的静态样式。每打开一个界面扫一次
 *   3. __ds.watch()         开始记录动画和 hover 状态变化
 *      __ds.mark('动作名')  每次操作前先打标记，比如 __ds.mark('打开设置')
 *      __ds.stop()          停止记录
 *   4. __ds.dump()          下载 JSON 文件
 */
(() => {
  if (window.__ds) { console.log('[ds] 已加载，直接使用 __ds'); return; }

  const MAX_SAMPLES = 3, MAX_ANIM = 600, MAX_HOVER = 400, MAX_TOP = 40;
  const state = { scans: [], animations: [], hovers: [], marker: null, markerAt: 0, watching: false };

  // ---------- 工具函数 ----------
  const describe = (el) => {
    if (!el || el.nodeType !== 1) return null;
    const cls = [...el.classList].slice(0, 3).map(c => c.length > 32 ? c.slice(0, 32) + '…' : c);
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      cls: cls.join('.') || undefined,
      role: el.getAttribute('role') || undefined,
      size: `${Math.round(r.width)}x${Math.round(r.height)}`,
      pos: `${Math.round(r.left)},${Math.round(r.top)}`,
    };
  };
  const descKey = (d) => d ? `${d.tag}.${d.cls || ''}[${d.role || ''}]` : '';

  const counter = () => new Map();
  const bump = (map, key, el) => {
    if (key == null || key === '') return;
    let e = map.get(key);
    if (!e) map.set(key, e = { value: key, count: 0, samples: [] });
    e.count++;
    if (e.samples.length < MAX_SAMPLES && el) e.samples.push(describe(el));
  };
  const top = (map, n = MAX_TOP) => [...map.values()].sort((a, b) => b.count - a.count).slice(0, n);

  const isVisible = (el, cs) => {
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  const hasOwnText = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const transparent = (c) => c === 'rgba(0, 0, 0, 0)' || c === 'transparent';
  const isInteractive = (el) =>
    el.matches('button, a[href], input, select, textarea, [role=button], [role=menuitem], [role=option], [role=tab], [role=switch], [role=checkbox], [contenteditable=true], [tabindex]:not([tabindex="-1"])');

  // 读取同源样式表中 :root / html / body 上定义的 CSS 变量
  const readCustomProps = () => {
    const out = {};
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch { continue; } // 跨域样式表读不到
      const walk = (list) => {
        for (const rule of list) {
          if (rule.cssRules) walk(rule.cssRules);
          if (!rule.style || !/(^|,)\s*(:root|html|body)\b/.test(rule.selectorText || '')) continue;
          for (const p of rule.style) if (p.startsWith('--')) out[p] = rule.style.getPropertyValue(p).trim();
        }
      };
      walk(rules);
    }
    return out;
  };

  // ---------- 静态扫描 ----------
  function scan(label = `scan-${state.scans.length + 1}`) {
    const m = {
      textColor: counter(), bgColor: counter(), borderColor: counter(),
      typography: counter(), textStyle: counter(), fontFamily: counter(),
      padding: counter(), margin: counter(), gap: counter(),
      radius: counter(), shadow: counter(), borderWidth: counter(),
      transition: counter(), controlHeight: counter(), iconSize: counter(),
      zIndex: counter(), opacity: counter(),
    };
    const panels = [];
    let visibleCount = 0;

    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      if (!isVisible(el, cs)) continue;
      visibleCount++;
      const r = el.getBoundingClientRect();

      if (!transparent(cs.backgroundColor)) bump(m.bgColor, cs.backgroundColor, el);
      for (const s of ['Top', 'Right', 'Bottom', 'Left']) {
        const w = cs[`border${s}Width`];
        if (parseFloat(w) > 0 && cs[`border${s}Style`] !== 'none') {
          bump(m.borderColor, cs[`border${s}Color`], el);
          bump(m.borderWidth, `${s.toLowerCase()} ${w}`, el);
        }
      }
      if (hasOwnText(el)) {
        bump(m.textColor, cs.color, el);
        bump(m.fontFamily, cs.fontFamily, el);
        bump(m.typography, `${cs.fontSize} / ${cs.lineHeight} / ${cs.fontWeight}${cs.letterSpacing !== 'normal' ? ' / ls ' + cs.letterSpacing : ''}`, el);
        bump(m.textStyle, `${cs.fontSize} ${cs.fontWeight} ${cs.color}`, el);
      }
      const pad = `${cs.paddingTop} ${cs.paddingRight} ${cs.paddingBottom} ${cs.paddingLeft}`;
      if (pad !== '0px 0px 0px 0px') bump(m.padding, pad, el);
      const mar = `${cs.marginTop} ${cs.marginRight} ${cs.marginBottom} ${cs.marginLeft}`;
      if (mar !== '0px 0px 0px 0px') bump(m.margin, mar, el);
      if (cs.display.includes('flex') || cs.display.includes('grid')) {
        if (cs.rowGap !== 'normal' && cs.rowGap !== '0px' || cs.columnGap !== 'normal' && cs.columnGap !== '0px')
          bump(m.gap, `${cs.rowGap} ${cs.columnGap}`, el);
      }
      const rad = `${cs.borderTopLeftRadius} ${cs.borderTopRightRadius} ${cs.borderBottomRightRadius} ${cs.borderBottomLeftRadius}`;
      if (rad !== '0px 0px 0px 0px') bump(m.radius, rad, el);
      if (cs.boxShadow !== 'none') bump(m.shadow, cs.boxShadow, el);
      if (cs.transitionDuration.split(',').some(d => parseFloat(d) > 0))
        bump(m.transition, `${cs.transitionProperty} | ${cs.transitionDuration} | ${cs.transitionTimingFunction} | delay ${cs.transitionDelay}`, el);
      if (isInteractive(el)) bump(m.controlHeight, `${Math.round(r.height)}px`, el);
      if (el.tagName === 'svg' || el.tagName === 'IMG' && r.width <= 32) bump(m.iconSize, `${Math.round(r.width)}x${Math.round(r.height)}`, el);
      if (cs.position !== 'static' && cs.zIndex !== 'auto') bump(m.zIndex, cs.zIndex, el);
      if (+cs.opacity < 1) bump(m.opacity, cs.opacity, el);

      // 大块区域：侧边栏、主内容、弹窗等，用来理解布局
      if (r.width >= 160 && r.height >= 240 && panels.length < 40 &&
          (!transparent(cs.backgroundColor) || cs.boxShadow !== 'none' || parseFloat(cs.borderRightWidth) > 0 || parseFloat(cs.borderLeftWidth) > 0)) {
        panels.push({ ...describe(el), bg: cs.backgroundColor, shadow: cs.boxShadow !== 'none' ? cs.boxShadow : undefined,
          radius: rad !== '0px 0px 0px 0px' ? rad : undefined, padding: pad, position: cs.position });
      }
    }

    const result = { label, at: new Date().toISOString(), viewport: `${innerWidth}x${innerHeight}`, visibleElements: visibleCount, panels };
    for (const [k, v] of Object.entries(m)) result[k] = top(v);
    state.scans.push(result);
    console.log(`[ds] 扫描「${label}」完成：${visibleCount} 个可见元素。已累计 ${state.scans.length} 次扫描`);
    return { label, colors: m.textColor.size + m.bgColor.size, typography: m.typography.size };
  }

  // ---------- 动画记录 ----------
  const seen = new WeakSet();
  const recordAnim = (a) => {
    if (seen.has(a) || state.animations.length >= MAX_ANIM) return;
    seen.add(a);
    const eff = a.effect; if (!eff) return;
    const t = eff.getTiming ? eff.getTiming() : {};
    let keyframes = [];
    try {
      keyframes = eff.getKeyframes().map(k => {
        const o = {};
        for (const [p, v] of Object.entries(k)) if (!['computedOffset', 'composite'].includes(p) && v != null) o[p] = v;
        return o;
      });
    } catch {}
    state.animations.push({
      marker: state.marker,
      tMs: Math.round(performance.now() - state.markerAt),
      type: a.constructor.name,                 // CSSTransition / CSSAnimation / Animation（JS 创建）
      property: a.transitionProperty || a.animationName || undefined,
      duration: t.duration, delay: t.delay, easing: t.easing, iterations: t.iterations, fill: t.fill,
      keyframes,
      target: describe(eff.target),
    });
  };
  const poll = () => { for (const a of document.getAnimations()) recordAnim(a); };
  const onAnimEvent = (e) => { try { e.target.getAnimations().forEach(recordAnim); } catch {} };

  // ---------- hover 状态差异（也能捕获没有过渡动画的即时变化） ----------
  const HOVER_PROPS = ['backgroundColor', 'color', 'borderTopColor', 'boxShadow', 'opacity', 'textDecorationLine', 'cursor', 'transform'];
  const snap = (el) => Object.fromEntries(HOVER_PROPS.map(p => [p, getComputedStyle(el)[p]]));
  const chain = (el) => { const out = []; for (let i = 0; el && el !== document.body && i < 5; i++, el = el.parentElement) out.push(el); return out; };
  const hoverSnaps = new WeakMap();
  const hoverKeys = new Set();
  const pushHover = (d, entry) => {
    const key = descKey(d) + JSON.stringify(entry);
    if (hoverKeys.has(key) || state.hovers.length >= MAX_HOVER) return;
    hoverKeys.add(key);
    state.hovers.push({ marker: state.marker, target: d, ...entry });
  };
  const onOver = (e) => {
    const els = chain(e.target);
    setTimeout(() => els.forEach(el => { if (el.isConnected) hoverSnaps.set(el, { style: snap(el), desc: describe(el) }); }), 200);
  };
  const onOut = (e) => {
    const els = chain(e.target);
    setTimeout(() => {
      for (const el of els) {
        const h = hoverSnaps.get(el); if (!h) continue;
        if (!el.isConnected) {   // 只在 hover 时出现的元素（如行尾操作按钮），离开后已被移除
          const { cursor, ...style } = h.style;
          pushHover(h.desc, { appearsOnHoverOnly: true, cursor, hoverStyle: style });
          continue;
        }
        if (el.matches(':hover')) continue;
        const rest = snap(el), diff = {};
        for (const p of HOVER_PROPS) if (p !== 'cursor' && h.style[p] !== rest[p]) diff[p] = { rest: rest[p], hover: h.style[p] };
        if (Object.keys(diff).length) pushHover(h.desc, { cursor: h.style.cursor, diff });
      }
    }, 200);
  };

  let timer = null;
  function watch() {
    if (state.watching) return console.log('[ds] 已在记录中');
    state.watching = true;
    timer = setInterval(poll, 16);
    for (const ev of ['transitionrun', 'animationstart']) document.addEventListener(ev, onAnimEvent, true);
    document.addEventListener('pointerover', onOver, true);
    document.addEventListener('pointerout', onOut, true);
    console.log('[ds] 开始记录动画和 hover。每次操作前用 __ds.mark("动作名") 打标记，结束后执行 __ds.stop()');
  }
  function stop() {
    state.watching = false; clearInterval(timer);
    for (const ev of ['transitionrun', 'animationstart']) document.removeEventListener(ev, onAnimEvent, true);
    document.removeEventListener('pointerover', onOver, true);
    document.removeEventListener('pointerout', onOut, true);
    console.log(`[ds] 停止记录：${state.animations.length} 条动画，${state.hovers.length} 条 hover 变化`);
  }
  function mark(label) {
    state.marker = label; state.markerAt = performance.now();
    console.log(`[ds] 标记：${label}`);
  }

  // ---------- 导出 ----------
  function dump(filename = `design-extract-${location.hostname}-${Date.now()}.json`) {
    const data = {
      tool: 'extract-styles.js v1.1',
      meta: {
        host: location.hostname,           // 只记录域名，路径中可能有页面标题
        at: new Date().toISOString(),
        viewport: `${innerWidth}x${innerHeight}`,
        devicePixelRatio,
        colorScheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        notes: '数值全部来自 getComputedStyle。动画只包含 CSS transition / CSS animation / Web Animations API，用 JS 逐帧驱动的动画记录不到。',
      },
      customProperties: readCustomProps(),
      scans: state.scans,
      animations: state.animations,
      hovers: state.hovers,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename });
    document.body.appendChild(a); a.click(); a.remove();
    console.log(`[ds] 已下载 ${filename}（${(blob.size / 1024).toFixed(0)} KB）`);
    return data;
  }

  const status = () => ({ scans: state.scans.map(s => s.label), animations: state.animations.length, hovers: state.hovers.length, watching: state.watching, marker: state.marker });
  const reset = () => { state.scans = []; state.animations = []; state.hovers = []; hoverKeys.clear(); console.log('[ds] 已清空'); };

  window.__ds = { scan, watch, stop, mark, dump, status, reset };
  console.log('[ds] 已加载。命令：__ds.scan("标签") / __ds.watch() / __ds.mark("动作") / __ds.stop() / __ds.dump() / __ds.status()');
})();
