/** Existing DOM anchors are viewport changes; unknown fragments remain routes. */
export function isDocumentAnchorChange(previous: string, next: string, document: Document): boolean {
  try {
    const before = new URL(previous);
    const after = new URL(next);
    if (before.origin !== after.origin || before.pathname !== after.pathname || before.search !== after.search) return false;
    const isAnchor = (hash: string) => {
      if (!hash || /^#(?:\/|!)/.test(hash)) return false;
      const name = decodeURIComponent(hash.slice(1));
      if (/^[\/!]/.test(name)) return false;
      return Boolean(document.getElementById(name)) || [...document.getElementsByName(name)].some(element => element.tagName === 'A');
    };
    if (before.hash && !isAnchor(before.hash)) return false;
    return after.hash ? isAnchor(after.hash) : isAnchor(before.hash);
  } catch { return false; }
}
