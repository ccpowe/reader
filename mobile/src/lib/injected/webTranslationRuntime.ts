import { splitWebTranslationPayload } from './webTranslationParts';
import { createWebTranslationCommitQueue } from './webTranslationCommitQueue';
import { selectWebTranslationRule } from './webTranslationRules';
import { isDocumentAnchorChange } from './webTranslationNavigation';
import { createWebTranslationLayoutManager } from './webTranslationLayout';
import { webTranslationPresentation } from './webTranslationPresentation';
import { createWebTranslationScrollAnchor } from './webTranslationScrollAnchor';
import { createDocumentScopes, type DocumentScope } from './webTranslationScopes';
import { Readability } from '@mozilla/readability';
import { boundedReaderElements, extractWebReader, readerDependenciesChanged, READER_CONTENT_ATTRIBUTES, sanitizeReaderContent, type WebReaderDependencies } from './webReaderExtraction';
import { MAX_WEB_READER_BYTES, utf8ByteLength } from './webReader';
import type { ResolvedWebTranslationRule } from './webTranslationRules';

type TranslationDisplayMode = 'original' | 'bilingual' | 'translated';

type BootstrapConfig = {
  channelToken: string;
  initialMode: TranslationDisplayMode;
  profile?: string;
  readerContent?: { html: string; url: string };
  rules: readonly ResolvedWebTranslationRule[];
};

type PreservedVariable = {
  token: string;
  closeToken?: string;
  text: string;
  kind: 'break' | 'strong' | 'emphasis' | 'link' | 'code' | 'keyboard' | 'math' | 'image-alt' | 'superscript' | 'subscript' | 'other';
  sourceElement: Element;
};

type NodeIdentity = { key: string; revision: number; fingerprint: string; entityId?: string };

type EntityPart = { id: string; text: string; joiner: string; state: 'discovered' | 'queued' | 'succeeded' | 'failed'; translated?: string; blocked?: boolean };

type ParagraphEntity = {
  id: string;
  scope: DocumentScope;
  commonAncestor: Element;
  container: Element;
  rootNodes: Node[];
  flatNodes: Node[];
  insertionAnchor: Node;
  internalTranslation: boolean;
  renderedInside?: boolean;
  constrainedPlacement: boolean;
  wholeBlock: boolean;
  sourceText: string;
  payloadText: string;
  parts: EntityPart[];
  sourceFingerprint: string;
  sourceTreeFingerprint: string | null;
  anchorParent: Node | null;
  preservedVariables: PreservedVariable[];
  expectedVersion: number;
  state: 'discovered' | 'queued' | 'translated' | 'failed' | 'stale' | 'disposed';
};

type BridgeWindow = Window & {
  ReactNativeWebView?: { postMessage: (message: string) => void };
  __readerTranslationBridge?: {
    retryFailed: () => void;
    applyTranslations: (results: unknown[], documentEpoch?: string) => void;
    cleanup?: () => void;
    confirmEpoch?: (documentEpoch: string, nonce?: string) => void;
    kind: string;
    profile?: string;
    refresh?: () => void;
    diagnoseSelection?: (nonce: string) => void;
    extractReader?: (requestId: string, expectedEpoch: string, revision: number) => void;
    cancelReader?: (requestId: string, revision: number) => void;
    reconnect?: (nonce?: string) => void;
    resetTranslations: () => void;
    setMode: (mode: TranslationDisplayMode) => void;
  };
};

const INLINE_DISPLAYS = new Set([
  'inline', 'inline-block', 'inline-flex', 'inline-grid', 'inline-table',
  'ruby', 'ruby-base', 'ruby-text', 'contents', 'math',
]);
const MAX_SEGMENTS = 5_000;
const MAX_PENDING_COMMITS = 2_048;
const MAX_SEGMENT_CHARS = 7_500;
const OWNED_SELECTORS = '.reader-translation-node,#reader-translation-style';
const X_LEGACY_SELECTOR = '[data-testid="tweetText"]';
const X_POST_SELECTOR = 'article[data-tweet-id],article[data-testid="tweet"]';
const X_CONTENT_SELECTOR = [X_LEGACY_SELECTOR, '.tweet-text', '.js-quoted-tweet-text',
  '[data-testid="UserDescription"]', '[data-testid="HoverCard"]', '[data-testid="birdwatch-pivot"]',
  '[data-testid="card.layoutSmall.detail"]', '[data-testid="card.layoutLarge.detail"]',
  '[data-testid="developerBuiltCardContainer"]', '[data-testid="twitterArticleReadView"]',
  '.font-chirp.text-body.font-normal',
].join(',');
const REDDIT_SELECTOR = 'p,h1,h2,h3,h4,h5,h6,blockquote,figcaption,li,dd,dt,[slot="title"],[slot="text-body"],[slot="comment"],[data-post-click-location="title"],[data-testid="comment"],.RichTextJSON-root,.md-container,.PostContent,.post-content,.Comment__body,faceplate-batch .md,[data-testid="post-title-text"],.i18n-subreddit-description';

const normalizeText = (value: string | null | undefined) =>
  (value ?? '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();

const containsWords = (text: string) => /[A-Za-z\u00c0-\uffff]/.test(text);

const fingerprint = (value: string) => {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return (hash >>> 0).toString(36);
};

function selectRule(url: string, rules: readonly ResolvedWebTranslationRule[], requested?: string, root?: Document) {
  if (requested) {
    const forced = rules.find(rule => rule.id === requested);
    if (forced?.adapter || forced?.id === 'reader') return forced;
  }
  return selectWebTranslationRule(url, rules, root);
}

export function webTranslationBootstrap(config: BootstrapConfig) {
  let selectedNode: Node | null = null;
  const bridgeWindow = window as BridgeWindow;
  const existing = bridgeWindow.__readerTranslationBridge;
  if (existing?.kind === 'web') {
    existing.setMode(config.initialMode);
    existing.reconnect?.();
    return;
  }
  existing?.cleanup?.();

  // Candidate HTML is untrusted even when it arrived through the page bridge.
  // Only this app-owned, initially empty document mounts it, after purification.
  if (config.profile === 'reader' && config.readerContent) {
    const content = document.querySelector('[data-reader-translatable-root]');
    if (content) content.innerHTML = sanitizeReaderContent(config.readerContent.html, config.readerContent.url);
  }

  const html = document.documentElement;
  const navigationId = `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  const entities = new Map<string, ParagraphEntity>();
  const plannedParts = new Map<string, number>();
  let plannedCharacters = 0;
  let budgetExhausted = false;
  const segmentOwners = new Map<string, { entity: ParagraphEntity; part: EntityPart }>();
  const nodeIdentities = new WeakMap<Node, NodeIdentity>();
  const translations = new Map<string, HTMLElement>();
  const sourceWrappers = new Map<string, HTMLElement>();
  const ownedSourceWrappers = new WeakSet<Node>();
  const sourceNodeIds = new WeakMap<Node, number>();
  let sourceNodeSequence = 0;
  let sourceTreeCache = new WeakMap<Element, string | null>();
  const observedParagraphs = new Set<Element>();
  const hiddenRoots = new Set<Element>();
  const pendingRoots = new Set<Element>();
  const pendingDirectRoots = new Set<Element>();
  const dirtyRoots = new Set<Element>();
  const attributeRoots = new Set<Element>();
  let discoveryFacts = new WeakMap<Element, string>();
  const processingRoots = new Set<Element>();
  const deferredRoots = new Map<Element, { firstAt: number; lastAt: number; signature: string }>();
  const pendingContainers = new Set<Element>();
  const renderFailures: Record<string, number> = {};
  let rule = selectRule(location.href, config.rules, config.profile, document);
  let scopeManager: ReturnType<typeof createDocumentScopes> | null = null;
  const scopeFor = (node: Node) => scopeManager?.forNode(node) ?? null;
  const nodeRule = (node: Node) => scopeFor(node)?.rule ?? rule;
  let mode: TranslationDisplayMode = config.initialMode;
  let documentEpoch = '';
  let currentHref = location.href;
  let observedBody = document.body;
  let readerSnapshot: { requestId: string; dependencies: WebReaderDependencies } | null = null;
  let readerSnapshotObserver: MutationObserver | null = null;
  let readerRequestState = { revision: 0, id: '', cancelled: false };
  let activeContentRoot: Element | null = null;
  // Discovery scope and article priority are independent: finding an article
  // must not exclude supporting content from generic in-page translation.
  let articlePriorityRoot: Element | null = null;
  let epochAcknowledged = false;
  let disposed = false;
  let sequence = 0;
  let version = 0;
  let viewportEpoch = 0;
  let traversalRunning = false;
  let writingOwnedDom = 0;
  let ownedMutationSerial = 0;
  const ownedMutationTargets = new WeakMap<Node, number>();
  const ownedAttributeMutations = new WeakMap<Element, { name: string; oldValue: string | null }[]>();
  let readerOwnedAttributeMutations = new WeakMap<Element, { name: string; oldValue: string | null }[]>();
  let drainTimer: number | null = null;
  let viewportTimer: number | null = null;
  let lastPlanSignature = '';
  let forceNextViewportPlan = false;
  let bodyTimer: number | null = null;
  let traversalMs = 0;
  let treeNodes = 0;
  let styleReads = 0;
  let rectReads = 0;
  let paragraphsFiltered = 0;

  const selector = (values: readonly string[]) => values.join(',');

  const safeMatches = (element: Element, value: string) => {
    if (!value) return false;
    try { return element.matches(value); } catch { return false; }
  };

  const clearReaderSnapshot = () => {
    readerSnapshotObserver?.disconnect();
    readerSnapshotObserver = null;
    readerSnapshot = null;
    readerOwnedAttributeMutations = new WeakMap();
  };

  const post = (payload: Record<string, unknown>) => {
    try {
      const message = JSON.stringify({
        ...payload,
        channel_token: config.channelToken,
        document_epoch: documentEpoch,
        document_url: location.href,
        navigation_id: navigationId,
      });
      if (payload.type === 'reader_extraction_result' && utf8ByteLength(message) > MAX_WEB_READER_BYTES) {
        post({ type: 'reader_extraction_result', request_id: payload.request_id, error: 'too_large' });
        clearReaderSnapshot();
        return;
      }
      bridgeWindow.ReactNativeWebView?.postMessage(message);
    } catch {
      // Translation is enhancement-only; a missing native bridge cannot break the page.
    }
  };

  const diagnostics = (truncatedReason?: 'node_limit' | 'segment_limit' | 'disposed') => post({
    type: 'reader_translation_engine_diagnostics',
    profile_id: rule.id,
    scope_id: 'main',
    traversal_ms: Math.round(traversalMs * 100) / 100,
    tree_nodes: treeNodes,
    style_reads: styleReads,
    rect_reads: rectReads,
    paragraphs_detected: entities.size,
    paragraphs_filtered: paragraphsFiltered,
    paragraphs_queued: [...entities.values()].filter((entity) => entity.state === 'queued' || entity.state === 'translated').length,
    pending_roots: pendingRoots.size,
    dirty_roots: dirtyRoots.size,
    deferred_roots: deferredRoots.size,
    processing_roots: processingRoots.size,
    render_failures: { ...renderFailures },
    ...(truncatedReason ? { truncated_reason: truncatedReason } : {}),
  });

  const ownedWrite = <T>(write: () => T): T => {
    writingOwnedDom += 1;
    try { return write(); } finally { writingOwnedDom -= 1; }
  };

  const markOwnedMutation = (...targets: (Node | null | undefined)[]) => {
    const token = ++ownedMutationSerial;
    targets.forEach((target) => { if (target) ownedMutationTargets.set(target, token); });
    window.setTimeout(() => targets.forEach((target) => {
      if (target && ownedMutationTargets.get(target) === token) ownedMutationTargets.delete(target);
    }), 0);
  };

  const markOwnedAttribute = (target: Element, name: string, oldValue = target.getAttribute(name)) => {
    // Observers receive separate records. Give each an independent consumption
    // queue so processing translations cannot erase the reader's owned marker.
    const queues = readerSnapshot ? [ownedAttributeMutations, readerOwnedAttributeMutations] : [ownedAttributeMutations];
    for (const queue of queues) {
      const expected = queue.get(target) ?? [];
      expected.push({ name, oldValue });
      queue.set(target, expected);
      window.setTimeout(() => {
        const pending = queue.get(target);
        if (!pending) return;
        const index = pending.findIndex((item) => item.name === name);
        if (index >= 0) pending.splice(index, 1);
        if (!pending.length) queue.delete(target);
      }, 0);
    }
  };

  const layoutManager = createWebTranslationLayoutManager({
    ownedWrite: (target, write) => ownedWrite(() => {
      const oldValue = target.getAttribute('style');
      write();
      if (oldValue !== target.getAttribute('style')) markOwnedAttribute(target, 'style', oldValue);
    }),
  });

  const setOwnedAttribute = (target: Element, name: string, value: string) => {
    if (target.getAttribute(name) === value) return;
    markOwnedAttribute(target, name);
    target.setAttribute(name, value);
  };

  const addOwnedClass = (target: Element, name: string) => {
    if (target.classList.contains(name)) return;
    markOwnedAttribute(target, 'class');
    target.classList.add(name);
  };

  const removeOwnedClass = (target: Element, name: string) => {
    if (!target.classList.contains(name)) return;
    markOwnedAttribute(target, 'class');
    target.classList.remove(name);
  };

  const isOwnedMutationRecord = (record: MutationRecord, attributeMutations = ownedAttributeMutations, visit?: () => boolean) => {
    if (isOwnedNode(record.target)) return true;
    if (record.type === 'attributes' && record.target.nodeType === Node.ELEMENT_NODE) {
      const expected = attributeMutations.get(record.target as Element);
      let index = -1;
      for (let candidate = 0; expected && candidate < expected.length; candidate++) {
        if (visit && !visit()) return false;
        if (expected[candidate].name === record.attributeName && expected[candidate].oldValue === record.oldValue) {
          index = candidate;
          break;
        }
      }
      if (index < 0 || !expected) return false;
      expected.splice(index, 1);
      if (!expected.length) attributeMutations.delete(record.target as Element);
      return true;
    }
    if (record.type !== 'childList') return false;
    if (!record.addedNodes.length && !record.removedNodes.length) return false;
    for (const nodes of [record.addedNodes, record.removedNodes]) {
      for (const node of nodes) {
        if (visit && !visit()) return false;
        if (!ownedMutationTargets.has(node) && !isOwnedNode(node)) return false;
      }
    }
    return true;
  };

  const withinActiveRoot = (element: Element) => {
    const scope = scopeFor(element);
    if (scope?.kind === 'shadow') return scope.alive;
    if (scope?.kind === 'frame') return scope.alive && Boolean(scope.contentRoot?.contains(element));
    return Boolean(activeContentRoot && (element === activeContentRoot || activeContentRoot.contains(element)));
  };
  const scopeIsCurrent = (scope: DocumentScope) => {
    for (let current: DocumentScope | null = scope; current; current = current.parent) {
      if (!current.alive) return false;
      if (current.kind === 'frame') {
        try {
          if ((current.host as HTMLIFrameElement).contentDocument !== current.root || current.ownerDocument.body !== current.body || (current.ownerDocument.URL !== current.url && !isDocumentAnchorChange(current.url, current.ownerDocument.URL, current.ownerDocument))) return false;
        } catch { return false; }
      }
    }
    return true;
  };

  const isOwnedNode = (node: Node | null) => {
    let element = node?.nodeType === Node.ELEMENT_NODE
      ? node as Element
      : node?.parentElement ?? null;
    for (let depth = 0; element && depth < 20; depth += 1, element = element.parentElement) {
      if (safeMatches(element, OWNED_SELECTORS)) return true;
    }
    return false;
  };

  const watchReaderSnapshot = (requestId: string, dependencies: WebReaderDependencies) => {
    clearReaderSnapshot();
    readerSnapshot = { requestId, dependencies };
    readerSnapshotObserver = new MutationObserver(records => {
      if (!readerSnapshot) return;
      if (!readerDependenciesChanged(readerSnapshot.dependencies, records, isOwnedNode,
        (record, visit) => isOwnedMutationRecord(record, readerOwnedAttributeMutations, visit))) return;
      const id = readerSnapshot.requestId;
      clearReaderSnapshot();
      post({ type: 'reader_snapshot_invalidated', request_id: id });
    });
    // Extraction depends on more attributes than translation discovery. This
    // observer exists only while a reader snapshot is in use.
    readerSnapshotObserver.observe(document, { attributes: true, attributeOldValue: true,
      characterData: true, childList: true, subtree: true });
  };

  let styleCache = new WeakMap<Element, CSSStyleDeclaration>();
  let rectCache = new WeakMap<Element, DOMRect>();
  const styleFor = (element: Element) => {
    const cached = styleCache.get(element);
    if (cached) return cached;
    styleReads += 1;
    const value = (element.ownerDocument.defaultView ?? window).getComputedStyle(element);
    styleCache.set(element, value);
    return value;
  };
  const rectFor = (element: Element) => {
    const cached = rectCache.get(element);
    if (cached) return cached;
    rectReads += 1;
    let value = element.getBoundingClientRect();
    let scope = scopeFor(element);
    while (scope?.parent) {
      if (scope.kind === 'frame') {
        const frame = scope.host!;
        const frameStyle = (frame.ownerDocument.defaultView ?? window).getComputedStyle(frame);
        if (frameStyle.transform !== 'none' || !['1', 'normal'].includes(frameStyle.zoom)) return new DOMRect(0, -1e9, 0, 0);
        const box = frame.getBoundingClientRect();
        const left = Math.max(value.left, 0), top = Math.max(value.top, 0);
        const right = Math.min(value.right, scope.view.innerWidth), bottom = Math.min(value.bottom, scope.view.innerHeight);
        if (right <= left || bottom <= top) return new DOMRect(0, -1e9, 0, 0);
        value = new DOMRect(left + box.left + frame.clientLeft, top + box.top + frame.clientTop, right - left, bottom - top);
        let ancestor: Element | null = frame;
        for (let depth = 0; ancestor && depth < 64; depth++, ancestor = ancestor.parentElement) {
          const css = (ancestor.ownerDocument.defaultView ?? window).getComputedStyle(ancestor);
          if (css.transform !== 'none' || css.scale !== 'none' || css.rotate !== 'none' || css.translate !== 'none' ||
              css.perspective !== 'none' || !['1', 'normal'].includes(css.zoom) || css.clipPath !== 'none' || css.maskImage !== 'none') {
            renderFailures.scope_geometry_unknown = 1;
            return new DOMRect(0, -1e9, 0, 0);
          }
          if (ancestor === frame || ancestor === ancestor.ownerDocument.body || ancestor === ancestor.ownerDocument.documentElement) continue;
          const clipX = /hidden|clip|scroll|auto/.test(css.overflowX), clipY = /hidden|clip|scroll|auto/.test(css.overflowY);
          const clip = ancestor.getBoundingClientRect();
          const l = clipX ? Math.max(value.left, clip.left + ancestor.clientLeft) : value.left;
          const t = clipY ? Math.max(value.top, clip.top + ancestor.clientTop) : value.top;
          const r = clipX ? Math.min(value.right, clip.left + ancestor.clientLeft + ancestor.clientWidth) : value.right;
          const b = clipY ? Math.min(value.bottom, clip.top + ancestor.clientTop + ancestor.clientHeight) : value.bottom;
          if (r <= l || b <= t) return new DOMRect(0, -1e9, 0, 0);
          value = new DOMRect(l, t, r - l, b - t);
        }
        if (ancestor) { renderFailures.scope_geometry_unknown = 1; return new DOMRect(0, -1e9, 0, 0); }
      }
      scope = scope.parent;
    }
    rectCache.set(element, value);
    return value;
  };

  const isExcluded = (element: Element) => {
    const excluded = selector(nodeRule(element).segmentation.exclude);
    return Boolean(excluded && (safeMatches(element, excluded) || element.closest(excluded)));
  };
  const isStayOriginal = (element: Element) => safeMatches(element, selector(nodeRule(element).segmentation.stayOriginal));
  const isAtomic = (element: Element) => safeMatches(element, selector(nodeRule(element).segmentation.atomic));
  const isPreformatted = (element: Element) => safeMatches(element, selector(nodeRule(element).segmentation.preformatted));
  const isBlock = (element: Element) => {
    if (safeMatches(element, selector(nodeRule(element).segmentation.forceInline))) return false;
    if (safeMatches(element, selector(nodeRule(element).segmentation.forceBlock))) return true;
    const display = styleFor(element).display;
    if (display) return !INLINE_DISPLAYS.has(display);
    return /^(ADDRESS|ARTICLE|ASIDE|BLOCKQUOTE|DIV|DL|DT|DD|FIGCAPTION|FIGURE|FOOTER|FORM|H[1-6]|HEADER|HR|LI|MAIN|NAV|OL|P|PRE|SECTION|TABLE|UL)$/.test(element.tagName);
  };

  const composedParent = (element: Element): Element | null => {
    if (element.parentElement) return element.parentElement;
    const scope = scopeFor(element);
    return scope?.host ?? null;
  };
  const sourceExcluded = (element: Element) => {
    const base = selector(nodeRule(element).segmentation.exclude.filter(value => value !== '.reader-source-layout'));
    if (base && element.closest(base)) return true;
    for (let candidate: Element | null = element; candidate; candidate = candidate.parentElement) {
      if (candidate.matches('.reader-source-layout') && !ownedSourceWrappers.has(candidate)) return true;
    }
    return false;
  };
  // These are discovery semantics, not geometry. Width/height/overflow and
  // measurement CSS variables alone must not destroy successful translations.
  const discoveryFact = (element: Element) => {
    styleCache.delete(element);
    const style = styleFor(element);
    const segmentation = nodeRule(element).segmentation;
    return [style.display, style.visibility, style.contentVisibility, style.whiteSpace,
      Number.parseFloat(style.opacity || '1') === 0,
      sourceExcluded(element), segmentation.minChars,
      safeMatches(element, selector(segmentation.preformatted)),
      safeMatches(element, selector(segmentation.stayOriginal)),
      safeMatches(element, selector(segmentation.atomic)),
      safeMatches(element, selector(segmentation.forceBlock)),
      safeMatches(element, selector(segmentation.forceInline)),
    ].join('|');
  };
  const rememberDiscoveryFact = (element: Element) => {
    if (!isOwnedNode(element) && !ownedSourceWrappers.has(element)) discoveryFacts.set(element, discoveryFact(element));
  };
  const captureEntityFacts = (entity: ParagraphEntity) => {
    rememberDiscoveryFact(entity.container);
    let budget = 2048;
    for (const root of entity.rootNodes) {
      if (root.nodeType !== Node.ELEMENT_NODE) continue;
      const element = root as Element;
      rememberDiscoveryFact(element);
      const walker = element.ownerDocument.createTreeWalker(element, NodeFilter.SHOW_ELEMENT);
      for (let node = walker.nextNode(); node && budget-- > 0; node = walker.nextNode()) rememberDiscoveryFact(node as Element);
    }
  };
  const discoveryUnchanged = async (root: Element, epoch: string) => {
    const roots: (Element | ShadowRoot)[] = [root];
    let count = 0, deadline = performance.now() + 8;
    for (let index = 0; index < roots.length; index++) {
      const current = roots[index];
      const check = (element: Element) => {
        if (isOwnedNode(element) || ownedSourceWrappers.has(element)) return true;
        if (++count > 80_000 || discoveryFacts.get(element) !== discoveryFact(element)) return false;
        if (element.shadowRoot && scopeFor(element.shadowRoot)?.alive) roots.push(element.shadowRoot);
        return true;
      };
      if (current.nodeType === Node.ELEMENT_NODE && !check(current as Element)) return false;
      let rejectedBoundaryChanged = false;
      const walker = current.ownerDocument.createTreeWalker(current, NodeFilter.SHOW_ELEMENT, {
        acceptNode: node => {
          if (isOwnedNode(node)) return NodeFilter.FILTER_REJECT;
          const element = node as Element;
          if (!ownedSourceWrappers.has(element) && (sourceExcluded(element) || isPreformatted(element))) {
            if (!check(element)) rejectedBoundaryChanged = true;
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!check(node as Element)) return false;
        if (performance.now() >= deadline) {
          await new Promise<void>(resolve => window.setTimeout(resolve, 0));
          if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return false;
          deadline = performance.now() + 8;
        }
      }
      if (rejectedBoundaryChanged) return false;
    }
    return true;
  };

  const visiblyRendered = (element: Element) => {
    for (let candidate: Element | null = element; candidate; candidate = composedParent(candidate)) {
      const style = styleFor(candidate);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' ||
          style.contentVisibility === 'hidden' || Number.parseFloat(style.opacity || '1') === 0) {
        hiddenRoots.add(element);
        visibilityObserver.observe(element);
        return false;
      }

    }
    const rect = rectFor(element);
    const visible = element.getClientRects().length > 0 && rect.width > 0 && rect.height > 0;
    if (!visible) {
      hiddenRoots.add(element);
      visibilityObserver.observe(element);
    }
    return visible;
  };

  const variableKind = (element: Element): PreservedVariable['kind'] => {
    if (element.matches('a')) return 'link';
    if (element.matches('code')) return 'code';
    if (element.matches('kbd')) return 'keyboard';
    if (element.matches('math')) return 'math';
    if (element.matches('img')) return 'image-alt';
    if (element.matches('sup')) return 'superscript';
    if (element.matches('sub')) return 'subscript';
    return 'other';
  };

  const serializeNodes = (nodes: readonly Node[]) => {
    const variables: PreservedVariable[] = [];
    const visit = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue ?? '';
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      const element = node as Element;
      if (isExcluded(element)) return '';
      if (element.tagName === 'BR') {
        const token = `⟪READER_${variables.length}⟫`;
        variables.push({ token, text: '\n', kind: 'break', sourceElement: element });
        return token;
      }
      if (isStayOriginal(element)) {
        const token = `\u27eaREADER_${variables.length}\u27eb`;
        const text = element.matches('img') ? normalizeText(element.getAttribute('alt')) : normalizeText(element.textContent);
        variables.push({ token, text, kind: element.matches('a') ? 'link' : variableKind(element), sourceElement: element });
        return ` ${token} `;
      }
      if (element.matches('a,strong,b,em,i')) {
        const index = variables.length;
        const token = `\u27eaREADER_OPEN_${index}\u27eb`;
        const closeToken = `\u27eaREADER_CLOSE_${index}\u27eb`;
        variables.push({ token, closeToken, text: normalizeText(element.textContent), kind: element.matches('a') ? 'link' : element.matches('strong,b') ? 'strong' : 'emphasis', sourceElement: element });
        return ` ${token} ${[...element.childNodes].map(visit).join('')} ${closeToken} `;
      }
      return [...element.childNodes].map(visit).join('');
    };
    const sourceText = normalizeText(nodes.map((node) => node.textContent ?? '').join(''));
    const rawPayload = nodes.map(visit).join('');
    const preserveLines = nodes.some(node => { const element = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement; return element && /^(pre-wrap|pre-line|break-spaces)$/.test(styleFor(element).whiteSpace); });
    const payloadText = preserveLines ? rawPayload.replace(/[^\S\n]+/g, ' ').replace(/ *\n */g, '\n').trim() : normalizeText(rawPayload);
    return { sourceText, payloadText, variables };
  };

  const flattenNodes = (nodes: readonly Node[]) => {
    const flat: Node[] = [];
    const visit = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) { flat.push(node); return; }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const element = node as Element;
      if (isExcluded(element)) return;
      if (isStayOriginal(element) || element.tagName === 'BR') { flat.push(element); return; }
      [...element.childNodes].forEach(visit);
    };
    nodes.forEach(visit);
    return flat;
  };

  const validNaturalText = (text: string, container: Element) => {
    const prose = normalizeText(text.replace(/\u27eaREADER_(?:(?:OPEN|CLOSE)_)?\d+\u27eb/g, ''));
    if (prose.length < nodeRule(container).segmentation.minChars || !containsWords(prose)) return false;
    if (/^(?:https?:\/\/|www\.)\S+$/i.test(prose) || /^\S+@\S+\.\S+$/.test(prose)) return false;
    if (/^[\d\p{P}\p{S}\s]+$/u.test(prose)) return false;
    return true;
  };

  const commonAncestor = (nodes: readonly Node[], fallback: Element) => {
    if (!nodes.length) return fallback;
    let candidate: Node | null = nodes[0].nodeType === Node.ELEMENT_NODE ? nodes[0] : nodes[0].parentNode;
    while (candidate && candidate.nodeType === Node.ELEMENT_NODE) {
      if (nodes.every((node) => candidate === node || candidate?.contains(node))) return candidate as Element;
      candidate = candidate.parentNode;
    }
    return fallback;
  };

  const serializableNode = (node: Node) => {
    if (normalizeText(node.textContent) || (node.nodeType === Node.ELEMENT_NODE && (node as Element).tagName === 'BR')) return true;
    return node.nodeType === Node.ELEMENT_NODE && isStayOriginal(node as Element) &&
      ((node as Element).matches('img') || Boolean((node as Element).textContent));
  };

  const projectSiblingRanges = (nodes: Node[], fallback: Element) => {
    const ancestor = commonAncestor(nodes, fallback);
    const projected: Node[] = [];
    for (const node of nodes) {
      if (node === ancestor) return [{ ancestor: ancestor.parentElement ?? fallback, nodes: [node] }];
      let candidate: Node | null = node;
      while (candidate?.parentNode && candidate.parentNode !== ancestor) candidate = candidate.parentNode;
      if (!candidate || candidate.parentNode !== ancestor) continue;
      if (projected.at(-1) !== candidate) projected.push(candidate);
    }
    const inputLeaves = new Set(flattenNodes(nodes));
    if (projected.some(node => flattenNodes([node]).some(leaf => !inputLeaves.has(leaf)))) {
      const exact: { ancestor: Element; nodes: Node[] }[] = [];
      for (const node of nodes) {
        const parent = node.parentElement ?? fallback;
        const previous = exact.at(-1);
        if (previous?.ancestor === parent && previous.nodes.at(-1)?.nextSibling === node) previous.nodes.push(node);
        else exact.push({ ancestor: parent, nodes: [node] });
      }
      return exact;
    }
    const ranges: { ancestor: Element; nodes: Node[] }[] = [];
    let range: Node[] = [];
    let previousIndex = -2;
    for (const node of projected) {
      const index = [...ancestor.childNodes].indexOf(node as ChildNode);
      if (range.length && index !== previousIndex + 1) {
        ranges.push({ ancestor, nodes: range });
        range = [];
      }
      range.push(node);
      previousIndex = index;
    }
    if (range.length) ranges.push({ ancestor, nodes: range });
    return ranges;
  };

  const exposedThroughSlots = (node: Node) => {
    let current: Node | null = node;
    for (let depth = 0; current?.parentElement && depth < 64; depth++) {
      const parent: Element = current.parentElement;
      if (parent.shadowRoot && !(current as Element | Text).assignedSlot) return false;
      current = parent;
    }
    return true;
  };

  const sourceTreeFingerprint = (container: Element) => {
    if (sourceTreeCache.has(container)) return sourceTreeCache.get(container)!;
    let nodes = 0, characters = 0;
    const parts: string[] = [];
    const visit = (node: Node): boolean => {
      if (++nodes > 4096 || characters > 750_000) return false;
      if (isOwnedNode(node)) return true;
      if (ownedSourceWrappers.has(node)) return [...node.childNodes].every(visit);
      if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.TEXT_NODE) return true;
      let identity = sourceNodeIds.get(node);
      if (!identity) { identity = ++sourceNodeSequence; sourceNodeIds.set(node, identity); }
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.nodeValue ?? '';
        characters += text.length;
        parts.push(`T${identity}:${text.length}:${text}`);
        return characters <= 750_000;
      }
      const element = node as Element;
      parts.push(`E${identity}:${element.tagName}`);
      if (element !== container && (sourceExcluded(element) || isPreformatted(element))) { parts.push('X'); return true; }
      if (![...element.childNodes].every(visit)) return false;
      parts.push('/');
      return true;
    };
    const value = visit(container) ? fingerprint(parts.join('|')) : null;
    sourceTreeCache.set(container, value);
    return value;
  };
  const canonicalParent = (node: Node) => {
    const parent = node.parentNode;
    return parent && ownedSourceWrappers.has(parent) ? parent.parentNode : parent;
  };

  const registerProjectedRun = (container: Element, rootNodes: Node[], projectedAncestor: Element) => {
    if (!rootNodes.length || !rootNodes.every(exposedThroughSlots) || isExcluded(container) || !visiblyRendered(container)) {
      paragraphsFiltered += 1;
      return;
    }
    const serialized = serializeNodes(rootNodes);
    if (!validNaturalText(serialized.payloadText, container)) {
      paragraphsFiltered += 1;
      return;
    }
    const payloadParts = splitWebTranslationPayload(serialized.payloadText, MAX_SEGMENT_CHARS);
    if (!payloadParts || segmentOwners.size + payloadParts.length > MAX_SEGMENTS) {
      renderFailures.paragraph_parts_limit = (renderFailures.paragraph_parts_limit ?? 0) + 1; diagnostics('segment_limit');
      budgetExhausted = true; post({ type: 'reader_translation_status', state: 'budget_exhausted', count: entities.size }); return;
    }
    const anchor = rootNodes[0];
    const structure = rootNodes.map((node) => `${node.nodeType}:${node.nodeName}`).join('|');
    const sourceFingerprint = fingerprint(`${serialized.payloadText}|${structure}`);
    let identity = nodeIdentities.get(anchor);
    if (!identity) {
      sequence += 1;
      identity = { key: sequence.toString(36), revision: 1, fingerprint: sourceFingerprint };
      nodeIdentities.set(anchor, identity);
    } else if (identity.fingerprint !== sourceFingerprint) {
      identity.revision += 1;
      identity.fingerprint = sourceFingerprint;
    }
    const scope = scopeFor(rootNodes[0]);
    if (!scope || !scopeIsCurrent(scope)) return;
    const id = `${navigationId}:${documentEpoch}:${scope.id}:${identity.key}:r${identity.revision}:${sourceFingerprint}`;
    const existingEntity = identity.entityId ? entities.get(identity.entityId) : undefined;
    if (existingEntity?.sourceFingerprint === sourceFingerprint) return;
    if (entities.size >= MAX_SEGMENTS && !existingEntity) {
      diagnostics('segment_limit');
      return;
    }
    if (existingEntity) disposeEntity(existingEntity, false);
    const wholeBlock = rootNodes[0].parentNode?.nodeType !== Node.DOCUMENT_FRAGMENT_NODE && serialized.sourceText === normalizeText(container.textContent);
    const internalTranslation = /^(LI|TD|TH|DT|DD)$/.test(container.tagName);
    const entity: ParagraphEntity = {
      id,
      scope,
      commonAncestor: projectedAncestor,
      container,
      rootNodes,
      flatNodes: flattenNodes(rootNodes),
      insertionAnchor: wholeBlock && !internalTranslation ? container : rootNodes[rootNodes.length - 1],
      internalTranslation,
      constrainedPlacement: Number.parseInt(styleFor(container).getPropertyValue('-webkit-line-clamp'), 10) > 0 || styleFor(container).whiteSpace === 'nowrap',
      wholeBlock,
      sourceText: serialized.sourceText,
      payloadText: serialized.payloadText,
      parts: payloadParts.map((part, index) => ({ ...part, id: payloadParts.length === 1 ? id : `${id}:p${index}`, state: 'discovered' })),
      sourceFingerprint,
      sourceTreeFingerprint: sourceTreeFingerprint(container),
      anchorParent: canonicalParent(wholeBlock && !internalTranslation ? container : rootNodes[rootNodes.length - 1]),
      preservedVariables: serialized.variables,
      expectedVersion: version,
      state: 'discovered',
    };
    entities.set(id, entity);
    entity.parts.forEach(part => segmentOwners.set(part.id, { entity, part }));
    captureEntityFacts(entity);
    identity.entityId = id;
    paragraphObserver.observe(container);
    observedParagraphs.add(container);
  };

  const registerRun = (container: Element, nodes: Node[]) => {
    const candidates = nodes.filter(serializableNode);
    if (!candidates.length) {
      paragraphsFiltered += 1;
      return;
    }
    if (candidates[0].parentNode?.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
      registerProjectedRun(container, candidates, container);
      return;
    }
    projectSiblingRanges(candidates, container)
      .forEach((range) => registerProjectedRun(container, range.nodes, range.ancestor));
  };

  const socialCandidates = (root: Element) => {
    const contentSelector = nodeRule(root).adapter === 'reddit' ? REDDIT_SELECTOR : X_CONTENT_SELECTOR;
    const candidates = new Set<Element>();
    // Mutations can start below the selected content element. Traverse only
    // the changed subtree, retaining its approved content scope.
    if (root.closest(contentSelector)) candidates.add(root);
    root.querySelectorAll(contentSelector).forEach(element => candidates.add(element));
    if (nodeRule(root).adapter === 'x') {
      const postings = [...root.querySelectorAll(X_POST_SELECTOR)];
      const posting = root.closest(X_POST_SELECTOR);
      if (posting) postings.unshift(root);
      for (const post of postings) {
        if (post.querySelector(X_CONTENT_SELECTOR) || post.closest(X_CONTENT_SELECTOR)) continue;
        const legacyBodies = [...post.querySelectorAll('div[dir="auto"].text-body')].filter(element =>
          !element.closest('a,button,time,[role="button"],[role="link"],[role="toolbar"],[data-testid="User-Name"]'));
        if (legacyBodies.length) legacyBodies.forEach(element => candidates.add(element));
        else candidates.add(post);
      }
    }
    return [...candidates].filter(element => !isExcluded(element) && !isPreformatted(element));
  };

  const discoverSocial = async (root: Element, epoch: string, preservedSources?: ReadonlySet<Node>) => {
    const candidates = socialCandidates(root);
    for (const element of candidates.filter(element => !candidates.some(parent => parent !== element && parent.contains(element)))) {
      if (disposed || documentEpoch !== epoch || !scopeFor(element)?.alive) return;
      // Selectors delimit content, never paragraph boundaries. Use the same
      // visual TreeWalker for nested paragraphs, lists and rich inline runs.
      await discoverGeneric(element, epoch, false, preservedSources);
    }
  };

  type ParagraphTextSplit = { nodes: Text[]; values: string[] };
  const paragraphTextSplits = new Set<ParagraphTextSplit>();
  const splitTextOwners = new WeakMap<Node, ParagraphTextSplit>();
  let splitTextCount = 0;
  const restoreTextSplit = (group: ParagraphTextSplit, hostReplaced = false) => {
    const first = group.nodes[0];
    for (let index = 1; index < group.nodes.length; index++) {
      const next = group.nodes[index];
      if (first.parentNode && first.nextSibling === next && (!hostReplaced || next.data === group.values[index])) {
        markOwnedMutation(first.parentNode, first, next);
        if (!hostReplaced) first.appendData(next.data);
        next.remove();
      }
    }
    splitTextOwners.delete(first);
    paragraphTextSplits.delete(group);
    splitTextCount -= group.nodes.length - 1;
  };
  const splitWhitespaceParagraphs = (root: Element) => {
    if (nodeRule(root).adapter !== 'x') return;
    const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const texts: Text[] = [];
    let node: Node | null;
    let visited = 0;
    while ((node = walker.nextNode()) && visited++ < 10_000) {
      const parent = node.parentElement;
      if (!parent || isOwnedNode(node) || isExcluded(parent) || parent.closest('a,code,pre,kbd') ||
          !/^(pre-wrap|pre-line|break-spaces)$/.test(styleFor(parent).whiteSpace)) continue;
      if (/\n[\t \r]*\n/.test(node.nodeValue ?? '')) texts.push(node as Text);
    }
    ownedWrite(() => {
      for (const text of texts) {
        let current = text;
        const nodes = [text];
        let match: RegExpExecArray | null;
        while ((match = /\n[\t \r]*\n(?:[\t \r]*\n)*/.exec(current.data)) && splitTextCount < MAX_SEGMENTS) {
          const end = match.index + match[0].length;
          if (end === current.length) break;
          markOwnedMutation(current.parentNode, current);
          const right = current.splitText(end);
          markOwnedMutation(right);
          nodes.push(right);
          splitTextCount++;
          current = right;
        }
        if (nodes.length > 1) {
          const group = { nodes, values: nodes.map(node => node.data) };
          paragraphTextSplits.add(group);
          splitTextOwners.set(text, group);
        }
      }
    });
    if (texts.length) sourceTreeCache = new WeakMap();
  };
  const restoreParagraphTextSplits = () => {
    for (const group of paragraphTextSplits) restoreTextSplit(group, group.nodes[0].data !== group.values[0]);
  };

  const discoverGeneric = async (root: Element, epoch: string, directContentOnly = false, preservedSources?: ReadonlySet<Node>) => {
    splitWhitespaceParagraphs(root);
    rememberDiscoveryFact(root);
    const discoveryScope = scopeFor(root);
    const started = performance.now();
    let deadline = started + 8;
    let run: Node[] = [];
    let runContainer: Element | null = null;
    let skipDescendantsOf: Element | null = null;
    const flush = () => {
      if (run.length && runContainer) registerRun(runContainer, run);
      run = [];
      runContainer = null;
    };
    const visualContainerFor = (node: Node) => {
      let candidate = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
      while (candidate && candidate !== root && !isBlock(candidate)) candidate = candidate.parentElement;
      return candidate ?? root;
    };
    const addUnit = (node: Node) => {
      const container = visualContainerFor(node.nodeType === Node.ELEMENT_NODE && (node as Element).tagName === 'BR' ? node.parentElement! : node);
      if (runContainer && runContainer !== container) flush();
      runContainer = container;
      run.push(node);
    };
    const walker = root.ownerDocument.createTreeWalker(
      root,
      NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          if (preservedSources?.has(node)) { flush(); return NodeFilter.FILTER_REJECT; }
          if (node.nodeType !== Node.ELEMENT_NODE) return NodeFilter.FILTER_ACCEPT;
          const element = node as Element;
          rememberDiscoveryFact(element);
          if (discoveryScope) scopeManager?.inspect(element, discoveryScope);
          // Long containers schedule their block children separately. Still
          // traverse the inline runs between them, without entering those
          // potentially distant block subtrees or merging across a boundary.
          if (directContentOnly && element.parentElement === root && isBlock(element)) {
            flush();
            return NodeFilter.FILTER_REJECT;
          }
          return (element.tagName === 'SLOT' && (element as HTMLSlotElement).assignedNodes().length > 0) || isExcluded(element) || isPreformatted(element)
            ? NodeFilter.FILTER_REJECT
            : NodeFilter.FILTER_ACCEPT;
        },
      },
    );
    for (let node = walker.nextNode(); node && entities.size < MAX_SEGMENTS; node = walker.nextNode()) {
      treeNodes += 1;
      if (performance.now() >= deadline) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
        deadline = performance.now() + 8;
        if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return;
      }
      if (skipDescendantsOf) {
        if (skipDescendantsOf.contains(node)) continue;
        skipDescendantsOf = null;
      }
      if (node.nodeType === Node.TEXT_NODE) {
        if (normalizeText(node.nodeValue)) addUnit(node);
        if (nodeRule(root).adapter === 'x' && /\n[\t \r]*\n[\t \r\n]*$/.test(node.nodeValue ?? '') && /^(pre-wrap|pre-line|break-spaces)$/.test(styleFor(node.parentElement ?? root).whiteSpace)) flush();
        continue;
      }
      const element = node as Element;
      if (element.tagName === 'BR') { addUnit(element); continue; }
      if (isStayOriginal(element) || element.matches('a') || (element.matches('strong,b,em,i') && !isBlock(element) && [...element.querySelectorAll('*')].every(child => !isBlock(child)))) {
        addUnit(element);
        skipDescendantsOf = element;
        continue;
      }
      if (isAtomic(element)) {
        flush();
        registerRun(element, [...element.childNodes]);
        skipDescendantsOf = element;
        continue;
      }
      if (isBlock(element) && runContainer && element !== runContainer) flush();
    }
    flush();
    traversalMs += performance.now() - started;
  };

  const paragraphRect = (entity: ParagraphEntity) => rectFor(entity.container);

  const requestViewportPlan = (force = false) => {
    if (mode === 'original' || !epochAcknowledged || disposed) return;
    styleCache = new WeakMap();
    rectCache = new WeakMap();
    const viewport = Math.max(window.innerHeight, 1);
    const ordered = [...entities.values()].filter((entity) =>
      entity.parts.some(part => part.state === 'discovered' || part.state === 'queued') && visiblyRendered(entity.container))
      .sort((left, right) => paragraphRect(left).top - paragraphRect(right).top);
    const previous = ordered.filter((entity) => { const rect = paragraphRect(entity); return rect.bottom < 0 && rect.bottom >= -viewport; });
    const current = ordered.filter((entity) => { const rect = paragraphRect(entity); return rect.bottom >= 0 && rect.top <= viewport; });
    const next = ordered.filter((entity) => { const rect = paragraphRect(entity); return rect.top > viewport && rect.top <= viewport * 2; });
    const prioritizeArticle = (values: ParagraphEntity[]) => values.sort((left, right) =>
      Number(Boolean(articlePriorityRoot?.contains(right.container))) -
      Number(Boolean(articlePriorityRoot?.contains(left.container))));
    viewportEpoch += 1;
    const windowId = `${documentEpoch}:viewport-${viewportEpoch}`;
    const batches: { id: string; priority: 'urgent' | 'prefetch'; segments: { segment_id: string; text: string }[]; window_id: string }[] = [];
    let messageCharacters = 0;
    const add = (values: ParagraphEntity[], slots: number, size: number, priority: 'urgent' | 'prefetch') => {
      const parts = values.flatMap(entity => entity.parts.filter(part => part.state === 'discovered' || part.state === 'queued').map(part => ({ entity, part }))).slice(0, slots);
      const budget = priority === 'urgent' ? 3_000 : 4_000;
      for (let index = 0; index < parts.length;) {
        let characters = 0;
        const segments: { segment_id: string; text: string }[] = [];
        while (index < parts.length && segments.length < size) {
          const { entity, part } = parts[index];
          const encodedSize = JSON.stringify({ segment_id: part.id, text: part.text }).length + 300;
          if (messageCharacters + encodedSize > 80_000) { index++; continue; }
          if (!plannedParts.has(part.id)) {
            if (plannedParts.size >= 5_000 || plannedCharacters + part.text.trim().length > 700_000) {
              part.state = 'failed'; part.blocked = true; entity.state = 'failed';
              renderFailures.document_budget = (renderFailures.document_budget ?? 0) + 1;
              diagnostics('segment_limit');
              budgetExhausted = true;
              post({ type: 'reader_translation_status', state: 'budget_exhausted', count: entities.size });
              index++; continue;
            }
          }
          if (segments.length && characters + part.text.length > budget) break;
          characters += part.text.length; messageCharacters += encodedSize;
          if (!plannedParts.has(part.id)) { plannedParts.set(part.id, part.text.trim().length); plannedCharacters += part.text.trim().length; }
          entity.state = 'queued'; part.state = 'queued';
          segments.push({ segment_id: part.id, text: part.text });
          index += 1;
        }
        if (segments.length) batches.push({ id: `web:${windowId}:${priority}:${segments[0].segment_id}`, priority, segments, window_id: windowId });
      }
    };
    add(prioritizeArticle(current), 10, 3, 'urgent');
    add(prioritizeArticle(previous.reverse()), 5, 5, 'prefetch');
    add(prioritizeArticle(next), 5, 5, 'prefetch');
    const signature = batches.map((batch) => `${batch.priority}:${batch.segments.map((segment) => segment.segment_id).join(',')}`).join('|');
    if (batches.length && (force || signature !== lastPlanSignature)) {
      lastPlanSignature = signature;
      post({ type: 'reader_translation_batch_plan', batches });
    }
  };

  const scheduleViewportPlan = (delay = 120, force = false) => {
    forceNextViewportPlan ||= force;
    if (viewportTimer !== null || mode === 'original' || !epochAcknowledged) return;
    viewportTimer = window.setTimeout(() => {
      viewportTimer = null;
      const forced = forceNextViewportPlan;
      forceNextViewportPlan = false;
      requestViewportPlan(forced);
    }, delay);
  };

  const paragraphObserver = new IntersectionObserver((entries) => {
    if (!traversalRunning && entries.some((entry) => entry.isIntersecting &&
      [...entities.values()].some((entity) => entity.container === entry.target && entity.state === 'discovered'))) {
      scheduleViewportPlan(20);
    }
  }, { rootMargin: '100% 0px 150% 0px', threshold: 0.01 });

  const containerObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const container = entry.target as Element;
      containerObserver.unobserve(container);
      pendingContainers.delete(container);
      queueRoot(container, false);
    });
  }, { rootMargin: '0px 0px 200% 0px', threshold: 0.01 });

  const visibilityObserver = new ResizeObserver((entries) => {
    styleCache = new WeakMap();
    rectCache = new WeakMap();
    entries.forEach((entry) => {
      const element = entry.target as Element;
      const style = styleFor(element);
      const rect = rectFor(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.contentVisibility === 'hidden' ||
          Number.parseFloat(style.opacity || '1') === 0 || rect.width <= 0 || rect.height <= 0) return;
      visibilityObserver.unobserve(element);
      hiddenRoots.delete(element);
      for (const scope of scopeManager?.scopes ?? []) {
        if (scope.host === element) { discoverShadowDirect(scope); scopeRoots(scope).forEach(root => queueRoot(root, false)); }
      }
      const root = nearestBlockRoot(element);
      if (root) queueRoot(root, false);
    });
  });

  const validatePlaceholderResult = (entity: ParagraphEntity, translated: string, source = entity.payloadText) => {
    const tokenPattern = /\u27eaREADER_(?:(?:OPEN|CLOSE)_)?\d+\u27eb/g;
    const found = translated.match(tokenPattern) ?? [];
    const expected = source.match(tokenPattern) ?? [];
    if (found.length !== expected.length || !expected.every((token) => found.filter((candidate) => candidate === token).length === 1)) return false;
    const stack: string[] = [];
    for (const token of found) {
      const variable = entity.preservedVariables.find((candidate) => candidate.token === token || candidate.closeToken === token);
      if (!variable) return false;
      if (variable.token === token && variable.closeToken) stack.push(variable.closeToken);
      else if (variable.closeToken === token && stack.pop() !== token) return false;
    }
    return stack.length === 0;
  };

  const safeHrefAttribute = (element: Element) => {
    const raw = element.getAttribute('href')?.trim();
    if (!raw) return null;
    if (raw.startsWith('#')) return raw;
    try {
      const parsed = new URL(raw, element.ownerDocument.baseURI);
      if (['http:', 'https:', 'mailto:', 'tel:'].includes(parsed.protocol)) return raw;
    } catch { /* Unsafe or malformed attributes are omitted. */ }
    return null;
  };

  const linkAppearance = (source: Element) => {
    const css = (source.ownerDocument.defaultView ?? window).getComputedStyle(source);
    return ['color', 'text-decoration-line', 'text-decoration-color', 'text-decoration-style']
      .map(property => [property, css.getPropertyValue(property)] as const);
  };
  const cloneControlledStructure = (variable: PreservedVariable) => {
    const source = variable.sourceElement;
    if (variable.kind === 'link') {
      const link = source.ownerDocument.createElement('a');
      const href = safeHrefAttribute(source);
      if (href) link.setAttribute('href', href);
      const title = source.getAttribute('title');
      if (title) link.setAttribute('title', title.slice(0, 500));
      const target = source.getAttribute('target');
      if (target === '_blank' || target === '_self') link.setAttribute('target', target);
      if (target === '_blank') link.setAttribute('rel', 'noopener noreferrer');
      for (const [property, value] of linkAppearance(source)) link.style.setProperty(property, value, 'important');
      if (!variable.closeToken) link.textContent = variable.text;
      return link;
    }
    if (variable.kind === 'break') return source.ownerDocument.createElement('br');
    if (variable.kind === 'strong' || variable.kind === 'emphasis') return source.ownerDocument.createElement(variable.kind === 'strong' ? 'strong' : 'em');
    const tag = variable.kind === 'keyboard' ? 'kbd'
      : variable.kind === 'math' ? 'math'
        : variable.kind === 'image-alt' ? 'span'
          : variable.kind === 'superscript' ? 'sup'
            : variable.kind === 'subscript' ? 'sub'
              : variable.kind === 'code' ? 'code' : 'span';
    const clone = source.ownerDocument.createElement(tag);
    clone.classList.add('reader-preserved-variable');
    if (variable.kind === 'image-alt') {
      clone.setAttribute('data-reader-image-alt', '');
      clone.textContent = variable.text.slice(0, 1_000);
    } else clone.textContent = variable.text;
    return clone;
  };

  const renderTranslatedText = (entity: ParagraphEntity, translated: string) => {
    const fragment = entity.container.ownerDocument.createDocumentFragment();
    const variables = new Map<string, { variable: PreservedVariable; close: boolean }>();
    entity.preservedVariables.forEach((variable) => {
      variables.set(variable.token, { variable, close: false });
      if (variable.closeToken) variables.set(variable.closeToken, { variable, close: true });
    });
    const tokenPattern = /(\u27eaREADER_(?:(?:OPEN|CLOSE)_)?\d+\u27eb)/g;
    const parents: (DocumentFragment | Element)[] = [fragment];
    translated.split(tokenPattern).filter(Boolean).forEach((part) => {
      const descriptor = variables.get(part);
      if (!descriptor) { parents[parents.length - 1].appendChild(entity.container.ownerDocument.createTextNode(part)); return; }
      if (descriptor.close) { parents.pop(); return; }
      const clone = cloneControlledStructure(descriptor.variable);
      parents[parents.length - 1].appendChild(clone);
      if (descriptor.variable.closeToken) parents.push(clone);
    });
    return fragment;
  };

  const wrapSource = (entity: ParagraphEntity) => {
    if (sourceWrappers.has(entity.id)) return;
    const wrapper = entity.container.ownerDocument.createElement('span');
    wrapper.className = 'reader-source-layout';
    ownedSourceWrappers.add(wrapper);
    wrapper.setAttribute('data-reader-source-layout-for', entity.id);
    if (entity.wholeBlock && !entity.renderedInside) {
      addOwnedClass(entity.container, 'reader-source-hidden');
      sourceWrappers.set(entity.id, wrapper);
      return;
    }
    const parent = entity.rootNodes[0]?.parentNode;
    if (!parent || !entity.rootNodes.every((node) => node.parentNode === parent)) return;
    markOwnedMutation(parent, wrapper, ...entity.rootNodes);
    parent.insertBefore(wrapper, entity.rootNodes[0]);
    entity.rootNodes.forEach((node) => wrapper.appendChild(node));
    sourceWrappers.set(entity.id, wrapper);
  };

  const unwrapSource = (entity: ParagraphEntity) => {
    removeOwnedClass(entity.container, 'reader-source-hidden');
    const wrapper = sourceWrappers.get(entity.id);
    if (wrapper?.isConnected && wrapper.childNodes.length) {
      const parent = wrapper.parentNode;
      markOwnedMutation(parent, wrapper, ...wrapper.childNodes);
      while (wrapper.firstChild) parent?.insertBefore(wrapper.firstChild, wrapper);
      wrapper.remove();
    }
    sourceWrappers.delete(entity.id);
  };

  const syncModeForEntity = (entity: ParagraphEntity) => {
    const translation = translations.get(entity.id);
    if (!translation) return;
    translation.hidden = mode === 'original';
    for (const [property, value] of presentationFor(entity).styles) translation.style.setProperty(property, value, 'important');
    translation.style.setProperty('display', mode === 'original' ? 'none' : presentationFor(entity).inline ? 'inline' : 'block', 'important');
    if (mode === 'translated') wrapSource(entity); else unwrapSource(entity);
    captureEntityFacts(entity);
    if (mode === 'bilingual') {
      layoutManager.acquire(entity.id, entity.commonAncestor as HTMLElement, translation, {
        selectors: entity.scope.rule.rendering.unclamp,
        naturalFlow: typeof (entity.commonAncestor as Element & { computedStyleMap?: unknown }).computedStyleMap === 'function',
      });
    } else layoutManager.release(entity.id);
  };

  const removeTranslation = (entity: ParagraphEntity) => ownedWrite(() => {
    layoutManager.release(entity.id);
    const translation = translations.get(entity.id);
    markOwnedMutation(translation?.parentNode, translation);
    translation?.remove();
    translations.delete(entity.id);
    unwrapSource(entity);
  });

  function disposeEntity(entity: ParagraphEntity, forgetIdentity: boolean) {
    deferredAttributes.delete(entity.id);
    commitQueue.cancel(`attributes:${entity.id}`);
    removeTranslation(entity);
    entities.delete(entity.id);
    entity.state = 'disposed';
    entity.parts.forEach(part => {
      const timer = expiryTimers.get(part.id);
      if (timer !== undefined) window.clearTimeout(timer);
      expiryTimers.delete(part.id);
      expiryActions.delete(part.id);
      commitQueue.cancel(part.id); segmentOwners.delete(part.id);
      post({ type: 'reader_translation_segment_event', segment_id: part.id, reason: 'source_removed', state: 'disposed' });
    });
    if (![...entities.values()].some((candidate) => candidate.container === entity.container)) {
      paragraphObserver.unobserve(entity.container);
      observedParagraphs.delete(entity.container);
    }
    if (forgetIdentity) entity.rootNodes.forEach((node) => nodeIdentities.delete(node));
  }

  const sweepDisconnectedEntities = () => {
    for (const entity of [...entities.values()]) {
      if (!entity.container.isConnected || !entity.rootNodes.every((node) => node.isConnected)) {
        disposeEntity(entity, true);
      }
    }
  };

  const failPart = (entity: ParagraphEntity, part: EntityPart, reason: string) => {
    part.state = 'failed'; entity.state = 'failed';
    post({ type: 'reader_translation_segment_event', segment_id: part.id, reason, state: 'failed' });
  };
  const deferredAttributes = new Set<string>();
  let attributeRetryTimer: number | null = null;
  const scheduleDeferredAttributes = () => {
    if (attributeRetryTimer !== null || !deferredAttributes.size || disposed) return;
    attributeRetryTimer = window.setTimeout(() => {
      attributeRetryTimer = null;
      for (const id of [...deferredAttributes].slice(0, Math.max(0, Math.min(32, MAX_PENDING_COMMITS - commitQueue.size)))) {
        deferredAttributes.delete(id);
        const entity = entities.get(id);
        if (entity?.state === 'translated') queueAttributeRender(entity);
      }
      if (deferredAttributes.size && commitQueue.size < MAX_PENDING_COMMITS) scheduleDeferredAttributes();
    }, 0);
  };
  const scrollAnchor = createWebTranslationScrollAnchor(window, element => {
    if (mode !== 'bilingual' || !epochAcknowledged) return false;
    let parent: Element | null = element;
    for (let depth = 0; parent && depth < 32; depth++, parent = parent.parentElement) {
      if (observedParagraphs.has(parent)) return true;
      const id = parent.getAttribute('data-reader-translation-for');
      if (id && entities.has(id)) return true;
    }
    return false;
  });
  const commitPriority = (entity: ParagraphEntity): 'visible' | 'background' => {
    if (mode === 'original' || !entity.container.isConnected) return 'background';
    const rect = rectFor(entity.container);
    return rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth ? 'visible' : 'background';
  };
  const commitQueue = createWebTranslationCommitQueue({
    maxPending: MAX_PENDING_COMMITS,
    beforeSlice() { rectCache = new WeakMap(); scrollAnchor.beforeSlice(); },
    afterSlice() { scrollAnchor.afterSlice(); rectCache = new WeakMap(); },
    onSettled(task, outcome) {
      if (task.key.startsWith('attributes:')) {
        const id = task.key.slice('attributes:'.length);
        const entity = entities.get(id);
        if (entity && outcome === 'rejected') deferredAttributes.add(id);
        if (entity && outcome === 'error') {
          removeTranslation(entity);
          entity.parts.forEach(part => failPart(entity, part, 'render_commit_failed'));
        }
      }
      scheduleDeferredAttributes();
      const owner = segmentOwners.get(task.key);
      if (owner && (outcome === 'error' || outcome === 'rejected')) failPart(owner.entity, owner.part, 'render_commit_failed');
      if (!commitQueue.size && (outcome === 'committed' || outcome === 'error' || outcome === 'rejected')) scheduleViewportPlan(0);
    },
  });
  const presentationFor = (entity: ParagraphEntity) => webTranslationPresentation(entity.container, {
    wholeBlock: entity.wholeBlock, internalTranslation: entity.internalTranslation, constrainedPlacement: entity.constrainedPlacement,
    typographySource: entity.commonAncestor,
    text: entity.sourceText, translatedOnly: mode === 'translated',
  });
  const translationStyleChanged = (entity: ParagraphEntity) => {
    const translation = translations.get(entity.id);
    if (!translation) return false;
    const presentation = presentationFor(entity);
    return renderedContents.get(translation)?.key !== contentKeyFor(entity) || layoutManager.hasLease(entity.id) || entity.renderedInside !== presentation.inside || presentation.styles.some(([property, value]) =>
      property !== 'display' && translation.style.getPropertyValue(property) !== value);
  };

  const renderedContents = new WeakMap<Element, { key: string; html: string }>();
  const contentKeyFor = (entity: ParagraphEntity) => fingerprint(JSON.stringify([entity.parts.map(part => [part.joiner, part.translated]),
    entity.preservedVariables.map(variable => variable.kind === 'link'
      ? [['href', 'target', 'title', 'rel'].map(name => variable.sourceElement.getAttribute(name)), (variable.sourceElement as HTMLAnchorElement).href, linkAppearance(variable.sourceElement)]
      : variable.text)]));
  const renderEntity = (entity: ParagraphEntity, notify = true) => {
    ownedWrite(() => {
      // Source wrappers belong only to translation-only mode. Restore before
      // moving a translation between inside/sibling positions after a resize.
      if (sourceWrappers.has(entity.id)) unwrapSource(entity);
      const presentation = presentationFor(entity);
      let translation = translations.get(entity.id);
      if (!translation) {
        translation = entity.container.ownerDocument.createElement('span');
        translation.className = 'reader-translation-node';
        translation.setAttribute('data-reader-translation-for', entity.id);
        translations.set(entity.id, translation);
      }
      const anchor = entity.insertionAnchor;
      const parent = presentation.inside ? entity.container : anchor.parentNode;
      // An internal range ends at its own last root, not after every other
      // paragraph in a list item or table cell.
      let previous: Node = presentation.inside ? entity.rootNodes[entity.rootNodes.length - 1] : anchor;
      if (presentation.inside) while (previous.parentNode && previous.parentNode !== parent) previous = previous.parentNode;
      if (parent && (translation.parentNode !== parent || translation.previousSibling !== previous)) {
        markOwnedMutation(translation.parentNode, parent, translation);
        parent.insertBefore(translation, previous.nextSibling);
      }
      entity.renderedInside = presentation.inside;
      if (!presentation.inside && entity.container.hasAttribute('slot')) translation.setAttribute('slot', entity.container.getAttribute('slot')!);
      else translation.removeAttribute('slot');
      for (const [property, value] of presentation.styles) {
        if (translation.style.getPropertyValue(property) !== value) translation.style.setProperty(property, value, 'important');
      }
      markOwnedMutation(translation);
      const contentKey = contentKeyFor(entity);
      const rendered = renderedContents.get(translation);
      if (rendered?.key !== contentKey || rendered.html !== translation.innerHTML) {
        translation.replaceChildren(...entity.parts.flatMap(part => [entity.container.ownerDocument.createTextNode(part.joiner), renderTranslatedText(entity, part.translated!)]));
        renderedContents.set(translation, { key: contentKey, html: translation.innerHTML });
      }
      entity.state = 'translated';
      if (notify) entity.parts.forEach(part => post({ type: 'reader_translation_segment_event', segment_id: part.id, reason: 'rendered', state: 'translated' }));
      syncModeForEntity(entity);
    });
  };
  const entitySourceCurrent = (entity: ParagraphEntity) => {
    if (entities.get(entity.id) !== entity || !scopeIsCurrent(entity.scope) || entity.state === 'disposed' ||
        !entity.rootNodes.every(node => node.isConnected) || !entity.flatNodes.every(node => node.isConnected)) return false;
    if (!sourceVisibilityCurrent(entity) || !withinActiveRoot(entity.container) || canonicalParent(entity.insertionAnchor) !== entity.anchorParent) return false;
    if (entity.sourceTreeFingerprint !== null && sourceTreeFingerprint(entity.container) !== entity.sourceTreeFingerprint) return false;
    const semanticElements = new Set([entity.container, ...entity.flatNodes.map(node => node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement).filter((element): element is Element => Boolean(element))]);
    for (const element of semanticElements) {
      if (ownedSourceWrappers.has(element)) continue;
      const previous = discoveryFacts.get(element);
      if (previous !== undefined && previous !== discoveryFact(element)) return false;
    }
    const current = serializeNodes(entity.rootNodes);
    return current.payloadText === entity.payloadText && current.variables.length === entity.preservedVariables.length &&
      current.variables.every((variable, index) => variable.sourceElement === entity.preservedVariables[index].sourceElement);
  };
  const queueAttributeRender = (entity: ParagraphEntity) => {
    commitQueue.enqueue({ key: `attributes:${entity.id}`, scopeId: entity.scope.id, priority: () => commitPriority(entity),
      isValid: () => entitySourceCurrent(entity) && entity.state === 'translated', commit: () => renderEntity(entity, false) });
  };
  const expiryTimers = new Map<string, number>();
  const expiryActions = new Map<string, { at: number; expire: () => void }>();
  const expireDueTranslations = () => {
    for (const { at, expire } of expiryActions.values()) if (at <= Date.now()) expire();
  };
  const clearExpiryTimers = () => {
    expiryTimers.forEach(timer => window.clearTimeout(timer));
    expiryTimers.clear();
    expiryActions.clear();
  };
  const applyTranslations = (rawResults: unknown[], resultEpoch = documentEpoch) => {
    if (!Array.isArray(rawResults) || resultEpoch !== documentEpoch || !epochAcknowledged) return;
    for (const raw of rawResults) {
      if (!raw || typeof raw !== 'object') continue;
      const result = raw as { cache_expires_at?: unknown; error_code?: unknown; segment_id?: unknown; source_text?: unknown; translated_text?: unknown; translation_status?: unknown };
      if (typeof result.segment_id !== 'string' || typeof result.source_text !== 'string') continue;
      const owner = segmentOwners.get(result.segment_id);
      if (!owner || normalizeText(result.source_text) !== normalizeText(owner.part.text)) continue;
      const { entity, part } = owner;
      const expiresAt = typeof result.cache_expires_at === 'string' ? Date.parse(result.cache_expires_at) : Date.now() + 60 * 60_000;
      if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) continue;
      commitQueue.enqueue({ key: part.id, scopeId: entity.scope.id, priority: () => commitPriority(entity),
        isValid: () => {
          if (resultEpoch !== documentEpoch || !epochAcknowledged || expiresAt <= Date.now()) return false;
          if (entitySourceCurrent(entity)) return true;
          if (entities.get(entity.id) === entity) queueRoot(entity.container, true);
          return false;
        },
        commit: () => {
          if (result.translation_status !== 'succeeded' || typeof result.translated_text !== 'string' || !result.translated_text.trim()) {
            failPart(entity, part, typeof result.error_code === 'string' ? result.error_code : 'invalid_result'); return;
          }
          if (!validatePlaceholderResult(entity, result.translated_text, part.text)) {
            failPart(entity, part, 'placeholder_mismatch');
            renderFailures.placeholder_mismatch = (renderFailures.placeholder_mismatch ?? 0) + 1;
            diagnostics(); return;
          }
          part.translated = result.translated_text.trim(); part.state = 'succeeded';
          const previousTimer = expiryTimers.get(part.id);
          if (previousTimer !== undefined) window.clearTimeout(previousTimer);
          const expire = () => {
            const timer = expiryTimers.get(part.id);
            if (timer !== undefined) window.clearTimeout(timer);
            expiryTimers.delete(part.id);
            expiryActions.delete(part.id);
            if (segmentOwners.get(part.id)?.part !== part) return;
            ownedWrite(() => {
              removeTranslation(entity);
              part.translated = undefined;
              part.state = 'discovered';
              entity.state = 'discovered';
            });
            lastPlanSignature = '';
            requestViewportPlan(true);
          };
          expiryActions.set(part.id, { at: expiresAt, expire });
          expiryTimers.set(part.id, window.setTimeout(expire, Math.max(0, expiresAt - Date.now())));
          if (entity.parts.every(candidate => candidate.state === 'succeeded')) renderEntity(entity);
        },
      });
    }
  };

  const resetTranslations = () => {
    clearExpiryTimers();
    ownedWrite(() => [...entities.values()].forEach((entity) => {
      removeTranslation(entity);
      entity.parts.forEach(part => { commitQueue.cancel(part.id); part.state = part.blocked ? 'failed' : 'discovered'; part.translated = undefined; });
      entity.state = 'discovered';
    }));
    requestViewportPlan(true);
    if (budgetExhausted && mode !== 'original') post({ type: 'reader_translation_status', state: 'budget_exhausted', count: entities.size });
  };

  const setMode = (nextMode: TranslationDisplayMode) => {
    mode = ['original', 'bilingual', 'translated'].includes(nextMode) ? nextMode : 'original';
    if (rule.adapter && mode === 'translated') mode = 'bilingual';
    if (budgetExhausted && mode !== 'original') post({ type: 'reader_translation_status', state: 'budget_exhausted', count: entities.size });
    ownedWrite(() => {
      const classes = new Set(html.className.split(/\s+/).filter(Boolean));
      classes.delete('reader-translation-original');
      classes.delete('reader-translation-bilingual');
      classes.delete('reader-translation-translated');
      classes.add(`reader-translation-${mode}`);
      setOwnedAttribute(html, 'class', [...classes].join(' '));
      scopeManager?.scopes.forEach(syncScopeMode);
      entities.forEach(syncModeForEntity);
    });
    if (mode !== 'original' && epochAcknowledged) scheduleViewportPlan(0, true);
  };

  const invalidateWithin = (root: Element) => {
    // A host-driven rebuild can recreate the same ids after removing our
    // DOM. It must be allowed to request/render them again.
    lastPlanSignature = '';
    for (const entity of [...entities.values()]) {
      if (entity.scope !== scopeFor(root)) continue;
      if (root === entity.container || root.contains(entity.container) ||
          entity.rootNodes.some((node) => root.contains(node) || node.contains(root))) {
        disposeEntity(entity, false);
      }
    }
  };

  const entityTouches = (entity: ParagraphEntity, root: Element) => entity.scope === scopeFor(root) && (
    root === entity.container || root.contains(entity.container) || entity.rootNodes.some(node => root.contains(node) || node.contains(root))
  );
  const translationPositionCurrent = (entity: ParagraphEntity) => {
    const translation = translations.get(entity.id);
    if (!translation) return entity.state !== 'translated';
    if (!translation.isConnected) return false;
    const wrapper = sourceWrappers.get(entity.id);
    const sourceAnchor = entity.renderedInside ? entity.rootNodes[entity.rootNodes.length - 1] : entity.insertionAnchor;
    let anchor = wrapper?.isConnected && wrapper.contains(sourceAnchor) ? wrapper : sourceAnchor;
    if (entity.renderedInside) while (anchor.parentNode && anchor.parentNode !== entity.container) anchor = anchor.parentNode;
    return translation.parentNode === (entity.renderedInside ? entity.container : anchor.parentNode)
      && translation.previousSibling === anchor;
  };
  const sourceVisibilityCurrent = (entity: ParagraphEntity) => {
    for (let element: Element | null = entity.container; element; element = composedParent(element)) {
      const style = styleFor(element);
      const ownedHidden = mode === 'translated' && (ownedSourceWrappers.has(element) ||
        (element === entity.container && element.classList.contains('reader-source-hidden') && sourceWrappers.has(entity.id)));
      if ((!ownedHidden && style.display === 'none') || style.visibility === 'hidden' || style.visibility === 'collapse' ||
          style.contentVisibility === 'hidden' || Number.parseFloat(style.opacity || '1') === 0) return false;
    }
    return true;
  };
  const prepareDiscovery = async (requestedRoot: Element, epoch: string) => {
    let root = requestedRoot;
    // A new block boundary inside an old run requires that run's container,
    // otherwise its unchanged leading/trailing inline prose would disappear.
    for (const entity of entities.values()) {
      if (entityTouches(entity, root) && entity.container.contains(root)) root = entity.container;
    }
    const candidates = [...entities.values()].filter(entity => entityTouches(entity, root));
    const groups = new Map<Element, ParagraphEntity[]>();
    candidates.forEach(entity => groups.set(entity.container, [...(groups.get(entity.container) ?? []), entity]));
    const preserved = new Set<Node>();
    let deadline = performance.now() + 8;
    for (const [container, group] of groups) {
      const signature = sourceTreeFingerprint(container);
      const stable = signature !== null && group.every(entity => entity.sourceTreeFingerprint === signature &&
        scopeIsCurrent(entity.scope) && entity.rootNodes.every(node => node.isConnected) &&
        canonicalParent(entity.insertionAnchor) === entity.anchorParent && translationPositionCurrent(entity) && sourceVisibilityCurrent(entity))
        && await discoveryUnchanged(container, epoch);
      if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return null;
      if (stable) group.forEach(entity => {
        entity.rootNodes.forEach(node => preserved.add(node));
        if (entity.state === 'translated' && translationStyleChanged(entity)) queueAttributeRender(entity);
      });
      else { lastPlanSignature = ''; group.forEach(entity => disposeEntity(entity, false)); }
      if (performance.now() >= deadline) {
        await new Promise<void>(resolve => window.setTimeout(resolve, 0));
        if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return null;
        deadline = performance.now() + 8;
      }
    }
    return { root, preserved };
  };

  const rootSignature = (root: Element) => `${normalizeText(root.textContent).length}:${root.childNodes.length}`;

  function queueRoot(root: Element, dirty: boolean, attributeOnly = false) {
    if (disposed || !root.isConnected || !withinActiveRoot(root) || isOwnedNode(root) || isExcluded(root)) return;
    if (attributeOnly && !pendingRoots.has(root) && !dirtyRoots.has(root)) attributeRoots.add(root);
    else if (!attributeOnly) attributeRoots.delete(root);
    (dirty ? dirtyRoots : pendingRoots).add(root);
    const now = performance.now();
    const previous = deferredRoots.get(root);
    const signature = rootSignature(root);
    deferredRoots.set(root, {
      firstAt: previous?.firstAt ?? now,
      lastAt: previous?.signature === signature ? previous.lastAt : now,
      signature,
    });
    scheduleDrain();
  }

  const collapseRoots = (roots: Element[]) => roots.filter((root, index) =>
    !roots.some((candidate, otherIndex) => otherIndex !== index && candidate.contains(root)));

  const nearestBlockRoot = (node: Node | null) => {
    let element = node?.nodeType === Node.ELEMENT_NODE ? node as Element : node?.parentElement ?? null;
    const sourceWrapper = element?.closest('.reader-source-layout');
    if (sourceWrapper) {
      const entityId = sourceWrapper.getAttribute('data-reader-source-layout-for');
      const sourceEntity = entityId ? entities.get(entityId) : undefined;
      if (sourceEntity?.container.isConnected) element = sourceEntity.container;
    }
    while (element && element !== activeContentRoot && element.parentElement && !isBlock(element)) element = element.parentElement;
    return element && withinActiveRoot(element) ? element : null;
  };

  const isLongContainer = (root: Element) => rectFor(root).height > Math.max(window.innerHeight, 1) * 10;

  async function drainRoots() {
    if (traversalRunning || disposed || !epochAcknowledged) return;
    const epoch = documentEpoch;
    sourceTreeCache = new WeakMap();
    traversalRunning = true;
    try {
      sweepDisconnectedEntities();
      for (const root of [...pendingRoots]) if (!root.isConnected) pendingRoots.delete(root);
      for (const root of [...attributeRoots]) if (!root.isConnected) attributeRoots.delete(root);
      for (const root of [...dirtyRoots]) if (!root.isConnected) dirtyRoots.delete(root);
      for (const root of [...pendingDirectRoots]) if (!root.isConnected) pendingDirectRoots.delete(root);
      for (const root of [...deferredRoots.keys()]) if (!root.isConnected) deferredRoots.delete(root);
      for (const root of [...processingRoots]) if (!root.isConnected) processingRoots.delete(root);
      for (const root of [...pendingContainers]) {
        if (root.isConnected) continue;
        containerObserver.unobserve(root);
        pendingContainers.delete(root);
      }
      const directCandidates = [...pendingDirectRoots];
      pendingDirectRoots.clear();
      for (const root of directCandidates) {
        await discoverGeneric(root, epoch, true);
        if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return;
      }
      const candidates = collapseRoots([...new Set([...pendingRoots, ...dirtyRoots])].filter((root) => root.isConnected));
      const pureAttributeRoots = new Set(candidates.filter(root => attributeRoots.has(root) &&
        ![...pendingRoots, ...dirtyRoots].some(candidate => root.contains(candidate) && !attributeRoots.has(candidate))));
      // A collapsed ancestor inherits structural work from its descendants,
      // including across the stability-delay turn. Never promote it back to
      // an attribute-only fast path after clearing the original child queue.
      for (const root of candidates) if (!pureAttributeRoots.has(root)) attributeRoots.delete(root);
      pendingRoots.clear();
      dirtyRoots.clear();
      candidates.sort((left, right) => Math.abs(rectFor(left).top) - Math.abs(rectFor(right).top));
      for (const root of candidates) {
        const stability = deferredRoots.get(root);
        const now = performance.now();
        if (stability && now - stability.lastAt < 300 && now - stability.firstAt < 1_200) {
          pendingRoots.add(root);
          continue;
        }
        deferredRoots.delete(root);
        processingRoots.add(root);
        try {
          if (pureAttributeRoots.has(root)) {
            const unchanged = await discoveryUnchanged(root, epoch);
            if (disposed || documentEpoch !== epoch || !scopeFor(root)?.alive) return;
            if (unchanged) {
              for (const entity of entities.values()) if (entityTouches(entity, root) && entity.state === 'translated' && translationStyleChanged(entity)) queueAttributeRender(entity);
              continue;
            }
          }
          version += 1;
          styleCache = new WeakMap();
          rectCache = new WeakMap();
          const prepared = await prepareDiscovery(root, epoch);
          if (!prepared) return;
          if (nodeRule(prepared.root).adapter) await discoverSocial(prepared.root, epoch, prepared.preserved);
          else await discoverGeneric(prepared.root, epoch, false, prepared.preserved);
        } finally {
          for (const attributeRoot of attributeRoots) {
            if (root.contains(attributeRoot) && !pendingRoots.has(attributeRoot) && !dirtyRoots.has(attributeRoot)) attributeRoots.delete(attributeRoot);
          }
          processingRoots.delete(root);
        }
      }
      post({ count: entities.size, state: entities.size ? 'ready' : 'waiting_for_content', type: 'reader_translation_status' });
      diagnostics();
      scheduleViewportPlan(0);
    } finally {
      traversalRunning = false;
      if (pendingRoots.size || dirtyRoots.size) scheduleDrain(100);
    }
  }

  function scheduleDrain(delay = 50) {
    if (drainTimer !== null || disposed || !epochAcknowledged) return;
    drainTimer = window.setTimeout(() => {
      drainTimer = null;
      void drainRoots();
    }, delay);
  }

  const findArticlePriorityRoot = () => {
    // Readability runs only on a clone. Temporary correspondence attributes
    // never touch the host DOM, and survive its wrapper/attribute cleanup.
    // Avoid cloning exceptionally large pages just to establish a priority.
    const live = boundedReaderElements(document);
    const clone = document.cloneNode(true) as Document;
    const marker = 'data-reader-analysis-node';
    clone.querySelectorAll('*').forEach((element, index) => element.setAttribute(marker, String(index)));
    const article = new Readability(clone).parse();
    if (!article?.content) return null;
    const parsed = new DOMParser().parseFromString(article.content, 'text/html');
    const mapped = new Set<Element>();
    let mappedAncestor: Element | null = null;
    for (const extracted of parsed.querySelectorAll(`[${marker}]`)) {
      // Descendants cannot expand an already mapped candidate's scope.
      if (mappedAncestor?.contains(extracted)) continue;
      const original = live[Number(extracted.getAttribute(marker))];
      if (!original || !document.body.contains(original)) continue;
      const walker = parsed.createTreeWalker(extracted, NodeFilter.SHOW_TEXT);
      let text: Node | null;
      let anchor = '';
      while ((text = walker.nextNode())) {
        const value = normalizeText(text.nodeValue);
        if (value.length >= 20) { anchor = value.slice(0, 160); break; }
      }
      if (anchor && normalizeText(original.textContent).includes(anchor)) {
        mapped.add(original);
        mappedAncestor = extracted;
      }
    }
    // A paragraph id is only one piece of evidence. Join all retained live
    // candidates so the first identified paragraph cannot truncate the scope.
    return mapped.size ? commonAncestor([...mapped], document.body) : null;
  };

  const chooseRoot = () => {
    articlePriorityRoot = null;
    for (const candidate of rule.scope.mainRoots) {
      try {
        const found = [...document.querySelectorAll(candidate)];
        if (!found.length) continue;
        if (rule.scope.rootMode === 'exclusive') return found[0];
        articlePriorityRoot = commonAncestor(found, document.body);
        return document.body;
      } catch { /* Compiled rules are also checked in Chromium. */ }
    }
    if (rule.scope.fallback === 'stop') return null;
    if (rule.scope.fallback === 'readability-then-document') {
      try {
        articlePriorityRoot = findArticlePriorityRoot();
      } catch { /* Generic traversal remains available when Readability cannot parse. */ }
    }
    return document.body;
  };

  const planContainers = (root: Element) => {
    const viewport = Math.max(window.innerHeight, 1);
    const rootRect = rectFor(root);
    if (rootRect.height <= viewport * 10 || nodeRule(root).adapter) {
      if (root === activeContentRoot || (rootRect.bottom >= -viewport && rootRect.top <= viewport * 3)) {
        queueRoot(root, false);
      } else if (!pendingContainers.has(root)) {
        pendingContainers.add(root);
        containerObserver.observe(root);
      }
      return;
    }
    const children = [...root.children].filter((child) => isBlock(child) && !isExcluded(child));
    if (!children.length) {
      queueRoot(root, false);
      return;
    }
    if ([...root.childNodes].some((node) =>
      (node.nodeType === Node.TEXT_NODE && normalizeText(node.nodeValue)) ||
      (node.nodeType === Node.ELEMENT_NODE && !isBlock(node as Element) && !isExcluded(node as Element)))) {
      pendingDirectRoots.add(root);
      scheduleDrain();
    }
    children.forEach((container) => {
      const rect = rectFor(container);
      if (rect.bottom >= -viewport && rect.top <= viewport * 3) queueRoot(container, false);
      else {
        pendingContainers.add(container);
        containerObserver.observe(container);
      }
    });
  };

  const ruleAttributes = new Set(['class', 'id']);
  config.rules.forEach(candidate => candidate.scope.mainRoots.forEach(value => {
    for (const attribute of value.matchAll(/\[([a-zA-Z_][\w-]*)/g)) ruleAttributes.add(attribute[1]);
  }));
  config.rules.forEach(candidate => candidate.matches.forEach(match => [...(match.requiredSelectors ?? []), ...(match.absentSelectors ?? [])].forEach(value => {
    for (const attribute of value.matchAll(/\[([a-zA-Z_][\w-]*)/g)) ruleAttributes.add(attribute[1]);
  })));
  const observedAttributes = [...new Set(['aria-hidden', 'class', 'hidden', 'inert', 'style', 'slot', 'translate', 'href', 'target', 'title', 'rel', ...READER_CONTENT_ATTRIBUTES, ...ruleAttributes])];
  let ruleRefreshTimer: number | null = null;
  const refreshRules = () => {
    ruleRefreshTimer = null;
    if (!epochAcknowledged || disposed) return;
    // Social sites may mount their permitted content root after the handshake.
    // No scopes exist yet in that case, so recover before iterating them.
    if (!scopeManager) {
      rule = selectRule(location.href, config.rules, config.profile, document);
      setOwnedAttribute(html, 'data-reader-translation-profile', rule.id);
      if (bridgeWindow.__readerTranslationBridge) bridgeWindow.__readerTranslationBridge.profile = rule.id;
      activateContentRoot();
      return;
    }
    for (const scope of scopeManager.scopes) {
      const next = scope.kind === 'shadow' ? scope.parent!.rule : selectRule(scope.kind === 'main' ? location.href : scope.ownerDocument.URL.startsWith('about:') ? scope.parent!.url : scope.ownerDocument.URL, config.rules, scope.kind === 'main' ? config.profile : undefined, scope.ownerDocument);
      if (scope.rule === next) continue;
      const previousRule = scope.rule;
      scope.rule = next;
      if (scope.kind === 'main') {
        rule = next; activeContentRoot = chooseRoot();
        setOwnedAttribute(html, 'data-reader-translation-profile', rule.id);
        if (bridgeWindow.__readerTranslationBridge) bridgeWindow.__readerTranslationBridge.profile = rule.id;
      } else if (scope.kind === 'frame') {
        scope.contentRoot = null;
        for (const selector of next.scope.mainRoots) { try { scope.contentRoot = scope.ownerDocument.querySelector(selector); } catch { /* Compiled rule. */ } if (scope.contentRoot) break; }
        if (!scope.contentRoot && next.scope.fallback !== 'stop') scope.contentRoot = scope.ownerDocument.body;
      }
      for (const entity of [...entities.values()]) if (entity.scope === scope) {
        if (isExcluded(entity.container) || !withinActiveRoot(entity.container) || !validNaturalText(entity.payloadText, entity.container)) disposeEntity(entity, false);
        else if (entity.state === 'translated' && JSON.stringify(previousRule.segmentation) === JSON.stringify(next.segmentation)) ownedWrite(() => syncModeForEntity(entity));
      }
      scopeRoots(scope).forEach(root => queueRoot(root, true));
      if (scope.kind === 'main' && activeContentRoot) queueRoot(activeContentRoot, true);
    }
  };
  const scheduleRuleRefresh = () => {
    if (ruleRefreshTimer === null) ruleRefreshTimer = window.setTimeout(refreshRules, 0);
  };
  const processMutations = (records: MutationRecord[]) => {
    if (writingOwnedDom) return;
    // Attribute and subtree mutations invalidate layout facts before the
    // affected visual root is classified.
    styleCache = new WeakMap();
    rectCache = new WeakMap();
    sourceTreeCache = new WeakMap();
    if (document.body !== observedBody) {
      if (bodyTimer === null) bodyTimer = window.setTimeout(() => { bodyTimer = null; beginEpoch(); }, 0);
      return;
    }
    for (const record of records) {
      if (isOwnedMutationRecord(record)) continue;
      const split = record.type === 'characterData' ? splitTextOwners.get(record.target) : undefined;
      if (split && split.nodes[0].data !== split.values[0] && record.target.parentElement) {
        // React still owns the original Text object. Replacing its value
        // replaces the entire original run, not just our first paragraph.
        ownedWrite(() => {
          invalidateWithin(record.target.parentElement!);
          restoreTextSplit(split, true);
        });
      }
      if (record.type === 'childList' || (record.type === 'attributes' && ruleAttributes.has(record.attributeName ?? ''))) scheduleRuleRefresh();
      if (record.type === 'attributes' && record.attributeName === 'slot') {
        for (const entity of entities.values()) {
          if (entity.container !== record.target) continue;
          const translation = translations.get(entity.id);
          if (translation && !entity.renderedInside) setOwnedAttribute(translation, 'slot', entity.container.getAttribute('slot') ?? '');
        }
        scheduleViewportPlan(0);
        continue;
      }
      if (record.type === 'attributes' && ['href', 'target', 'title', 'rel'].includes(record.attributeName ?? '')) {
        for (const entity of entities.values()) {
          if (entity.state === 'translated' && entity.preservedVariables.some(variable => variable.sourceElement === record.target || ((record.target as Element).tagName === 'BASE' && variable.kind === 'link' && variable.sourceElement.ownerDocument === record.target.ownerDocument))) {
            queueAttributeRender(entity);
          }
        }
        continue;
      }
      const recordScope = scopeFor(record.target);
      if (recordScope?.kind === 'shadow' && (record.target === recordScope.root || record.target.parentNode === recordScope.root)) discoverShadowDirect(recordScope);
      if (record.type === 'childList') {
        if ([...record.addedNodes, ...record.removedNodes].some(node => node.nodeType === Node.ELEMENT_NODE && ((node as Element).tagName === 'BASE' || (node as Element).querySelector('base')))) {
          for (const entity of entities.values()) if (entity.state === 'translated' && entity.preservedVariables.some(variable => variable.kind === 'link')) queueAttributeRender(entity);
        }
        for (const removed of record.removedNodes) {
          for (const entity of [...entities.values()]) {
            if (removed === entity.container || (removed.nodeType === Node.ELEMENT_NODE && (removed as Element).contains(entity.container)) ||
                entity.rootNodes.some((node) => removed === node || (removed.nodeType === Node.ELEMENT_NODE && (removed as Element).contains(node)))) {
              disposeEntity(entity, true);
            }
          }
          if (removed === activeContentRoot || (removed.nodeType === Node.ELEMENT_NODE && activeContentRoot && (removed as Element).contains(activeContentRoot))) {
            beginEpoch();
            return;
          }
        }
        for (const added of record.addedNodes) {
          if (isOwnedNode(added)) continue;
          if (added.nodeType === Node.ELEMENT_NODE) {
            const affected = nearestBlockRoot(added);
            if (affected) planContainers(affected);
          } else if (added.nodeType === Node.TEXT_NODE) {
            const affected = nearestBlockRoot(record.target);
            if (!affected) continue;
            if (affected === activeContentRoot && isLongContainer(affected)) {
              pendingDirectRoots.add(affected);
              scheduleDrain();
            } else {
              queueRoot(affected, true);
            }
          }
        }
        if (record.addedNodes.length) continue;
        if (record.removedNodes.length) {
          const affected = nearestBlockRoot(record.target);
          if (affected && affected === activeContentRoot && isLongContainer(affected)) {
            pendingDirectRoots.add(affected);
            scheduleDrain();
          } else if (affected) {
            queueRoot(affected, true);
          }
          continue;
        }
      }
      const root = nearestBlockRoot(record.target);
      if (root && record.type === 'attributes') {
        if (isExcluded(root)) { discoveryFacts.delete(root); invalidateWithin(root); }
        else queueRoot(root, false, true);
      } else if (root) queueRoot(root, record.type !== 'childList' || record.removedNodes.length > 0);
    }
  };
  const mutationObserver = new MutationObserver(processMutations);

  const style = document.createElement('style');
  style.id = 'reader-translation-style';
  style.textContent = `
    .reader-translation-node{box-sizing:border-box!important;color:inherit!important;display:block!important;font-family:inherit!important;font-size:inherit!important;font-style:inherit!important;font-weight:inherit!important;inline-size:auto!important;line-height:inherit!important;margin:.35em 0 .8em!important;max-inline-size:100%!important;opacity:1!important;overflow-wrap:anywhere!important;padding:0!important;position:relative!important;text-align:start!important;unicode-bidi:plaintext!important;white-space:pre-wrap!important;word-break:break-word!important}
    html.reader-translation-original .reader-translation-node{display:none!important}
    html.reader-translation-translated .reader-source-hidden{display:none!important}
    html.reader-translation-translated .reader-source-layout{display:none!important}
    .reader-preserved-variable{font-family:inherit!important;white-space:pre-wrap!important}
  `;

  const discoverShadowDirect = (scope: DocumentScope) => {
    if (scope.kind !== 'shadow' || !scope.alive) return;
    // Direct text runs need no synthetic wrapper or host DOM restructuring.
    let run: Node[] = [];
    const flush = () => { if (run.length) registerRun(scope.host!, run); run = []; };
    for (const node of scope.root.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) run.push(node);
      else flush();
    }
    flush();
  };
  const scopeRoots = (scope: DocumentScope): Element[] => scope.kind === 'shadow'
    ? [...(scope.root as ShadowRoot).children].filter(element => !isOwnedNode(element))
    : scope.contentRoot ? [scope.contentRoot] : [];
  const syncScopeMode = (scope: DocumentScope) => {
    if (scope.kind === 'main') return;
    const target = scope.kind === 'frame' ? scope.ownerDocument.documentElement : scope.host!;
    for (const value of ['original', 'bilingual', 'translated']) {
      if (value === mode) addOwnedClass(target, `reader-translation-${value}`);
      else removeOwnedClass(target, `reader-translation-${value}`);
    }
  };
  const initializeScopes = () => {
    scopeManager = createDocumentScopes({
      document, rule, selectRule: (url, ownerDocument) => selectRule(url, config.rules, undefined, ownerDocument),
      eligible: host => {
        const parent = scopeFor(host);
        if (!parent?.alive || isOwnedNode(host) || !withinActiveRoot(host)) return false;
        // The iframe itself is normally excluded from text traversal. Its
        // ancestors still obey the same navigation/form exclusion boundary.
        return host.tagName === 'IFRAME' ? !host.parentElement || !isExcluded(host.parentElement) : !isExcluded(host);
      },
      added: scope => {
        const scopeStyle = scope.ownerDocument.createElement('style');
        scopeStyle.id = 'reader-translation-style';
        scopeStyle.textContent = scope.kind === 'shadow'
          ? style.textContent!.replace(/html\.reader-translation-(original|translated)/g, ':host(.reader-translation-$1)')
          : style.textContent;
        ownedWrite(() => (scope.kind === 'shadow' ? scope.root : scope.ownerDocument.head ?? scope.ownerDocument.documentElement).appendChild(scopeStyle));
        const observer = new MutationObserver(processMutations);
        observer.observe(scope.root, { attributes: true, attributeOldValue: true, attributeFilter: observedAttributes, characterData: true, childList: true, subtree: true });
        const selection = () => rememberSelection(scope.view);
        scope.ownerDocument.addEventListener('selectionchange', selection);
        scope.cleanup.push(() => { observer.disconnect(); scopeStyle.remove(); const target = scope.kind === 'frame' ? scope.ownerDocument.documentElement : scope.host!; target.classList.remove('reader-translation-original', 'reader-translation-bilingual', 'reader-translation-translated'); scope.ownerDocument.removeEventListener('selectionchange', selection); });
        syncScopeMode(scope);
        discoverShadowDirect(scope);
        scopeRoots(scope).forEach(root => queueRoot(root, false));
      },
      removed: scope => {
        for (const [id, length] of plannedParts) if (id.startsWith(`${navigationId}:${documentEpoch}:${scope.id}:`)) { plannedParts.delete(id); plannedCharacters -= length; }
        post({ type: 'reader_translation_scope_disposed', scope_id: scope.id });
        if (selectedNode && scopeFor(selectedNode) === scope) selectedNode = null;
        for (const entity of [...entities.values()]) if (entity.scope === scope) disposeEntity(entity, true);
        for (const set of [pendingRoots, pendingDirectRoots, dirtyRoots, attributeRoots, processingRoots, hiddenRoots, pendingContainers]) {
          for (const root of set) if (scopeFor(root) === scope) { set.delete(root); containerObserver.unobserve(root); visibilityObserver.unobserve(root); }
        }
        for (const root of deferredRoots.keys()) if (scopeFor(root) === scope) deferredRoots.delete(root);
        scheduleViewportPlan(0, true);
      },
      changed: scope => { if (scope.alive) { discoverShadowDirect(scope); scopeRoots(scope).forEach(root => queueRoot(root, false)); } },
      viewport: () => {
        styleCache = new WeakMap(); rectCache = new WeakMap();
        for (const root of [...hiddenRoots].slice(0, 128)) {
          hiddenRoots.delete(root);
          if (root.isConnected && visiblyRendered(root)) {
            const scope = scopeFor(root);
            if (scope?.kind === 'shadow') discoverShadowDirect(scope);
            queueRoot(root, false);
          }
        }
        scheduleViewportPlan(40);
      },
      report: reason => { renderFailures[reason] = (renderFailures[reason] ?? 0) + 1; },
    });
    scopeManager.start();
  };

  const clearEpoch = () => ownedWrite(() => {
    clearExpiryTimers();
    scrollAnchor.reset();
    commitQueue.reset();
    deferredAttributes.clear();
    if (attributeRetryTimer !== null) window.clearTimeout(attributeRetryTimer);
    attributeRetryTimer = null;
    plannedParts.clear(); plannedCharacters = 0; budgetExhausted = false;
    if (ruleRefreshTimer !== null) window.clearTimeout(ruleRefreshTimer);
    ruleRefreshTimer = null;
    layoutManager.releaseAll();
    selectedNode = null;
    scopeManager?.dispose();
    scopeManager = null;
    entities.forEach((entity) => disposeEntity(entity, true));
    entities.clear();
    restoreParagraphTextSplits();
    translations.clear();
    sourceWrappers.clear();
    observedParagraphs.forEach((element) => paragraphObserver.unobserve(element));
    observedParagraphs.clear();
    pendingContainers.forEach((element) => containerObserver.unobserve(element));
    pendingContainers.clear();
    hiddenRoots.forEach((element) => visibilityObserver.unobserve(element));
    hiddenRoots.clear();
    pendingRoots.clear();
    pendingDirectRoots.clear();
    dirtyRoots.clear();
    attributeRoots.clear();
    discoveryFacts = new WeakMap();
    sourceTreeCache = new WeakMap();
    processingRoots.clear();
    deferredRoots.clear();
    activeContentRoot = null;
    articlePriorityRoot = null;
    lastPlanSignature = '';
    forceNextViewportPlan = false;
    if (drainTimer !== null) window.clearTimeout(drainTimer);
    if (viewportTimer !== null) window.clearTimeout(viewportTimer);
    drainTimer = null;
    viewportTimer = null;
  });

  function beginEpoch() {
    clearReaderSnapshot();
    clearEpoch();
    sequence = 0;
    version = 0;
    epochAcknowledged = false;
    currentHref = location.href;
    observedBody = document.body;
    rule = selectRule(location.href, config.rules, config.profile, document);
    documentEpoch = `epoch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
    setOwnedAttribute(html, 'data-reader-translation-profile', rule.id);
    post({ profile: rule.id, type: 'reader_translation_navigation_reset' });
  }

  const confirmEpoch = (candidate: string, nonce?: string) => {
    if (candidate !== documentEpoch || disposed) return;
    if (nonce) post({ challenge_nonce: nonce, type: 'reader_translation_handshake_ready' });
    if (epochAcknowledged && activeContentRoot) {
      scheduleViewportPlan(0, true);
      post({ count: entities.size, state: activeContentRoot ? 'ready' : 'profile_mismatch', type: 'reader_translation_status' });
      return;
    }
    epochAcknowledged = true;
    activateContentRoot();
  };

  function activateContentRoot() {
    const root = chooseRoot();
    if (!root) {
      post({ count: 0, state: 'profile_mismatch', type: 'reader_translation_status' });
      return;
    }
    activeContentRoot = root;
    initializeScopes();
    styleCache = new WeakMap();
    rectCache = new WeakMap();
    planContainers(root);
  }

  const refresh = () => {
    if (locationChanged()) return;
    refreshRules();
    scopeManager?.refresh();
    scopeManager?.scopes.forEach(scope => scopeRoots(scope).forEach(root => queueRoot(root, true)));
    if (activeContentRoot?.isConnected && epochAcknowledged) queueRoot(activeContentRoot, true);
  };

  const rememberSelection = (view: Window | Event = window) => {
    const selection = ('getSelection' in view ? view as Window : window).getSelection();
    if (selection && !selection.isCollapsed && selection.anchorNode?.isConnected) {
      const anchor = selection.anchorNode;
      selectedNode = anchor.nodeType === Node.ELEMENT_NODE
        ? anchor.childNodes[selection.anchorOffset] ?? anchor : anchor;
    }
  };
  // On-demand geometry only: bounded to the selected entity, never a page scan.
  const diagnoseDisplay = (entity: ParagraphEntity): string => {
    if (mode === 'original') return 'mode_hidden';
    const translation = translations.get(entity.id);
    if (!translation) return 'not_inserted';
    if (!translation.isConnected) return 'host_hidden';
    // Frame scheduling has mapped rectangles; detailed text clipping still
    // needs per-frame text-range intersection and remains conservative.
    if (entity.scope.ownerDocument !== document) return 'unknown';
    const ancestors: { element: Element; style: CSSStyleDeclaration }[] = [];
    let ancestor: Element | null = translation;
    while (ancestor && ancestors.length < 64) {
      const style = window.getComputedStyle(ancestor);
      if (style.display === 'none' || (ancestor === translation && style.visibility !== 'visible') || Number(style.opacity) === 0) return 'host_hidden';
      ancestors.push({ element: ancestor, style });
      ancestor = composedParent(ancestor);
    }
    if (ancestor) return 'unknown';
    const walker = document.createTreeWalker(translation, NodeFilter.SHOW_TEXT);
    const rects: DOMRect[] = [];
    let textNode: Node | null;
    let count = 0;
    while ((textNode = walker.nextNode())) {
      if (++count > 256) return 'unknown';
      if (!textNode.textContent?.trim()) continue;
      let textAncestor = textNode.parentElement;
      let depth = 0;
      while (textAncestor && textAncestor !== translation) {
        if (++depth > 64) return 'unknown';
        const textStyle = window.getComputedStyle(textAncestor);
        const transparentPaint = [textStyle.color, textStyle.webkitTextFillColor].some(color =>
          color === 'transparent' || /^(?:rgba|hsla)\([^)]*,\s*0(?:\.0+)?%?\s*\)$|\/\s*0(?:\.0+)?%?\s*\)$/.test(color));
        if (transparentPaint) return 'unknown';
        if (textStyle.display === 'none' || textStyle.visibility !== 'visible' || Number(textStyle.opacity) === 0 ||
            textStyle.clipPath !== 'none' || textStyle.transform !== 'none' || textStyle.maskImage !== 'none' ||
            textStyle.filter !== 'none' || textStyle.scale !== 'none' || textStyle.rotate !== 'none' || textStyle.translate !== 'none' ||
            textStyle.perspective !== 'none' || !['1', 'normal'].includes(textStyle.zoom) || textStyle.clip !== 'auto' || textStyle.contentVisibility !== 'visible' ||
            /paint|strict|content/.test(textStyle.contain) || /hidden|clip|scroll|auto/.test(`${textStyle.overflowX} ${textStyle.overflowY}`)) return 'unknown';
        textAncestor = textAncestor.parentElement;
      }
      const range = document.createRange();
      range.selectNodeContents(textNode);
      for (const rect of range.getClientRects()) {
        if (rect.width > 0 && rect.height > 0) rects.push(rect);
        if (rects.length > 512) return 'unknown';
      }
    }
    if (!rects.length) return 'unknown';
    const transparentPaint = (style: CSSStyleDeclaration) => [style.color, style.webkitTextFillColor].some(color =>
      color === 'transparent' || /^(?:rgba|hsla)\([^)]*,\s*0(?:\.0+)?%?\s*\)$|\/\s*0(?:\.0+)?%?\s*\)$/.test(color));
    const uncertain = ancestors.some(({ style }) => transparentPaint(style) || style.clipPath !== 'none' || style.transform !== 'none' ||
      style.clip !== 'auto' || style.maskImage !== 'none' || style.contentVisibility !== 'visible' ||
      style.filter !== 'none' || style.scale !== 'none' || style.rotate !== 'none' || style.translate !== 'none' ||
      style.perspective !== 'none' || !['1', 'normal'].includes(style.zoom) || /paint|strict|content/.test(style.contain));
    for (const { element, style } of ancestors) {
      // Root scrolling is classified against the viewport, not as a clipped host.
      if (element === document.documentElement || element === document.body) continue;
      const clipX = /^(hidden|clip|scroll|auto)$/.test(style.overflowX);
      const clipY = /^(hidden|clip|scroll|auto)$/.test(style.overflowY);
      if (!clipX && !clipY) continue;
      const box = element.getBoundingClientRect();
      const left = box.left + element.clientLeft;
      const top = box.top + element.clientTop;
      const right = left + element.clientWidth;
      const bottom = top + element.clientHeight;
      if (rects.some(rect => (clipX && (rect.left < left - 1 || rect.right > right + 1)) ||
          (clipY && (rect.top < top - 1 || rect.bottom > bottom + 1)))) return uncertain ? 'unknown' : 'clipped';
    }
    if (uncertain) return 'unknown';
    const inViewport = rects.some(rect => rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth);
    if (!inViewport) return 'offscreen';
    if (rects.some(rect => rect.top < -1 || rect.bottom > window.innerHeight + 1 || rect.left < -1 || rect.right > window.innerWidth + 1)) return 'partial';
    return 'visible';
  };
  const diagnoseSelection = (nonce: string) => {
    if (disposed || typeof nonce !== 'string' || nonce.length > 80) return;
    rememberSelection();
    const node = selectedNode?.isConnected ? selectedNode : null;
    const element = node?.nodeType === Node.ELEMENT_NODE ? node as Element : node?.parentElement;
    let reason = 'no_selection';
    let ruleIndex = -1;
    const translatedId = element?.closest('[data-reader-translation-for]')?.getAttribute('data-reader-translation-for');
    const entity = translatedId ? entities.get(translatedId) : node ? [...entities.values()].find((candidate) =>
      candidate.rootNodes.some((root) => root === node || root.contains(node))) : undefined;
    if (element) {
      ruleIndex = nodeRule(element).segmentation.exclude.findIndex((selector) => Boolean(element.closest(selector)));
      if (entity) reason = entity.state;
      else if (ruleIndex >= 0) reason = 'excluded';
      else if (!withinActiveRoot(element)) reason = 'outside_scope';
      else if (selector(nodeRule(element).segmentation.preformatted) && element.closest(selector(nodeRule(element).segmentation.preformatted))) reason = 'preformatted';
      else reason = 'not_discovered';
    }
    post({ type: 'reader_translation_selection_diagnostic', challenge_nonce: nonce,
      reason, display_status: entity ? diagnoseDisplay(entity) : undefined, rule_index: ruleIndex, tag_name: element?.tagName ?? '',
      profile_id: entity?.scope.rule.id ?? rule.id, scope_id: entity?.scope.id ?? (node ? scopeFor(node)?.id : null) ?? 'main', segment_id: entity?.id ?? null });
  };

  const reconnect = (nonce?: string) => {
    if (disposed) return;
    locationChanged();
    post(nonce
      ? { challenge_nonce: nonce, type: 'reader_translation_handshake' }
      : { profile: rule.id, type: 'reader_translation_navigation_reset' });
  };

  const originalPushState = history.pushState;
  const originalReplaceState = history.replaceState;
  const locationChanged = () => {
    if (location.href === currentHref) return false;
    if (isDocumentAnchorChange(currentHref, location.href, document)) {
      currentHref = location.href;
      post({ type: 'reader_translation_location_changed' });
      scheduleViewportPlan(40);
      return false;
    }
    beginEpoch();
    return true;
  };
  const acceptReaderRequest = (requestId: string, revision: number, cancelled: boolean) => {
    if (!Number.isSafeInteger(revision) || revision < 1 || !/^reader-[a-z0-9-]{1,100}$/.test(requestId) ||
        revision < readerRequestState.revision) return false;
    if (revision > readerRequestState.revision) {
      clearReaderSnapshot();
      readerRequestState = { revision, id: requestId, cancelled };
    } else if (requestId !== readerRequestState.id) return false;
    if (cancelled) {
      readerRequestState.cancelled = true;
      if (readerSnapshot?.requestId === requestId) clearReaderSnapshot();
    }
    return !readerRequestState.cancelled;
  };
  const extractReader = (requestId: string, expectedEpoch: string, revision: number) => {
    if (disposed || !/^reader-[a-z0-9-]{1,100}$/.test(requestId)) return;
    locationChanged();
    if (document.body !== observedBody) beginEpoch();
    if (expectedEpoch !== documentEpoch || !epochAcknowledged) {
      post({ type: 'reader_extraction_result', request_id: requestId, error: 'changed' });
      return;
    }
    // A cancellation can arrive before a delayed extraction injection. Keep a
    // constant-size revision watermark so old commands cannot revive snapshots.
    if (!acceptReaderRequest(requestId, revision, false)) return;
    try {
      // Flush host mutations before creating a snapshot so old queued records
      // cannot invalidate a newly extracted version of the same article.
      processMutations(mutationObserver.takeRecords());
      if (expectedEpoch !== documentEpoch) throw new Error('changed');
      const result = extractWebReader(document);
      watchReaderSnapshot(requestId, result.dependencies);
      post({ type: 'reader_extraction_result', request_id: requestId, article: result.article });
    } catch (error) {
      clearReaderSnapshot();
      post({ type: 'reader_extraction_result', request_id: requestId,
        error: error instanceof Error && ['too_large', 'changed'].includes(error.message) ? error.message : 'unavailable' });
    }
  };
  const wrappedPushState: History['pushState'] = (...args) => {
    const value = Reflect.apply(originalPushState, history, args);
    locationChanged();
    return value;
  };
  const wrappedReplaceState: History['replaceState'] = (...args) => {
    const value = Reflect.apply(originalReplaceState, history, args);
    locationChanged();
    return value;
  };
  const handleResize = () => {
    // Sibling translations cannot inherit responsive typography from their
    // source element. Refresh through the bounded existing render queue.
    for (const entity of entities.values()) {
      if (entity.state === 'translated' && translationStyleChanged(entity)) deferredAttributes.add(entity.id);
    }
    scheduleDeferredAttributes();
    scheduleViewportPlan();
  };
  const handleScroll = () => scheduleViewportPlan(40);

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    clearReaderSnapshot();
    readerRequestState = { revision: 0, id: '', cancelled: false };
    commitQueue.dispose();
    scrollAnchor.dispose();
    mutationObserver.disconnect();
    paragraphObserver.disconnect();
    containerObserver.disconnect();
    visibilityObserver.disconnect();
    clearEpoch();
    if (bodyTimer !== null) window.clearTimeout(bodyTimer);
    style.remove();
    html.removeAttribute('data-reader-translation-profile');
    html.classList.remove('reader-translation-original', 'reader-translation-bilingual', 'reader-translation-translated');
    window.removeEventListener('popstate', locationChanged);
    window.removeEventListener('hashchange', locationChanged);
    window.removeEventListener('resize', handleResize);
    window.removeEventListener('scroll', handleScroll);
    document.removeEventListener('selectionchange', rememberSelection);
    document.removeEventListener('visibilitychange', expireDueTranslations);
    try {
      if (history.pushState === wrappedPushState) history.pushState = originalPushState;
      if (history.replaceState === wrappedReplaceState) history.replaceState = originalReplaceState;
    } catch { /* The document is being discarded. */ }
    diagnostics('disposed');
  };

  ownedWrite(() => (document.head ?? html).appendChild(style));
  try { history.pushState = wrappedPushState; history.replaceState = wrappedReplaceState; } catch { /* immutable History */ }
  window.addEventListener('popstate', locationChanged);
  window.addEventListener('hashchange', locationChanged);
  window.addEventListener('resize', handleResize);
  window.addEventListener('scroll', handleScroll, { passive: true });
  window.addEventListener('pagehide', cleanup, { once: true });
  document.addEventListener('selectionchange', rememberSelection);
  document.addEventListener('visibilitychange', expireDueTranslations);
  mutationObserver.observe(html, {
    attributeFilter: observedAttributes,
    attributes: true,
    attributeOldValue: true,
    characterData: true,
    childList: true,
    subtree: true,
  });
  bridgeWindow.__readerTranslationBridge = {
    applyTranslations,
    retryFailed: () => {
      for (const entity of entities.values()) {
        entity.parts.forEach(part => { if (part.state === 'failed' && !part.blocked) part.state = 'discovered'; });
        if (entity.state === 'failed') entity.state = 'discovered';
      }
      lastPlanSignature = '';
      scheduleViewportPlan();
    },
    cleanup,
    diagnoseSelection,
    extractReader,
    cancelReader: (requestId: string, revision: number) => {
      if (!disposed) acceptReaderRequest(requestId, revision, true);
    },
    confirmEpoch,
    kind: 'web',
    profile: rule.id,
    refresh,
    reconnect,
    resetTranslations,
    setMode,
  };
  setMode(mode);
  beginEpoch();
}
