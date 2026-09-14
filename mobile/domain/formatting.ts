import { i18n } from '../i18n';

/** Formats an ISO timestamp relative to `now`, which can be supplied by tests. */
export function relativeTime(value: string, now = Date.now()): string {
  const elapsed = now - new Date(value).getTime();
  if (!Number.isFinite(elapsed)) return '—';
  const minutes = Math.floor(Math.abs(elapsed) / 60_000);
  if (minutes === 0) return i18n.t('common:justNow');
  const future = elapsed < 0;
  // Hermes provides DateTimeFormat but not RelativeTimeFormat. Bundled plural
  // messages keep these labels available on native and preserve Chinese spacing.
  if (minutes < 60) return i18n.t(future ? 'common:relativeMinuteFuture' : 'common:relativeMinutePast', { count: minutes });
  if (minutes < 1_440) return i18n.t(future ? 'common:relativeHourFuture' : 'common:relativeHourPast', { count: Math.floor(minutes / 60) });
  return i18n.t(future ? 'common:relativeDayFuture' : 'common:relativeDayPast', { count: Math.floor(minutes / 1_440) });
}

export function formatCount(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const locale = i18n.resolvedLanguage ?? i18n.language;
  if (value < 1_000) return new Intl.NumberFormat(locale, { useGrouping: false }).format(value);
  const digits = value >= 10_000 ? 0 : 1;
  return `${new Intl.NumberFormat(locale, { useGrouping: false, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value / 1_000)}k`;
}

export function formatArticleDate(value: string, now = Date.now()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const formatted = new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: locale.startsWith('zh') ? 'long' : 'short', day: 'numeric',
  }).format(date);
  return `${formatted} · ${relativeTime(value, now)}`;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character] ?? character
  ));
}
