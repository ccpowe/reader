import { isDocumentAnchorChange } from './webTranslationNavigation';
import type { ResolvedWebTranslationRule } from './webTranslationRules';

export type DocumentScope = {
  id: string;
  generation: number;
  kind: 'main' | 'frame' | 'shadow';
  root: Document | ShadowRoot;
  ownerDocument: Document;
  view: Window;
  host: Element | null;
  parent: DocumentScope | null;
  rule: ResolvedWebTranslationRule;
  alive: boolean;
  body: HTMLElement | null;
  contentRoot: Element | null;
  url: string;
  cleanup: (() => void)[];
};

// One registry per top-level runtime. DOM scopes never own a bridge or queue.
export function createDocumentScopes(options: {
  document: Document;
  rule: ResolvedWebTranslationRule;
  selectRule: (url: string, ownerDocument: Document) => ResolvedWebTranslationRule;
  eligible: (host: Element) => boolean;
  added: (scope: DocumentScope) => void;
  removed: (scope: DocumentScope) => void;
  changed: (scope: DocumentScope) => void;
  viewport: () => void;
  report: (reason: string) => void;
}) {
  const scopes = new Set<DocumentScope>();
  const roots = new WeakMap<Node, DocumentScope>();
  const hosts = new Map<Element, DocumentScope>();
  const frames = new Map<HTMLIFrameElement, () => void>();
  const walkers = new Map<DocumentScope, TreeWalker>();
  const reported = new Set<string>();
  const report = (reason: string) => { if (!reported.has(reason)) { reported.add(reason); options.report(reason); } };
  let serial = 0;
  let stopped = false;
  let timer: number | undefined;
  const main: DocumentScope = {
    id: 'main', generation: 1, kind: 'main', root: options.document,
    ownerDocument: options.document, view: options.document.defaultView!, host: null,
    parent: null, rule: options.rule, alive: true, body: options.document.body, contentRoot: options.document.body, url: options.document.URL, cleanup: [],
  };
  scopes.add(main); roots.set(main.root, main);
  const forNode = (node: Node) => roots.get(node.getRootNode()) ?? null;
  const dispose = (scope: DocumentScope) => {
    for (const child of [...scopes]) if (child.parent === scope) dispose(child);
    if (!scope.alive) return;
    scope.alive = false;
    options.removed(scope);
    scope.cleanup.forEach(clean => clean());
    scopes.delete(scope); roots.delete(scope.root); walkers.delete(scope);
    if (scope.host) hosts.delete(scope.host);
  };
  const register = (root: Document | ShadowRoot, host: Element, parent: DocumentScope) => {
    if (roots.has(root)) return;
    if (scopes.size >= 32) { report('scope_limit'); return; }
    let depth = 0;
    for (let ancestor: DocumentScope | null = parent; ancestor; ancestor = ancestor.parent) depth++;
    if (depth >= 8) { report('scope_depth_limit'); return; }
    const doc = root.nodeType === 9 ? root as Document : root.ownerDocument!;
    const scope: DocumentScope = {
      id: `scope-${++serial}`, generation: serial, kind: root.nodeType === 9 ? 'frame' : 'shadow',
      root, ownerDocument: doc, view: doc.defaultView!, host, parent,
      rule: root.nodeType === 9 ? options.selectRule(doc.URL.startsWith('about:') ? parent.url : doc.URL, doc) : parent.rule,
      alive: true, body: doc.body, contentRoot: doc.body, url: doc.URL, cleanup: [],
    };
    if (scope.kind === 'frame') {
      const rootSelectors = scope.rule.scope.mainRoots;
      let matched: Element | null = null;
      for (const selector of rootSelectors) { try { matched = doc.querySelector(selector); } catch { /* Invalid compiled selector is ignored. */ } if (matched) break; }
      scope.contentRoot = scope.rule.scope.rootMode === 'exclusive' && matched ? matched
        : !matched && scope.rule.scope.fallback === 'stop' ? null : doc.body;
    }
    scopes.add(scope); roots.set(root, scope); hosts.set(host, scope);
    if (scope.kind === 'frame') {
      scope.view.addEventListener('scroll', options.viewport, { passive: true, capture: true });
      scope.view.addEventListener('resize', options.viewport);
      scope.cleanup.push(() => {
        scope.view.removeEventListener('scroll', options.viewport, true);
        scope.view.removeEventListener('resize', options.viewport);
      });
    } else {
      const slotChanged = () => { options.changed(scope); options.viewport(); };
      root.addEventListener('slotchange', slotChanged);
      scope.cleanup.push(() => root.removeEventListener('slotchange', slotChanged));
    }
    options.added(scope);
  };
  const inspectFrame = (frame: HTMLIFrameElement, parent: DocumentScope) => {
    if (!options.eligible(frame)) { const previous = hosts.get(frame); if (previous) dispose(previous); return; }
    let doc: Document | null = null;
    try {
      // Capability check, not origin-string equality: srcdoc/about:blank inherit.
      doc = frame.contentDocument;
      if (!doc?.body || !doc.defaultView || (doc.defaultView as Window & { __readerTranslationBridge?: unknown }).__readerTranslationBridge) doc = null;
    } catch { doc = null; }
    if (!doc) report('scope_inaccessible');
    const previous = hosts.get(frame);
    if (previous && doc && isDocumentAnchorChange(previous.url, doc.URL, doc)) previous.url = doc.URL;
    if (previous && (previous.root !== doc || previous.body !== doc?.body || previous.url !== doc?.URL)) dispose(previous);
    if (doc && !hosts.has(frame)) register(doc, frame, parent);
  };
  const inspect = (element: Element, parent: DocumentScope) => {
    if (!options.eligible(element)) return;
    if (element.tagName === 'IFRAME') {
      const frame = element as HTMLIFrameElement;
      if (!frames.has(frame) && frames.size < 64) {
        const load = () => { const owner = forNode(frame); if (owner?.alive) inspectFrame(frame, owner); };
        frame.addEventListener('load', load);
        frames.set(frame, () => frame.removeEventListener('load', load));
      }
      if (!frames.has(frame)) report('frame_listener_limit');
      inspectFrame(frame, parent);
    }
    if (element.shadowRoot && !hosts.has(element)) register(element.shadowRoot, element, parent);
  };
  const scan = () => {
    if (stopped) return;
    for (const [frame, clean] of frames) {
      const parent = forNode(frame);
      if (!frame.isConnected || !parent?.alive) { clean(); frames.delete(frame); }
      else inspectFrame(frame, parent);
    }
    for (const scope of [...scopes]) {
      if (scope.host && (!scope.host.isConnected || !scope.parent?.alive || !options.eligible(scope.host))) dispose(scope);
    }
    // Rotate a single global budget across roots; a document cannot multiply it.
    let budget = 512;
    for (const scope of [...scopes]) {
      if (!scope.alive || budget <= 0) continue;
      let walker = walkers.get(scope);
      if (!walker) { walker = scope.ownerDocument.createTreeWalker(scope.root, 1); walkers.set(scope, walker); }
      let used = 0;
      let node: Node | null = null;
      while (budget > 0 && used++ < Math.max(16, Math.floor(512 / scopes.size)) && (node = walker.nextNode())) {
        budget--; inspect(node as Element, scope);
      }
      if (!node) walkers.delete(scope);
    }
    timer = main.view.setTimeout(scan, 750);
  };
  return {
    main, scopes, forNode,
    start() { scan(); },
    inspect,
    refresh() { walkers.clear(); },
    dispose() {
      stopped = true;
      if (timer !== undefined) main.view.clearTimeout(timer);
      for (const clean of frames.values()) clean();
      frames.clear();
      for (const scope of [...scopes]) if (scope !== main) dispose(scope);
      main.alive = false;
    },
  };
}
