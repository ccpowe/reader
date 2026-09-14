import type { Article, FeedItem } from '../lib/api';
import type { WebReaderArticle } from './webReader';
import { i18n } from '../i18n';
import { displayTitle } from '../lib/api';
import { colors, radii } from '../ui/tokens';
import { escapeHtml, formatArticleDate } from './formatting';

const READER_STYLES = `    *{box-sizing:border-box} body{background:${colors.background};color:${colors.textPrimary};font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;margin:0;padding:16px 20px 32px} article{margin:0 auto;max-width:42rem} h1{font-size:28px;font-weight:700;letter-spacing:-.56px;line-height:36px;margin:0 0 16px} .meta{align-items:center;display:flex;justify-content:space-between;margin-bottom:32px}.byline{display:flex;flex-direction:column;gap:4px}.author{font-size:12px;font-weight:600;line-height:16px}.date{color:${colors.textSecondary};font-size:13px;line-height:20px}figure{margin:0 0 32px}figure img{background:${colors.imagePlaceholder};border-radius:${radii.md}px;display:block;height:auto;max-width:100%;width:100%}.content{font-size:17px;line-height:28px}.post-content{font-size:19px;line-height:30px;margin-bottom:24px;overflow-wrap:anywhere;white-space:normal}.content>*:first-child{margin-top:0}.content p{margin:0 0 16px}.content h2,.content h3{font-size:22px;line-height:30px;margin:32px 0 12px}.content img{border-radius:${radii.md}px;height:auto;max-width:100%}.content a{color:${colors.accent}}.content pre{background:${colors.surfaceMuted};border-radius:${radii.sm}px;overflow:auto;padding:16px;white-space:pre-wrap}.content blockquote{border-left:4px solid ${colors.textStrong};margin:28px 0;padding:12px 0 12px 16px}.content blockquote p{font-size:18px;font-style:italic;font-weight:600;line-height:24px;margin:0}.content iframe{max-width:100%}`;

export function hasReadableBody(article: Pick<Article, 'body_html' | 'body_text'>): boolean {
  if (article.body_text?.replace(/[\s\u200b\ufeff]/g, '')) return true;
  const html = article.body_html ?? '';
  if (/<(?:img|video|audio|iframe)\b[^>]*\bsrc\s*=/i.test(html)) return true;
  return Boolean(html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<[^>]*>/g, '')
    .replace(/&(?:nbsp|#160|#x0*a0);/gi, '')
    .replace(/[\s\u200b\ufeff]/g, ''));
}

export function buildReaderHtml(article: Article, item: FeedItem, _saved?: boolean): string {
  const isPost = item.source_kind === 'x';
  const interfaceLocale = escapeHtml(i18n.resolvedLanguage ?? i18n.language);
  const body = hasReadableBody({ body_html: article.body_html, body_text: null })
    ? article.body_html
    : article.body_text?.trim()
      ? `<p>${escapeHtml(article.body_text.trim())}</p>`
      : `<p data-reader-empty-body translate="no" lang="${interfaceLocale}">${escapeHtml(i18n.t('reader:emptyBody'))}</p>`;
  const sourceAuthor = article.author_name ?? item.source_name;
  const author = escapeHtml(sourceAuthor ?? i18n.t('reader:fallbackSource'));
  const publishedAt = article.published_at ?? item.published_at ?? item.fetched_at;
  const hero = item.thumbnail_url ? `<figure><img alt="" src="${escapeHtml(item.thumbnail_url)}"></figure>` : '';
  const heading = isPost ? '' : `<h1>${escapeHtml(displayTitle(article))}</h1>`;
  const content = isPost
    ? `<section class="content post-content" data-reader-translatable-root>${body}</section>${hero}`
    : `${hero}<section class="content" data-reader-translatable-root>${body}</section>`;

  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><base href="${escapeHtml(article.external_url)}"><style>
${READER_STYLES}
  </style></head><body><article>${heading}<div class="meta"><div class="byline"><span class="author"${sourceAuthor == null ? ` data-reader-fallback-author lang="${interfaceLocale}"` : ''}>${author}</span><span class="date" data-reader-date lang="${interfaceLocale}">${escapeHtml(formatArticleDate(publishedAt))}</span></div></div>${content}</article></body></html>`;
}

/** Update Reader-owned shell copy without replacing the document or its source content. */
export function createReaderInterfaceScript(publishedAt: string): string {
  const messages = JSON.stringify({
    locale: i18n.resolvedLanguage ?? i18n.language,
    date: formatArticleDate(publishedAt),
    emptyBody: i18n.t('reader:emptyBody'),
    author: i18n.t('reader:fallbackSource'),
  }).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  return `(function(messages){
    var date = document.querySelector('body > article > .meta > .byline > [data-reader-date]');
    if (date) { date.textContent = messages.date; date.lang = messages.locale; }
    var author = document.querySelector('body > article > .meta > .byline > [data-reader-fallback-author]');
    if (author) { author.textContent = messages.author; author.lang = messages.locale; }
    var empty = document.querySelector('body > article > .content > [data-reader-empty-body]');
    if (empty) { empty.textContent = messages.emptyBody; empty.lang = messages.locale; }
  })(${messages});true;`;
}

/** Empty trusted shell: candidate HTML is data, mounted only after DOMPurify. */
export function buildExtractedReaderHtml(article: WebReaderArticle): string {
  const lang = article.lang ? ` lang="${escapeHtml(article.lang)}"` : '';
  const author = article.byline ? `<div class="meta"><div class="byline"><span class="author">${escapeHtml(article.byline)}</span></div></div>` : '';
  return `<!doctype html><html${lang}><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; img-src http: https:; style-src 'unsafe-inline'; base-uri http: https:; form-action 'none'; frame-src 'none'"><base href="${escapeHtml(article.url)}"><style>${READER_STYLES}</style></head><body><article><h1>${escapeHtml(article.title)}</h1>${author}<section class="content" data-reader-translatable-root></section></article></body></html>`;
}
