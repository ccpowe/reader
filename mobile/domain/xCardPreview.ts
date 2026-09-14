/** Timeline previews never affect the stored full text. */
export function xCardSnippet(value: string | null | undefined, limit: number): string {
  const text = (value ?? '').replace(/\s+/g, ' ').trim();
  const segmenter = typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null;
  const characters = segmenter
    ? Array.from(segmenter.segment(text), (part) => part.segment)
    : Array.from(text);
  return characters.length > limit ? `${characters.slice(0, limit).join('')}…` : text;
}
