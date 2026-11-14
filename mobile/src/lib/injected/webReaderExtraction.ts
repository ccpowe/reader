import { Readability } from '@mozilla/readability';
import DOMPurify from 'dompurify';
import { MAX_WEB_READER_BYTES, utf8ByteLength, type WebReaderArticle } from './webReader';

const MARKER = 'data-reader-extraction-node';
const ALLOWED_TAGS = ['p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'br', 'hr', 'a', 'img', 'figure', 'figcaption', 'strong', 'b', 'em', 'i', 'u', 's', 'del',
  'span', 'sub', 'sup', 'blockquote', 'pre', 'code', 'kbd', 'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'time'];
// Every retained attribute participates in snapshot invalidation as well.
export const READER_CONTENT_ATTRIBUTES = ['href', 'src', 'alt', 'title', 'lang', 'dir',
  'colspan', 'rowspan', 'start', 'datetime'];

type ReaderContextKind = 'title' | 'meta' | 'jsonld' | 'heading' | 'byline';
export type WebReaderDependencies = {
  document: Document;
  baseURI: string;
  language: string | null;
  roots: Set<Element>;
  ancestors: Set<Element>;
  context: Map<Element, ReaderContextKind>;
  complete: boolean;
};
// Readability 0.6 reads these attributes on candidates and their ancestors,
// even when those ancestors are absent from the final serialized content.
const ANCESTOR_ATTRIBUTES = new Set(['lang', 'dir', 'style', 'hidden', 'aria-hidden',
  'aria-modal', 'role', 'class', 'id', 'rel', 'itemprop']);
const CONTEXT_SELECTOR_ATTRIBUTES = new Set(['name', 'property', 'content', 'type', 'rel', 'itemprop', 'class', 'id']);
const MAX_CONTEXT_MUTATION_NODES = 2000;
const MAX_CONTEXT_COLLECTION_WORK = 160_000;
const MAX_CONTEXT_ANCESTORS = 256;
type ReaderWorkBudget = { remaining: number };
function spend(budget: ReaderWorkBudget, count = 1): boolean {
  budget.remaining -= count;
  return budget.remaining >= 0;
}
const READER_ONLY_CONTENT = '.reader-translation-node,#reader-translation-style';

function bylineCandidate(element: Element, budget: ReaderWorkBudget): boolean {
  if (!(element.getAttribute('rel') === 'author' || element.getAttribute('itemprop')?.includes('author') ||
      /byline|author|dateline|writtenby|p-author/i.test(`${element.getAttribute('class') ?? ''} ${element.id}`))) return false;
  // Readability requires 1..99 trimmed characters. Check only a bounded prefix;
  // do not materialize the whole body when a site uses an author-page class.
  const walker = element.ownerDocument.createTreeWalker(element, NodeFilter.SHOW_ALL, {
    acceptNode: node => {
      // Count rejected nodes too; nextNode must not hide unbounded sibling work.
      if (!spend(budget)) return NodeFilter.FILTER_ACCEPT;
      return node.nodeType === Node.ELEMENT_NODE && (node as Element).matches(READER_ONLY_CONTENT)
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    },
  });
  let length = 0;
  let trimmedLength = 0;
  let text: Node | null;
  while ((text = walker.nextNode())) {
    if (budget.remaining < 0) return true;
    if (text.nodeType !== Node.TEXT_NODE) continue;
    for (const character of text.nodeValue ?? '') {
      if (!spend(budget)) return true;
      const whitespace = /\s/.test(character);
      if (!length && whitespace) continue;
      length += character.length;
      if (!whitespace) trimmedLength = length;
      if (trimmedLength >= 100) return false;
    }
  }
  return trimmedLength > 0;
}

function readerContextKind(element: Element, budget: ReaderWorkBudget): ReaderContextKind | null {
  if (element.tagName === 'TITLE') return 'title';
  if (element.tagName === 'META' && /(?:^|[\s:._-])(title|author|creator)(?:$|\s)/i.test(
    `${element.getAttribute('name') ?? ''} ${element.getAttribute('property') ?? ''}`)) return 'meta';
  if (element.tagName === 'SCRIPT' && element.getAttribute('type')?.trim().toLowerCase() === 'application/ld+json') return 'jsonld';
  // Heading fallback and bylines can be outside the retained article subtree.
  if (element.tagName === 'H1' || element.tagName === 'H2') return 'heading';
  if (bylineCandidate(element, budget)) return 'byline';
  return null;
}

function collectReaderDependencies(document: Document, roots: Element[], live: Element[]): WebReaderDependencies {
  const context = new Map<Element, ReaderContextKind>();
  const budget = { remaining: MAX_CONTEXT_COLLECTION_WORK };
  for (const element of live) {
    if (!spend(budget)) break;
    const kind = readerContextKind(element, budget);
    if (budget.remaining < 0) break;
    if (!kind) continue;
    let owned = false;
    for (let parent: Element | null = element; parent; parent = parent.parentElement) {
      if (!spend(budget)) break;
      if (parent.matches(READER_ONLY_CONTENT)) { owned = true; break; }
    }
    if (budget.remaining < 0) break;
    if (!owned) context.set(element, kind);
  }
  const ancestors = new Set<Element>();
  collectAncestors:
  for (const node of [...roots, ...context.keys()]) {
    for (let parent: Element | null = node; parent; parent = parent.parentElement) {
      if (!spend(budget)) break collectAncestors;
      if (ancestors.has(parent)) break;
      ancestors.add(parent);
    }
  }
  return { document, baseURI: document.baseURI, language: document.documentElement.getAttribute('lang'),
    roots: new Set(roots), ancestors, context, complete: budget.remaining >= 0 };
}

/** Check extraction inputs, without cloning, hashing or reparsing the article. */
export function readerDependenciesChanged(dependencies: WebReaderDependencies, records: MutationRecord[], isOwned: (node: Node) => boolean, isOwnedRecord: (record: MutationRecord, visit: () => boolean) => boolean): boolean {
  const { document, roots, ancestors, context } = dependencies;
  let checkedDocument = false;
  const budget = { remaining: MAX_CONTEXT_MUTATION_NODES };
  for (const record of records) {
    if (!spend(budget)) return true;
    if (isOwned(record.target)) continue;
    // Bound classification before any helper iterates these NodeLists, even if
    // every changed child is owned. Counts also cover the later removal loop.
    if (!spend(budget, record.addedNodes.length + record.removedNodes.length)) return true;
    const owned = isOwnedRecord(record, () => spend(budget));
    if (budget.remaining < 0) return true;
    if (owned) continue;
    if (!dependencies.complete) return true;
    if (!checkedDocument) {
      if (!document.documentElement || !document.body) return true;
      if (document.baseURI !== dependencies.baseURI || document.documentElement.getAttribute('lang') !== dependencies.language) return true;
      checkedDocument = true;
    }
    for (const removed of record.removedNodes) {
      if (removed.nodeType === Node.ELEMENT_NODE && ancestors.has(removed as Element)) return true;
    }
    const target = record.target.nodeType === Node.ELEMENT_NODE ? record.target as Element : record.target.parentElement;
    if (!target) continue;
    if (record.type === 'attributes' && ancestors.has(target) && ANCESTOR_ATTRIBUTES.has(record.attributeName ?? '')) return true;
    let parent: Element | null = target;
    let depth = 0;
    for (; parent && depth < MAX_CONTEXT_ANCESTORS; parent = parent.parentElement, depth++) {
      if (!spend(budget)) return true;
      if (roots.has(parent)) return true;
      const kind = context.get(parent);
      if (kind === 'heading' || kind === 'byline') return true;
      if (kind === 'title' && record.type !== 'attributes') return true;
      if (kind === 'jsonld' && (record.type !== 'attributes' || record.attributeName === 'type')) return true;
      if (kind === 'meta' && record.type === 'attributes' && ['name', 'property', 'content'].includes(record.attributeName ?? '')) return true;
      if (!kind && bylineCandidate(parent, budget)) return true;
    }
    if (parent) return true;
    // Previously irrelevant nodes may become metadata/byline candidates.
    if (record.type === 'attributes' && CONTEXT_SELECTOR_ATTRIBUTES.has(record.attributeName ?? '') && readerContextKind(target, budget)) return true;
    if (record.type !== 'childList') continue;
    // Inspect only added branches. A budget overrun invalidates once instead of
    // trying to prove an arbitrarily large document update harmless.
    for (const added of record.addedNodes) {
      if (isOwned(added)) continue;
      const walker = document.createTreeWalker(added, NodeFilter.SHOW_ALL, {
        acceptNode: node => {
          if (!spend(budget)) return NodeFilter.FILTER_ACCEPT;
          return isOwned(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        },
      });
      let node: Node | null = added;
      do {
        if (budget.remaining < 0) return true;
        if (node.nodeType === Node.ELEMENT_NODE && readerContextKind(node as Element, budget)) return true;
        node = walker.nextNode();
      } while (node);
    }
  }
  return false;
}

/** The final call is also made inside the app-owned reader, before HTML is mounted. */
export function sanitizeReaderContent(html: string, baseUrl: string): string {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  for (const element of parsed.querySelectorAll('[href],[src]')) {
    for (const name of ['href', 'src']) {
      const value = element.getAttribute(name);
      if (value === null) continue;
      try {
        const url = new URL(value, baseUrl);
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Unsupported URL');
        element.setAttribute(name, url.href);
      } catch { element.removeAttribute(name); }
    }
  }
  // No mutations or third-party processing may follow this safety boundary.
  return DOMPurify.sanitize(parsed.body.innerHTML, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: READER_CONTENT_ATTRIBUTES,
    // These values describe content rather than URLs. The strict URI pattern
    // below must apply only to href/src, not list numbering or language.
    ADD_URI_SAFE_ATTR: READER_CONTENT_ATTRIBUTES.filter(name => name !== 'href' && name !== 'src'),
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: /^https?:\/\//i,
  });
}

/** Count existing nodes/attributes incrementally, without serializing huge HTML. */
export function boundedReaderElements(document: Document): Element[] {
  const elements: Element[] = [];
  let characters = 0;
  let nodes = 0;
  let elementCount = 0;
  // TreeWalker does not enter template.content, but cloneNode(true) copies it.
  // Keep the ordinary document's mapping order while budgeting every fragment.
  const roots: { node: Node; mapped: boolean }[] = [{ node: document, mapped: true }];
  for (let index = 0; index < roots.length; index++) {
    const root = roots[index];
    const walker = document.createTreeWalker(root.node, NodeFilter.SHOW_ALL);
    let node: Node | null;
    nodes++;
    while ((node = walker.nextNode())) {
      nodes++;
      characters += node.nodeValue?.length ?? 0;
      if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as Element;
        elementCount++;
        if (root.mapped) elements.push(element);
        for (const attribute of element.attributes) {
          characters += attribute.name.length + attribute.value.length;
          if (characters > 1_000_000) throw new Error('too_large');
        }
        if (element.tagName === 'TEMPLATE' && 'content' in element) {
          roots.push({ node: (element as HTMLTemplateElement).content, mapped: false });
        }
      }
      if (nodes > 160_000 || elementCount > 80_000 || characters > 1_000_000) throw new Error('too_large');
    }
  }
  return elements;
}

export function extractWebReader(document: Document): { article: WebReaderArticle; roots: Element[]; dependencies: WebReaderDependencies } {
  // Limits apply before cloning; timeout in the native host only abandons a result.
  if (!document.body || !/^https?:\/\//i.test(document.URL)) throw new Error('unavailable');
  const live = boundedReaderElements(document);
  const clone = document.cloneNode(true) as Document;
  clone.querySelectorAll('*').forEach((node, index) => node.setAttribute(MARKER, String(index)));
  clone.querySelectorAll('.reader-translation-node,#reader-translation-style').forEach(node => node.remove());
  clone.querySelectorAll('.reader-source-layout').forEach(node => node.replaceWith(...node.childNodes));
  clone.querySelectorAll('.reader-source-hidden').forEach(node => node.classList.remove('reader-source-hidden'));
  clone.documentElement.classList.remove('reader-translation-translated', 'reader-translation-bilingual', 'reader-translation-original');
  const extracted = new Readability(clone, { charThreshold: 0 }).parse();
  if (!extracted?.content) throw new Error('unavailable');
  const parsed = new DOMParser().parseFromString(extracted.content, 'text/html');
  const candidates = [...parsed.querySelectorAll(`[${MARKER}]`)]
    .map(node => live[Number(node.getAttribute(MARKER))])
    .filter(node => node && node !== document.body && node !== document.documentElement && document.body.contains(node));
  const retained = new Set(candidates);
  const roots = candidates.filter(node => {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      if (retained.has(parent)) return false;
    }
    return true;
  });
  const html = sanitizeReaderContent(extracted.content, document.baseURI);
  const clean = new DOMParser().parseFromString(html, 'text/html');
  const text = clean.body.textContent?.trim() ?? '';
  // Short announcements are valid; a heading/menu alone is not an article.
  if (!text || ![...clean.querySelectorAll('p,pre,blockquote,li,td')].some(node => node.textContent?.trim())) throw new Error('unavailable');
  const article: WebReaderArticle = {
    url: document.URL,
    title: (extracted.title || document.title).slice(0, 2000),
    byline: extracted.byline?.slice(0, 2000) || null,
    lang: extracted.lang && /^[a-zA-Z0-9-]{1,50}$/.test(extracted.lang) ? extracted.lang : null,
    html,
    text,
  };
  if (utf8ByteLength(JSON.stringify(article)) > MAX_WEB_READER_BYTES - 2048) throw new Error('too_large');
  const contentRoots = roots.length ? roots : [document.body];
  return { article, roots: contentRoots, dependencies: collectReaderDependencies(document, contentRoots, live) };
}
