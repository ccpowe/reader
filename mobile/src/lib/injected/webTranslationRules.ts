export type SelectorPatch = {
  add?: string[];
  remove?: string[];
  replace?: string[];
};

export type WebTranslationRuleMatch = {
  host: string;
  pathPrefix?: string;
  excludePathPrefixes?: readonly string[];
  requiredSelectors?: readonly string[];
  absentSelectors?: readonly string[];
};

export type WebTranslationProfilePatch = {
  id: string;
  priority: number;
  matches: WebTranslationRuleMatch[];
  adapter?: 'reddit' | 'x';
  rendering?: { unclamp?: SelectorPatch };
  scope?: {
    mainRoots?: SelectorPatch;
    rootMode?: 'exclusive' | 'priority';
    fallback?: 'readability-then-document' | 'document' | 'stop';
  };
  segmentation?: {
    exclude?: SelectorPatch;
    stayOriginal?: SelectorPatch;
    forceBlock?: SelectorPatch;
    forceInline?: SelectorPatch;
    atomic?: SelectorPatch;
    preformatted?: SelectorPatch;
    minChars?: number;
  };
};

export type ResolvedWebTranslationRule = {
  id: string;
  priority: number;
  matches: readonly Readonly<WebTranslationRuleMatch>[];
  adapter?: 'reddit' | 'x';
  rendering: { unclamp: readonly string[] };
  scope: {
    mainRoots: readonly string[];
    rootMode: 'exclusive' | 'priority';
    fallback: 'readability-then-document' | 'document' | 'stop';
  };
  segmentation: {
    exclude: readonly string[];
    stayOriginal: readonly string[];
    forceBlock: readonly string[];
    forceInline: readonly string[];
    atomic: readonly string[];
    preformatted: readonly string[];
    minChars: number;
  };
};

const MAX_SELECTORS = 256;
const MAX_SELECTOR_LENGTH = 500;

const defaultRule: ResolvedWebTranslationRule = {
  id: 'generic',
  priority: -100,
  matches: [],
  rendering: { unclamp: [] },
  scope: {
    mainRoots: ['[data-reader-translatable-root]'],
    rootMode: 'exclusive',
    fallback: 'readability-then-document',
  },
  segmentation: {
    exclude: [
      'nav', '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
      'header:not(article header):not(main header):not([role="main"] header)',
      'footer:not(article footer):not(main footer):not([role="main"] footer)',
      'form', 'button', 'input', 'textarea',
      'select', 'option', 'script', 'style', 'noscript', 'template', 'svg', 'canvas',
      'iframe', 'object', 'embed', 'audio', 'video', '[contenteditable="true"]',
      '[translate="no"]', '[hidden]', '[aria-hidden="true"]', '[inert]',
      '.notranslate', '.reader-translation-node', '.reader-source-layout',
    ],
    stayOriginal: ['code', 'kbd', 'math', 'img', 'sup', 'sub'],
    forceBlock: ['br', 'input'],
    forceInline: [],
    atomic: ['pre', 'figure'],
    preformatted: ['pre'],
    minChars: 2,
  },
};

const patches: WebTranslationProfilePatch[] = [
  {
    id: 'youtube', priority: 80, matches: [{ host: 'youtube.com' }],
    scope: { mainRoots: { replace: ['ytd-watch-flexy', 'ytm-watch', '#page-manager'] }, fallback: 'document' },
    segmentation: {
      exclude: { add: ['.html5-video-player', '#player', '#player-container', '.player-container', '.reader-bilingual-caption-host', '[data-reader-caption-control]'] },
      forceBlock: { add: ['yt-attributed-string', 'yt-formatted-string', '.yt-core-attributed-string'] },
    },
  },
  {
    id: 'x', priority: 100, matches: [{ host: 'x.com' }, { host: 'twitter.com' }], adapter: 'x',
    scope: { mainRoots: { replace: ['[data-testid="primaryColumn"]', 'main', '[role="main"]'] }, fallback: 'stop' },
    segmentation: {
      exclude: { add: ['[data-testid="User-Name"]', '[data-testid="socialContext"]', '[data-testid="userFollowIndicator"]', '[data-testid="tweet-text-show-more-link"]', '[data-testid="videoComponent"]', '[data-testid="sidebarColumn"]', '[role="toolbar"]', '[role="group"]', 'time'] },
      forceInline: { add: ['[data-testid="tweetText"] span', '[data-testid="UserDescription"] div'] },
      forceBlock: { add: ['[data-testid="tweetText"] div'] },
    },
    rendering: { unclamp: { add: ['[data-testid="tweetText"]', '[data-testid="card.layoutSmall.detail"]', '[data-testid="card.layoutLarge.detail"]', '[data-testid="twitterArticleReadView"] [style*="line-clamp"]'] } },
  },
  {
    id: 'reddit', priority: 100, matches: [{ host: 'reddit.com' }, { host: 'redd.it' }], adapter: 'reddit',
    scope: { mainRoots: { replace: ['main', '[role="main"]', '#siteTable'] }, fallback: 'stop' },
    segmentation: { exclude: { add: ['[slot="credit-bar"]', '[slot="action-row"]', '[data-testid="comment_author_link"]', 'time'] } },
    rendering: { unclamp: { add: ['[slot="text-body"]', '[slot="comment"]', '.RichTextJSON-root', '[class*="line-clamp"]'] } },
  },
  { id: 'wikipedia', priority: 40, matches: [{ host: 'wikipedia.org', pathPrefix: '/wiki/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['#mw-content-text .mw-parser-output', '#bodyContent'] } }, segmentation: { exclude: { add: ['.navbox', '.reflist', '.mw-editsection', '.metadata', '.catlinks', '.mw-jump-link'] } } },
  { id: 'mdn', priority: 40, matches: [{ host: 'developer.mozilla.org', pathPrefix: '/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['main article', 'main .main-page-content'] } }, segmentation: { exclude: { add: ['.sidebar-container', '.article-footer', '.metadata', '.page-footer'] } } },
  {
    id: 'github', priority: 40, matches: [{ host: 'github.com' }],
    scope: { rootMode: 'priority', mainRoots: { replace: ['.markdown-body', '#repo-content-turbo-frame', 'main'] } },
    segmentation: {
      exclude: { add: ['.blob-code', '.blob-wrapper', '.diff-table', '.js-diff-progressive-container', '.js-suggested-changes-blob', '.react-code-view', '[data-testid="code-viewer"]', '.timeline-comment-header', '.author', '.assignee', '[data-testid^="breadcrumbs"]', '[data-testid="list-row-repo-name-and-number"]', '.react-blob-sticky-header', '.review-thread-reply', '.topic-tag-link', 'a.anchor', 'time'] },
      stayOriginal: { add: ['tt', 'g-emoji', '.issue-link', 'a[data-hovercard-type="user"]'] },
      forceBlock: { add: ['bdi', 'turbo-frame', 'readme-toc'] },
      forceInline: { add: ['g-emoji'] },
    },
    rendering: { unclamp: { add: ['.discussion-title', '.markdown-title', '[class*="TitleHeader"]', '[class*="GridCard-module__description"]', '.TimelineItem-body .Link--primary'] } },
  },
  { id: 'github-docs', priority: 40, matches: [{ host: 'docs.github.com', pathPrefix: '/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['main article', '[data-container="article"]'] } }, segmentation: { exclude: { add: ['.article-footer', '.Sidebar'] } } },
  { id: 'bbc', priority: 40, matches: [{ host: 'bbc.com', pathPrefix: '/news/' }, { host: 'bbc.co.uk', pathPrefix: '/news/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['main article', '[data-component="text-block"]'] } }, segmentation: { exclude: { add: ['[data-component="topic-list"]', '[data-component="related-content"]', '[data-testid="sharing-tools"]'] } } },
  { id: 'react-docs', priority: 40, matches: [{ host: 'react.dev', pathPrefix: '/learn/' }, { host: 'react.dev', pathPrefix: '/reference/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['main article', 'main'] } }, segmentation: { exclude: { add: ['[data-radix-popper-content-wrapper]', '.sandpack', '[class*="sidebar"]'] }, atomic: { add: ['.sandpack'] } } },
  { id: 'stackoverflow', priority: 40, matches: [{ host: 'stackoverflow.com', pathPrefix: '/questions/' }, { host: 'stackoverflow.com', pathPrefix: '/a/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['#mainbar', '#question', '.answer'] } }, segmentation: { exclude: { add: ['#sidebar', '#left-sidebar', '.js-post-menu', '.post-signature', '.comments', '.js-comments-container'] }, atomic: { add: ['.s-prose'] } } },
  { id: 'hacker-news-item', priority: 40, matches: [{ host: 'news.ycombinator.com', pathPrefix: '/item' }], scope: { rootMode: 'priority', mainRoots: { replace: ['#hnmain'] } }, segmentation: { exclude: { add: ['.comhead', '.reply', '.votelinks', '.navs'] }, forceBlock: { add: ['.titleline > a', '.commtext'] }, atomic: { add: ['.commtext'] } } },
  { id: 'devto', priority: 40, matches: [{ host: 'dev.to', pathPrefix: '/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['#article-body', 'article'] } }, segmentation: { exclude: { add: ['#on-page-nav', '.crayons-article__footer', '.crayons-article__sidebar', '.comments'] }, atomic: { add: ['.highlight', '.ltag__link'] } } },
  { id: 'medium', priority: 40, matches: [{ host: 'medium.com', pathPrefix: '/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['article', 'main article'] } }, segmentation: { exclude: { add: ['[data-testid="storySidebar"]', '[data-testid="storyFooter"]', '[data-testid="related-posts"]', '[aria-label="responses"]'] } } },
  { id: 'ars-technica', priority: 40, matches: [{ host: 'arstechnica.com', pathPrefix: '/' }], scope: { rootMode: 'priority', mainRoots: { replace: ['article', '.article-content'] } }, segmentation: { exclude: { add: ['.article-footer', '.related-posts', '.post-meta', '.comments', '.sidebar'] } } },
  { id: 'reader', priority: 10, matches: [{ host: 'reader.local' }], scope: { mainRoots: { replace: ['[data-reader-translatable-root]'] }, fallback: 'stop' } },
];

function validateSelectorList(values: readonly string[], field: string): string[] {
  if (values.length > MAX_SELECTORS) throw new Error(`${field} exceeds selector limit`);
  const unique = new Set<string>();
  for (const raw of values) {
    const value = raw.trim();
    if (!value || value.length > MAX_SELECTOR_LENGTH || /[{};]/.test(value)) {
      throw new Error(`${field} contains an invalid selector`);
    }
    unique.add(value);
  }
  return [...unique];
}

function applySelectorPatch(base: readonly string[], patch: SelectorPatch | undefined, field: string): string[] {
  if (!patch) return [...base];
  if (patch.replace && (patch.add || patch.remove)) throw new Error(`${field} replace cannot be combined with add/remove`);
  const result = patch.replace ? [...patch.replace] : base.filter((value) => !patch.remove?.includes(value));
  result.push(...(patch.add ?? []));
  return validateSelectorList(result, field);
}

function assertKnownKeys(value: object | undefined, allowed: readonly string[], field: string) {
  if (!value) return;
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) throw new Error(`${field} contains unknown field ${unknown[0]}`);
}

export function compileWebTranslationRule(patch: WebTranslationProfilePatch): ResolvedWebTranslationRule {
  assertKnownKeys(patch, ['id', 'priority', 'matches', 'adapter', 'scope', 'segmentation', 'rendering'], 'rule');
  if (patch.rendering !== undefined && (!patch.rendering || typeof patch.rendering !== 'object' || Array.isArray(patch.rendering))) throw new Error('invalid rendering policy');
  assertKnownKeys(patch.rendering, ['unclamp'], 'rendering');
  assertKnownKeys(patch.rendering?.unclamp, ['add', 'remove', 'replace'], 'unclamp');
  if (patch.rendering?.unclamp !== undefined) {
    const unclamp = patch.rendering.unclamp;
    if (!unclamp || typeof unclamp !== 'object' || Array.isArray(unclamp)) throw new Error('invalid unclamp patch');
    for (const values of Object.values(unclamp)) {
      if (!Array.isArray(values) || values.some(value => typeof value !== 'string')) throw new Error('unclamp selectors must be string arrays');
    }
  }
  assertKnownKeys(patch.scope, ['mainRoots', 'rootMode', 'fallback'], 'scope');
  assertKnownKeys(patch.segmentation, ['exclude', 'stayOriginal', 'forceBlock', 'forceInline', 'atomic', 'preformatted', 'minChars'], 'segmentation');
  assertKnownKeys(patch.scope?.mainRoots, ['add', 'remove', 'replace'], 'mainRoots');
  for (const [field, selectorPatch] of Object.entries(patch.segmentation ?? {}).filter(([, value]) => typeof value === 'object')) {
    assertKnownKeys(selectorPatch as SelectorPatch, ['add', 'remove', 'replace'], field);
  }
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(patch.id)) throw new Error('invalid rule id');
  if (!Number.isFinite(patch.priority)) throw new Error('invalid rule priority');
  if (patch.adapter && patch.adapter !== 'reddit' && patch.adapter !== 'x') throw new Error('invalid rule adapter');
  if (patch.scope?.fallback && !['readability-then-document', 'document', 'stop'].includes(patch.scope.fallback)) throw new Error('invalid rule fallback');
  if (patch.scope?.rootMode && !['exclusive', 'priority'].includes(patch.scope.rootMode)) throw new Error('invalid root mode');
  if (patch.adapter && patch.scope?.rootMode === 'priority') throw new Error('social adapter requires exclusive root mode');
  const minChars = patch.segmentation?.minChars ?? defaultRule.segmentation.minChars;
  if (!Number.isSafeInteger(minChars) || minChars < 1 || minChars > 100) throw new Error('invalid minChars');
  if (!Array.isArray(patch.matches) || patch.matches.length > 32) throw new Error('invalid rule matches');
  const matches = patch.matches.map((match) => {
    if (!match || typeof match !== 'object' || Array.isArray(match)) throw new Error('invalid rule match');
    assertKnownKeys(match, ['host', 'pathPrefix', 'excludePathPrefixes', 'requiredSelectors', 'absentSelectors'], 'match');
    if (typeof match.host !== 'string') throw new Error('invalid rule host');
    const host = match.host.trim().toLowerCase().replace(/\.$/, '');
    if (!host || !/^[a-z0-9.-]+$/.test(host)) throw new Error('invalid rule host');
    const validPath = (value: unknown): value is string => typeof value === 'string' && value.startsWith('/') && value.length <= 500 && !/[?#\s]/.test(value);
    if (match.pathPrefix !== undefined && !validPath(match.pathPrefix)) throw new Error('invalid rule pathPrefix');
    const excludePathPrefixes = match.excludePathPrefixes;
    if (excludePathPrefixes !== undefined && (!Array.isArray(excludePathPrefixes) || excludePathPrefixes.length > 16 || !excludePathPrefixes.every(validPath))) throw new Error('invalid excluded paths');
    const conditions = (values: readonly string[] | undefined) => {
      if (values === undefined) return undefined;
      if (!Array.isArray(values) || values.length > 8 || values.some(value => typeof value !== 'string' || !isConditionSelector(value))) throw new Error('invalid condition selectors');
      return [...new Set(values.map(value => value.trim()))];
    };
    const requiredSelectors = conditions(match.requiredSelectors);
    const absentSelectors = conditions(match.absentSelectors);
    return { host, ...(match.pathPrefix !== undefined ? { pathPrefix: match.pathPrefix } : {}),
      ...(excludePathPrefixes !== undefined ? { excludePathPrefixes: [...new Set(excludePathPrefixes)] } : {}),
      ...(requiredSelectors !== undefined ? { requiredSelectors } : {}),
      ...(absentSelectors !== undefined ? { absentSelectors } : {}),
    };
  });
  return {
    id: patch.id,
    priority: patch.priority,
    matches,
    ...(patch.adapter ? { adapter: patch.adapter } : {}),
    rendering: { unclamp: applySelectorPatch(defaultRule.rendering.unclamp, patch.rendering?.unclamp, 'unclamp') },
    scope: {
      mainRoots: applySelectorPatch(defaultRule.scope.mainRoots, patch.scope?.mainRoots, 'mainRoots'),
      rootMode: patch.scope?.rootMode ?? defaultRule.scope.rootMode,
      fallback: patch.scope?.fallback ?? defaultRule.scope.fallback,
    },
    segmentation: {
      exclude: applySelectorPatch(defaultRule.segmentation.exclude, patch.segmentation?.exclude, 'exclude'),
      stayOriginal: applySelectorPatch(defaultRule.segmentation.stayOriginal, patch.segmentation?.stayOriginal, 'stayOriginal'),
      forceBlock: applySelectorPatch(defaultRule.segmentation.forceBlock, patch.segmentation?.forceBlock, 'forceBlock'),
      forceInline: applySelectorPatch(defaultRule.segmentation.forceInline, patch.segmentation?.forceInline, 'forceInline'),
      atomic: applySelectorPatch(defaultRule.segmentation.atomic, patch.segmentation?.atomic, 'atomic'),
      preformatted: applySelectorPatch(defaultRule.segmentation.preformatted, patch.segmentation?.preformatted, 'preformatted'),
      minChars,
    },
  };
}

function freezeRule(rule: ResolvedWebTranslationRule): ResolvedWebTranslationRule {
  rule.matches.forEach(match => {
    if (match.excludePathPrefixes) Object.freeze(match.excludePathPrefixes);
    if (match.requiredSelectors) Object.freeze(match.requiredSelectors);
    if (match.absentSelectors) Object.freeze(match.absentSelectors);
    Object.freeze(match);
  });
  Object.freeze(rule.matches);
  Object.values(rule.segmentation).forEach((value) => { if (Array.isArray(value)) Object.freeze(value); });
  Object.freeze(rule.rendering.unclamp);
  Object.freeze(rule.rendering);
  Object.freeze(rule.scope.mainRoots);
  Object.freeze(rule.scope);
  Object.freeze(rule.segmentation);
  return Object.freeze(rule);
}

export const WEB_TRANSLATION_RULE_REGISTRY: readonly ResolvedWebTranslationRule[] = Object.freeze([
  ...patches.map(compileWebTranslationRule),
  defaultRule,
].map(freezeRule));

function normalizedHost(host: string) { return host.trim().toLowerCase().replace(/\.$/, ''); }
function matchesHost(host: string, expected: string) { return host === expected || host.endsWith(`.${expected}`); }

// Conditions intentionally accept only small, static CSS selectors. No pseudo
// classes, combinators, selector lists or escaped/functional syntax. A single
// compound cannot accidentally use a Reader source wrapper as an ancestor.
function isConditionSelector(value: string): boolean {
  if (!value.trim() || value.length > 160 || /reader-/i.test(value)) return false;
  const atom = '(?:[a-zA-Z][a-zA-Z0-9_-]*|[.#][a-zA-Z_][a-zA-Z0-9_-]*|\\[[a-zA-Z_][a-zA-Z0-9_-]*(?:=(?:"[a-zA-Z0-9_ -]+"|\'[a-zA-Z0-9_ -]+\'|[a-zA-Z0-9_-]+))?\\])';
  const compound = `(?:${atom})+`;
  return new RegExp(`^${compound}$`).test(value.trim());
}

export function selectWebTranslationRule(url: string, registry = WEB_TRANSLATION_RULE_REGISTRY, root?: Pick<ParentNode, 'querySelector'>): ResolvedWebTranslationRule {
  const fallback = registry.find(rule => rule.id === 'generic') ?? defaultRule;
  let parsed: URL;
  try { parsed = new URL(url); } catch { return fallback; }
  const host = normalizedHost(parsed.hostname);
  const candidates = registry.flatMap(rule => rule.matches
    .filter(match => matchesHost(host, match.host) && (!match.pathPrefix || parsed.pathname.startsWith(match.pathPrefix)) &&
      !match.excludePathPrefixes?.some(path => parsed.pathname.startsWith(path)))
    .map(match => ({ match, rule })))
    .sort((left, right) => right.rule.priority - left.rule.priority || (right.match.pathPrefix?.length ?? 0) - (left.match.pathPrefix?.length ?? 0));
  const results = new Map<string, boolean | null>();
  let queries = 0;
  const present = (selector: string): boolean | null => {
    if (!root || !isConditionSelector(selector)) return null;
    if (results.has(selector)) return results.get(selector)!;
    if (queries >= 64) return null;
    queries++;
    let result: boolean | null;
    try { result = Boolean(root.querySelector(`${selector}:not(.reader-translation-node):not(.reader-translation-node *):not(.reader-source-layout):not(#reader-translation-style)`)); } catch { result = null; }
    results.set(selector, result);
    return result;
  };
  for (const { rule, match } of candidates) {
    if ((match.requiredSelectors ?? []).every(selector => present(selector) === true) &&
        (match.absentSelectors ?? []).every(selector => present(selector) === false)) return rule;
  }
  return fallback;
}

export function isSpecialRuleAllowed(url: string, ruleId: string): boolean {
  const rule = WEB_TRANSLATION_RULE_REGISTRY.find((candidate) => candidate.id === ruleId);
  return Boolean(rule?.adapter && selectWebTranslationRule(url).id === ruleId);
}
