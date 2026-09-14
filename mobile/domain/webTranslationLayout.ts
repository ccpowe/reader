/** Restricted, reversible layout leases. No translation/network or document-wide observer. */
export type WebTranslationLayoutPolicy = { selectors: readonly string[]; maxAncestors?: number; naturalFlow?: boolean };
export type WebTranslationLayoutAssessment = {
  status: 'disabled' | 'unmatched' | 'unchanged' | 'applied' | 'shared' | 'unsafe' | 'interactive' | 'budget_exceeded' | 'invalid_selector';
  container?: HTMLElement;
};
type SavedStyle = { value: string; priority: string; appliedValue: string; appliedPriority: string; owned: boolean };
type Transaction = { element: HTMLElement; owners: Set<string>; styles: Map<string, SavedStyle>; observer: MutationObserver; naturalFlow: boolean };
const controls = 'button,summary,input,select,textarea,[role="button"],[aria-expanded],[aria-controls]';
const semanticBoundary = 'nav,form,dialog,details,table,[role="dialog"],[role="grid"],[contenteditable="true"]';

function layoutParent(element: HTMLElement): HTMLElement | null {
  if (element.assignedSlot) return element.assignedSlot;
  if (element.parentElement) return element.parentElement;
  const root = element.getRootNode();
  return root.nodeType === 11 && 'host' in root ? (root as ShadowRoot).host as HTMLElement : null;
}

function controlsWithin(root: HTMLElement): 'clear' | 'interactive' | 'budget_exceeded' {
  const walker = root.ownerDocument.createTreeWalker(root, 1);
  let current: Node | null = root;
  for (let visited = 0; current && visited < 128; visited += 1, current = walker.nextNode()) {
    if ((current as Element).matches(controls)) return 'interactive';
  }
  return current ? 'budget_exceeded' : 'clear';
}

function nearbyControls(element: HTMLElement) {
  const own = controlsWithin(element);
  if (own !== 'clear') return own;
  // A button in another card/article is not an expansion control for this
  // paragraph. Check the adjacent controls/wrappers instead of all of main.
  for (const sibling of [element.previousElementSibling, element.nextElementSibling]) {
    if (!sibling || sibling.classList.contains('reader-translation-node')) continue;
    const status = controlsWithin(sibling as HTMLElement);
    if (status !== 'clear') return status;
  }
  return 'clear' as const;
}

export function createWebTranslationLayoutManager(options: {
  /** Synchronous wrapper: runtime may mark/drain these exact style mutations as renderer-owned. */
  ownedWrite?: (element: HTMLElement, write: () => void) => void;
} = {}) {
  const transactions = new Map<HTMLElement, Transaction>();
  const leases = new Map<string, Transaction>();
  // A host that takes control stays in charge for the lifetime of this manager.
  const hostOwned = new WeakSet<HTMLElement>();
  function hasNaturalHeight(element: HTMLElement): boolean {
    // getComputedStyle resolves auto height to pixels. Typed OM retains the
    // computed keyword, so fixed/percentage heights can be rejected without writes.
    // Older engines fail closed for generic discovery; explicit site rules remain available.
    const typed = element as HTMLElement & { computedStyleMap?: () => { get: (property: string) => { toString(): string } | undefined } };
    return typed.computedStyleMap?.().get('height')?.toString() === 'auto'
      && typed.computedStyleMap?.().get('max-height')?.toString() === 'none';
  }
  const write = (element: HTMLElement, action: () => void) => options.ownedWrite ? options.ownedWrite(element, action) : action();

  function observe(transaction: Transaction) {
    transaction.observer.observe(transaction.element, { attributes: true, attributeOldValue: true, attributeFilter: ['style', 'class'] });
  }
  function hostChanges(transaction: Transaction, records: MutationRecord[]) {
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      if (transaction.naturalFlow && (record.attributeName === 'class' || record.attributeName === 'style')) hostOwned.add(transaction.element);
      if (record.attributeName !== 'style') continue;
      const next = records.slice(index + 1).find((item) => item.attributeName === 'style');
      const before = transaction.element.ownerDocument.createElement('span').style;
      const after = transaction.element.ownerDocument.createElement('span').style;
      before.cssText = record.oldValue ?? '';
      after.cssText = next ? next.oldValue ?? '' : transaction.element.getAttribute('style') ?? '';
      for (const [property, saved] of transaction.styles) {
        // Attribute snapshots detect property changes, including change-then-revert sequences.
        // A CSSOM setter that produces no mutation cannot establish observable ownership.
        if (before.getPropertyValue(property) !== after.getPropertyValue(property)
          || before.getPropertyPriority(property) !== after.getPropertyPriority(property)) saved.owned = false;
      }
    }
  }
  function restore(transaction: Transaction) {
    hostChanges(transaction, transaction.observer.takeRecords());
    transaction.observer.disconnect();
    write(transaction.element, () => {
      for (const [property, saved] of transaction.styles) {
        const style = transaction.element.style;
        if (!saved.owned || style.getPropertyValue(property) !== saved.appliedValue || style.getPropertyPriority(property) !== saved.appliedPriority) continue;
        if (saved.value) style.setProperty(property, saved.value, saved.priority);
        else style.removeProperty(property);
      }
    });
    transactions.delete(transaction.element);
    for (const key of transaction.owners) leases.delete(key);
  }
  function release(entityKey: string) {
    const transaction = leases.get(entityKey);
    if (!transaction) return;
    leases.delete(entityKey);
    transaction.owners.delete(entityKey);
    if (!transaction.owners.size) restore(transaction);
  }
  function acquire(entityKey: string, source: HTMLElement, translation: HTMLElement, policy?: WebTranslationLayoutPolicy): WebTranslationLayoutAssessment {
    if (!policy || (!policy.selectors.length && !policy.naturalFlow)) { release(entityKey); return { status: 'disabled' }; }
    const view = source.ownerDocument.defaultView;
    if (!view || !source.isConnected || !translation.isConnected || source.ownerDocument !== translation.ownerDocument) { release(entityKey); return { status: 'unsafe' }; }
    const limit = Math.min(12, Math.max(1, Math.floor(policy.maxAncestors ?? 8)));
    let candidate: HTMLElement | undefined;
    let element: HTMLElement | null = source;
    let reachedBoundary = false;
    const ancestors: HTMLElement[] = [];
    for (let depth = 0; element && depth < limit; depth += 1, element = layoutParent(element)) {
      ancestors.push(element);
      const style = view.getComputedStyle(element);
      const parent = layoutParent(element);
      const parentDisplay = parent ? view.getComputedStyle(parent).display : '';
      if (element.matches(semanticBoundary) || /^(fixed|absolute|sticky)$/.test(style.position)
        || /flex|grid|table/.test(style.display) || /flex|grid/.test(parentDisplay)
        || /auto|scroll/.test(`${style.overflowX} ${style.overflowY}`)
        || style.transform !== 'none' || style.contain !== 'none') { release(entityKey); return { status: 'unsafe' }; }
      if (policy.naturalFlow && !hasNaturalHeight(element)) { release(entityKey); return { status: 'unsafe' }; }
      if (element.matches(controls)) { release(entityKey); return { status: 'interactive' }; }
      // Keep an explicit expansion control in the container or its immediate wrapper in charge.
      if (!candidate) {
        let matches = false;
        try { matches = policy.selectors.some((selector) => element!.matches(selector)); }
        catch { release(entityKey); return { status: 'invalid_selector' }; }
        const passiveClamp = policy.naturalFlow && !hostOwned.has(element)
          && (transactions.has(element) || (Number.parseInt(style.getPropertyValue('-webkit-line-clamp'), 10) > 0
            && element.scrollHeight > element.clientHeight + 1));
        if ((matches || passiveClamp) && element.contains(translation)) {
          if (policy.naturalFlow && hostOwned.has(element)) { release(entityKey); return { status: 'unsafe' }; }
          candidate = element;
          const controlStatus = policy.naturalFlow ? nearbyControls(element) : controlsWithin(parent ?? element);
          if (controlStatus !== 'clear') { release(entityKey); return { status: controlStatus, container: element }; }
        }
      }
      if (element === source.ownerDocument.body || element === source.ownerDocument.documentElement) { reachedBoundary = true; break; }
    }
    if (!reachedBoundary) { release(entityKey); return { status: 'budget_exceeded' }; }
    if (!candidate) { release(entityKey); return { status: 'unmatched' }; }
    const previous = leases.get(entityKey);
    if (previous && previous.element !== candidate) release(entityKey);
    const existing = transactions.get(candidate);
    if (existing) {
      const pending = existing.observer.takeRecords();
      hostChanges(existing, pending);
      if (hostOwned.has(candidate) || pending.some((record) => record.attributeName === 'class') || [...existing.styles.values()].some((saved) => !saved.owned)) {
        restore(existing);
        return { status: 'unsafe', container: candidate };
      }
      existing.owners.add(entityKey); leases.set(entityKey, existing);
      return { status: 'shared', container: candidate };
    }
    const computed = view.getComputedStyle(candidate);
    if (!(Number.parseInt(computed.getPropertyValue('-webkit-line-clamp'), 10) > 0)
      || candidate.scrollHeight <= candidate.clientHeight + 1) return { status: 'unchanged', container: candidate };
    if (computed.maxHeight !== 'none' || !/^(block|-webkit-box|flow-root)$/.test(computed.display)) return { status: 'unsafe', container: candidate };
    const property = '-webkit-line-clamp';
    const saved: SavedStyle = { value: candidate.style.getPropertyValue(property), priority: candidate.style.getPropertyPriority(property), appliedValue: 'none', appliedPriority: 'important', owned: true };
    const transaction: Transaction = {
      element: candidate, naturalFlow: !!policy.naturalFlow, owners: new Set([entityKey]), styles: new Map([[property, saved]]),
      observer: new view.MutationObserver((records) => {
        hostChanges(transaction, records);
        // Class toggles may be a host expansion/collapse control. Restore immediately.
        if (hostOwned.has(transaction.element) || records.some((record) => record.attributeName === 'class')) restore(transaction);
      }),
    };
    write(candidate, () => candidate!.style.setProperty(property, saved.appliedValue, saved.appliedPriority));
    transactions.set(candidate, transaction); leases.set(entityKey, transaction);
    // Never broaden overflow/height. If removing clamp alone cannot expose content, roll back.
    if (ancestors.some((ancestor) => {
      const style = view.getComputedStyle(ancestor);
      const isContainerAncestor = ancestor !== candidate && ancestor.contains(candidate);
      const extendsPastAncestor = isContainerAncestor && candidate!.getBoundingClientRect().bottom > ancestor.getBoundingClientRect().bottom + 1;
      return extendsPastAncestor || (/hidden|clip/.test(style.overflowY) && ancestor.scrollHeight > ancestor.clientHeight + 1);
    })) { restore(transaction); return { status: 'unsafe', container: candidate }; }
    observe(transaction);
    return { status: 'applied', container: candidate };
  }
  return { acquire, release, hasLease(entityKey: string) { return leases.has(entityKey); }, releaseAll() { for (const transaction of [...transactions.values()]) restore(transaction); } };
}
