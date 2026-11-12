/** Compensate only synchronous renderer shifts in an ordinary document flow. */
export function createWebTranslationScrollAnchor(view: Window, eligible: (element: Element) => boolean) {
  const doc = view.document;
  type CaretDocument = Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  };
  type Snapshot = {
    range: Range; node: Text; text: string; top: number; scrollX: number; scrollY: number;
    width: number; height: number; url: string;
  };
  let snapshot: Snapshot | undefined;
  let disposed = false;
  let quietUntil = 0;
  const pointers = new Set<number>();
  let touching = false;
  const removers: (() => void)[] = [];
  const reset = () => { snapshot = undefined; };
  function pause() { reset(); quietUntil = view.performance.now() + 500; }
  function listen(type: string, listener: EventListener) {
    view.addEventListener(type, listener, { capture: true, passive: true });
    removers.push(() => view.removeEventListener(type, listener, true));
  }
  listen('pointerdown', (event) => { pointers.add((event as PointerEvent).pointerId); pause(); });
  const releasePointer = (event: Event) => { pointers.delete((event as PointerEvent).pointerId); pause(); };
  listen('pointerup', releasePointer);
  listen('pointercancel', releasePointer);
  listen('touchstart', () => { touching = true; pause(); });
  const releaseTouch = (event: Event) => { touching = (event as TouchEvent).touches.length > 0; pause(); };
  listen('touchend', releaseTouch);
  listen('touchcancel', releaseTouch);
  listen('wheel', pause);
  listen('keydown', pause);
  listen('hashchange', pause);
  listen('popstate', pause);
  listen('blur', () => { pointers.clear(); touching = false; pause(); });
  function inactive() {
    const active = doc.activeElement;
    return disposed || pointers.size > 0 || touching || view.performance.now() < quietUntil
      || !doc.getSelection()?.isCollapsed
      || !!active?.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])');
  }
  function safeSource(node: Node): node is Text {
    if (node.nodeType !== 3 || !node.isConnected || node.getRootNode() !== doc || !node.textContent?.trim()) return false;
    const source = node.parentElement;
    if (!source || !eligible(source)) return false;
    let element: Element | null = source;
    for (let depth = 0; element && depth < 32; depth++, element = element.parentElement) {
      const style = view.getComputedStyle(element);
      if (style.position === 'fixed' || style.position === 'sticky' || style.position === 'absolute'
        || /paint|layout|strict|content/.test(style.contain) || style.perspective !== 'none'
        || (style.zoom && !['1', 'normal'].includes(style.zoom)) || style.transform !== 'none'
        || (style.translate && style.translate !== 'none') || (style.rotate && style.rotate !== 'none')
        || (style.scale && style.scale !== 'none') || style.writingMode !== 'horizontal-tb') return false;
      if (element !== doc.body && element !== doc.documentElement
        && /auto|scroll|hidden|clip/.test(`${style.overflowX} ${style.overflowY}`)) return false;
      if (element === doc.documentElement) return true;
    }
    return false;
  }
  function beforeSlice() {
    reset();
    try {
      if (view !== view.top || inactive() || view.scrollY <= 1 || view.innerHeight < 160) return;
      const caretDoc = doc as CaretDocument;
      if (!caretDoc.caretRangeFromPoint && !caretDoc.caretPositionFromPoint) return;
      // At most nine hit-tests, independent of document size. A one-character
      // range follows the same text even if wrapping changes within its paragraph.
      for (const y of [64, 96, 128]) {
        if (y > view.innerHeight * 0.35) continue;
        for (const fraction of [0.2, 0.5, 0.8]) {
          const x = view.innerWidth * fraction;
          let range = caretDoc.caretRangeFromPoint?.(x, y) ?? null;
          if (!range && caretDoc.caretPositionFromPoint) {
            const caret = caretDoc.caretPositionFromPoint(x, y);
            if (caret) { range = doc.createRange(); range.setStart(caret.offsetNode, caret.offset); range.collapse(true); }
          }
          if (!range || !safeSource(range.startContainer)) continue;
          const node = range.startContainer;
          const offset = Math.min(range.startOffset, node.length - 1);
          range.setStart(node, offset); range.setEnd(node, offset + 1);
          const rect = range.getBoundingClientRect();
          if (!Number.isFinite(rect.top) || rect.height <= 0 || rect.top < 0 || rect.bottom > view.innerHeight
            || Math.abs(rect.top - y) > 40 || x < rect.left - 48 || x > rect.right + 48) continue;
          snapshot = { range, node, text: node.data, top: rect.top, scrollX: view.scrollX, scrollY: view.scrollY,
            width: view.innerWidth, height: view.innerHeight, url: view.location.href };
          return;
        }
      }
    } catch { reset(); /* Unsupported or concurrently detached geometry: leave host scroll alone. */ }
  }
  function afterSlice() {
    const previous = snapshot;
    reset();
    if (!previous) return;
    try {
      if (inactive() || previous.url !== view.location.href || previous.width !== view.innerWidth
        || previous.height !== view.innerHeight || !safeSource(previous.node)
        || previous.node.data !== previous.text || previous.range.startContainer !== previous.node) return;
      // Force layout before checking scroll: native anchoring may already have
      // compensated the mutation. Never add a second correction in that case.
      const rect = previous.range.getBoundingClientRect();
      if (view.scrollY !== previous.scrollY || view.scrollX !== previous.scrollX || rect.height <= 0) return;
      const delta = rect.top - previous.top;
      if (!Number.isFinite(delta) || Math.abs(delta) < 1 || Math.abs(delta) > view.innerHeight) return;
      view.scrollBy({ top: delta, left: 0, behavior: 'instant' });
    } catch { /* Host navigation or detached text must not interfere with commits. */ }
  }
  return { beforeSlice, afterSlice, reset, dispose() {
    disposed = true; reset(); pointers.clear(); touching = false;
    for (const remove of removers) remove();
    removers.length = 0;
  } };
}
