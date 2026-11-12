/**
 * The reader page's pure logic: which view an article opens in, YouTube URLs, and the reader-mode
 * document (the app's own HTML around the article body). Pure: runs under `node --test`.
 */
import { t } from './i18n/index.ts';

/** The app identity YouTube's embed player wants as origin/referrer (old app: domain/media.ts). */
export const YOUTUBE_APP_REFERER = 'https://com.ccpower.reader';

export function youtubeVideoId(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === 'youtu.be') return parsed.pathname.slice(1) || null;
    if (parsed.pathname.startsWith('/shorts/') || parsed.pathname.startsWith('/embed/')) return parsed.pathname.split('/')[2] || null;
    return parsed.searchParams.get('v');
  } catch {
    return null;
  }
}

export function youtubeEmbedUrl(videoId: string): string {
  const id = videoId.replace(/[^A-Za-z0-9_-]/g, '');
  const parameters = new URLSearchParams({ cc_load_policy: '1', iv_load_policy: '3', origin: YOUTUBE_APP_REFERER, playsinline: '1', rel: '0' });
  return `https://www.youtube.com/embed/${id}?${parameters.toString()}`;
}

export function youtubeWatchUrl(videoId: string): string {
  return `https://www.youtube.com/watch?v=${videoId.replace(/[^A-Za-z0-9_-]/g, '')}`;
}

/**
 * How an article opens:
 * - youtube: the embedded player (later with translated captions), web page on request
 * - social: X and Reddit open their web page; no reader mode (P16)
 * - web: the original page by default, reader mode on request (P13)
 */
export type ReaderKind = { kind: 'youtube'; videoId: string } | { kind: 'social' } | { kind: 'web' };

const SOCIAL_HOSTS = /(^|\.)(x\.com|twitter\.com|reddit\.com|redd\.it)$/i;
const YOUTUBE_HOSTS = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i;

function host(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

/**
 * Decided by the source type, the article kind ("post" / "video") and, for items without a
 * source (saved from a board), the host.
 */
export function readerKind(article: { url: string; kind?: string; source?: { type: string } | null }): ReaderKind {
  const type = article.source?.type;
  if (type === 'youtube' || article.kind === 'video' || YOUTUBE_HOSTS.test(host(article.url))) {
    const videoId = youtubeVideoId(article.url);
    if (videoId) return { kind: 'youtube', videoId };
  }
  if (type === 'x' || type === 'reddit' || article.kind === 'post' || SOCIAL_HOSTS.test(host(article.url))) return { kind: 'social' };
  return { kind: 'web' };
}

/**
 * Only http(s) addresses are opened, shared or loaded. Feed URLs are third-party data and can be
 * javascript:, file:, intent: or an app's own scheme (which would launch that app).
 */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null;
  } catch {
    return null;
  }
}

export function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Whether the server's body has something to read (text or media), not just empty markup. */
export function hasReadableBody(html: string | null | undefined): boolean {
  if (!html) return false;
  if (/<(?:img|video|audio|iframe)\b[^>]*\bsrc\s*=/i.test(html)) return true;
  return Boolean(
    html
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/<[^>]*>/g, '')
      .replace(/&(?:nbsp|#160|#x0*a0);/gi, '')
      .replace(/[\s​﻿]/g, ''),
  );
}

/**
 * Whether the server's body is the article rather than a feed summary: at least this much text.
 * Shorter bodies (most news feeds) get reader mode from the page itself (extracted, block 3b).
 */
export const READER_MIN_CHARS = 400;

export function hasFullBody(html: string | null | undefined): boolean {
  if (!hasReadableBody(html)) return false;
  const text = (html ?? '').replace(/<[^>]*>/g, '').replace(/&[a-z#0-9]+;/gi, ' ').replace(/\s+/g, ' ').trim();
  return text.length >= READER_MIN_CHARS;
}

/** "2026 年 11 月 12 日" */
export function articleDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return t().time.date(date);
}

/** Colors of the reader document: the app's light and dark themes (global.css), P25 scale. */
const css = `
:root{--bg:#fcfbf8;--text:#2c2c2b;--muted:#7d7a75;--line:rgba(42,28,0,.07);--sunken:#f0efed;--link:#1f76cc}
@media (prefers-color-scheme:dark){:root{--bg:#191919;--text:#e6e5e3;--muted:#9b9994;--line:rgba(255,255,255,.07);--sunken:#2c2c2b;--link:#5aa2e8}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,system-ui,sans-serif;margin:0;padding:24px 24px 96px;-webkit-text-size-adjust:100%}
article{margin:0 auto;max-width:42rem}
h1{font-size:28px;font-weight:700;letter-spacing:-.4px;line-height:36px;margin:0 0 12px}
.meta{color:var(--muted);font-size:13px;line-height:20px;margin-bottom:28px}
figure{margin:0 0 28px}figure img{background:var(--sunken);border-radius:8px;display:block;height:auto;width:100%}
.content{font-size:17px;line-height:28px;overflow-wrap:anywhere}
.content>*:first-child{margin-top:0}.content p{margin:0 0 16px}
.content h2,.content h3{font-size:21px;line-height:29px;margin:32px 0 12px}
.content img,.content video{border-radius:8px;height:auto;max-width:100%}
.content a{color:var(--link)}
.content pre{background:var(--sunken);border-radius:6px;overflow:auto;padding:16px;white-space:pre-wrap}
.content code{font-size:15px}
.content blockquote{border-left:3px solid var(--line);color:var(--muted);margin:24px 0;padding:4px 0 4px 16px}
.content table{border-collapse:collapse;display:block;overflow:auto}
.content td,.content th{border:1px solid var(--line);padding:6px 10px}
.content iframe{max-width:100%}`;

/**
 * Reader mode for an article whose body the server has. The body is the server's sanitized HTML;
 * the document also forbids scripts, forms and frames (Content-Security-Policy), and resolves
 * relative links against the article's URL.
 */
export function readerDocument(article: {
  url: string;
  title: string;
  author?: string | null;
  sourceName?: string | null;
  publishedAt?: string | null;
  imageUrl?: string | null;
  /** empty for an article extracted from the page: the runtime mounts it after purifying (web-translation-host) */
  bodyHtml: string;
  lang?: string | null;
}): string {
  const byline = [article.author ?? article.sourceName, article.publishedAt ? articleDate(article.publishedAt) : null].filter(Boolean).join(' · ');
  const hero = article.imageUrl && !article.bodyHtml.includes(article.imageUrl) ? `<figure><img alt="" src="${escapeHtml(article.imageUrl)}"></figure>` : '';
  return (
    `<!doctype html><html${article.lang && /^[a-zA-Z0-9-]{1,50}$/.test(article.lang) ? ` lang="${article.lang}"` : ''}><head><meta charset="utf-8">` +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; img-src http: https: data:; media-src http: https:; style-src 'unsafe-inline'; form-action 'none'; frame-src 'none'">` +
    `<base href="${escapeHtml(article.url)}"><style>${css}</style></head><body><article>` +
    `<h1>${escapeHtml(article.title)}</h1>` +
    (byline ? `<div class="meta">${escapeHtml(byline)}</div>` : '') +
    `${hero}<section class="content" data-reader-translatable-root>${article.bodyHtml}</section>` +
    '</article></body></html>'
  );
}
