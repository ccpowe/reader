/** Split transport payloads, never source DOM. Each part is independently valid rich-text-v1. */
export type WebTranslationPart = { text: string; joiner: string };
const tokenPattern = /⟪READER_(?:(OPEN|CLOSE)_)?(\d+)⟫/g;
export function splitWebTranslationPayload(payload: string, limit = 7500, maxParts = 128): WebTranslationPart[] | null {
  if (payload.length <= limit) return [{ text: payload, joiner: '' }];
  const parts: WebTranslationPart[] = [];
  const stack: string[] = [];
  let current = '', trailing = '', cursor = 0;
  const closing = () => [...stack].reverse().map(id => `⟪READER_CLOSE_${id}⟫`).join('');
  const opening = () => stack.map(id => `⟪READER_OPEN_${id}⟫`).join('');
  const flush = () => {
    const text = current + closing();
    if (!text || parts.length >= maxParts) return false;
    parts.push({ text, joiner: trailing + (text.match(/^\s+/)?.[0] ?? '') });
    trailing = text.match(/\s+$/)?.[0] ?? '';
    current = opening();
    return true;
  };
  const appendText = (value: string) => {
    while (value) {
      const room = limit - current.length - closing().length;
      if (room < 2) { if (!flush()) return false; continue; }
      if (value.length <= room) { current += value; return true; }
      let cut = room;
      // Prefer a word boundary without creating a succession of tiny parts.
      const boundary = value.slice(0, room).search(/\s+\S*$/);
      if (boundary > room / 2) cut = boundary + 1;
      // A UTF-16 surrogate pair must never be divided across requests.
      if (/[\uD800-\uDBFF]/.test(value[cut - 1])) cut--;
      current += value.slice(0, cut); value = value.slice(cut);
      if (!flush()) return false;
    }
    return true;
  };
  for (const match of payload.matchAll(tokenPattern)) {
    if (!appendText(payload.slice(cursor, match.index))) return null;
    const [, kind, id] = match;
    const additionalClose = kind === 'OPEN' ? `⟪READER_CLOSE_${id}⟫`.length : kind === 'CLOSE' ? -match[0].length : 0;
    if (current.length + match[0].length + closing().length + additionalClose > limit && !flush()) return null;
    if (current.length + match[0].length + closing().length + additionalClose > limit) return null;
    current += match[0];
    if (kind === 'OPEN') stack.push(id);
    else if (kind === 'CLOSE' && stack.pop() !== id) return null;
    cursor = match.index! + match[0].length;
  }
  if (!appendText(payload.slice(cursor)) || stack.length || (current && !flush())) return null;
  return parts;
}
