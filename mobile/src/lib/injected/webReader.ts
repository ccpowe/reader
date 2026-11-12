/** Local WebView content, never a persisted Reader Article/content identity. */
export type WebReaderArticle = {
  url: string;
  title: string;
  byline: string | null;
  lang: string | null;
  html: string;
  text: string;
};

export type WebDocumentIdentity = { navigationId: string; epoch: string; url: string };
export type WebReaderFailure = 'unavailable' | 'too_large' | 'changed' | 'timeout' | 'cancelled';
export type WebReaderResult =
  | { ok: true; requestId: string; document: WebDocumentIdentity; article: WebReaderArticle }
  | { ok: false; reason: WebReaderFailure };

export const MAX_WEB_READER_BYTES = 512 * 1024;

/** Works in Hermes too, without relying on a global TextEncoder. */
export function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code < 0x80) bytes++;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && index + 1 < value.length &&
      value.charCodeAt(index + 1) >= 0xdc00 && value.charCodeAt(index + 1) <= 0xdfff) {
      bytes += 4;
      index++;
    } else bytes += 3;
  }
  return bytes;
}

export function isWebReaderArticle(value: unknown, url: string): value is WebReaderArticle {
  if (!value || typeof value !== 'object') return false;
  const article = value as Record<string, unknown>;
  return article.url === url && /^https?:\/\//i.test(url) && url.length <= 4096 &&
    typeof article.title === 'string' && article.title.length <= 2000 &&
    (article.byline === null || typeof article.byline === 'string' && article.byline.length <= 2000) &&
    (article.lang === null || typeof article.lang === 'string' && /^[a-zA-Z0-9-]{1,50}$/.test(article.lang)) &&
    typeof article.html === 'string' && article.html.length > 0 && article.html.length <= MAX_WEB_READER_BYTES &&
    typeof article.text === 'string' && Boolean(article.text.trim()) && article.text.length <= MAX_WEB_READER_BYTES;
}
