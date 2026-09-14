import { i18n } from '../i18n';
import type { SourceListItem } from '../lib/api';

export type AddableSourceKind = 'rss' | 'web' | 'reddit' | 'youtube' | 'x';
export type SourceIconName = 'rss' | 'reddit' | 'youtube' | 'twitter' | 'web';

export function sourceHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

export function sourceLocationLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, '');
    let pathname = parsed.pathname;
    try {
      pathname = decodeURIComponent(pathname);
    } catch {
      // Keep the encoded path when an upstream URL contains malformed escapes.
    }
    const normalizedPath = pathname === '/' ? '' : pathname.replace(/\/+$/, '');
    return `${host}${normalizedPath}`;
  } catch {
    return url;
  }
}

export function redditCommunityFromSource(source: Pick<SourceListItem, 'canonical_url'>): string | null {
  const match = source.canonical_url.match(/^https:\/\/www\.reddit\.com\/r\/([A-Za-z0-9_]{2,21})\//i);
  return match?.[1] ?? null;
}

export function redditCommunitiesFromSources(sources: readonly SourceListItem[]): string[] {
  return Array.from(new Set(
    sources
      .filter((source) => source.kind === 'reddit')
      .map(redditCommunityFromSource)
      .filter((community): community is string => community !== null),
  ));
}

export function displaySourceLabel(kind: string, label: string): string {
  return kind === 'reddit'
    ? label.replace(/\s*·\s*(hot|top|rising)(?:\s*·\s*(day|week|month|year))?/gi, '')
    : label;
}

export function sourceDisplayName(source: Pick<SourceListItem, 'kind' | 'display_name' | 'canonical_url'>): string {
  return displaySourceLabel(source.kind, source.display_name ?? sourceLocationLabel(source.canonical_url));
}

export function sourceSecondaryLabel(source: Pick<SourceListItem, 'kind' | 'display_name' | 'canonical_url'>): string | null {
  const location = sourceLocationLabel(source.canonical_url);
  return sourceDisplayName(source) === location ? null : location;
}

export function sourceHealthLabel(item: Pick<
  SourceListItem,
  'backlog_cycles' | 'gap_detected' | 'last_error_code' | 'status' | 'sync_phase'
>): string {
  if (item.status === 'paused') return i18n.t('errors:sourcePaused');
  if (['web_challenge_required', 'web_rss_challenge_required', 'rss_challenge_required'].includes(item.last_error_code ?? '')) {
    return i18n.t('errors:sourceBrowserVerification');
  }
  if (['web_http_403', 'web_rss_http_403', 'rss_http_403'].includes(item.last_error_code ?? '')) {
    return i18n.t('errors:sourceAccessDenied');
  }
  if (['web_http_401', 'web_rss_http_401', 'rss_http_401'].includes(item.last_error_code ?? '')) {
    return i18n.t('errors:sourceAuthenticationRequired');
  }
  if (item.last_error_code === 'web_feed_discovering') return i18n.t('errors:sourceFeedDiscovering');
  if (item.last_error_code === 'web_feed_discovery_retry') return i18n.t('errors:sourceFeedDiscoveryRetry');
  if (item.last_error_code === 'web_rule_authoring') return i18n.t('errors:sourceRuleAuthoring');
  if (item.last_error_code === 'web_rule_repairing') return i18n.t('errors:sourceRuleRepairing');
  if (item.last_error_code === 'web_rule_authoring_failed') return i18n.t('errors:sourceRuleAuthoringFailed');
  if (item.last_error_code === 'web_rule_agent_unavailable') return i18n.t('errors:sourceRuleAgentUnavailable');
  if (item.sync_phase === 'degraded') {
    if (item.gap_detected) return i18n.t('errors:sourceHistoryGap');
    if (item.last_error_code === 'web_rule_partial_parse') return i18n.t('errors:sourcePartialParsing');
    if (item.last_error_code === 'youtube_data_api_unavailable') return i18n.t('errors:sourceYouTubeQuota');
    if (item.last_error_code === 'auth_failed') return i18n.t('errors:sourceXAuthFailed');
    if (item.last_error_code === 'manifest_failed') return i18n.t('errors:sourceXManifestFailed');
    if (item.last_error_code === 'robots_disallowed') return i18n.t('errors:sourceRobotsDisallowed');
    return i18n.t('errors:sourceDegraded');
  }
  if (item.status === 'pending') return i18n.t('errors:sourcePending');
  if (item.sync_phase === 'catching_up') {
    return item.backlog_cycles >= 3 ? i18n.t('errors:sourceCatchingUpBacklog') : i18n.t('errors:sourceCatchingUp');
  }
  return i18n.t('errors:sourceHealthy');
}

export function sourceStatusRefreshInterval(items: readonly Pick<SourceListItem, 'status' | 'last_error_code'>[]): number | false {
  if (!items.length) return false;
  const busy = items.some((item) => item.status !== 'paused' && (
    (item.status === 'pending' && !item.last_error_code)
    || item.last_error_code === 'web_feed_discovering'
    || item.last_error_code === 'web_rule_authoring'
    || item.last_error_code === 'web_rule_repairing'
  ));
  // Failures and scheduled backlog scans may last hours. Keep them current
  // without treating them as continuously running short operations.
  return busy ? 3_000 : 30_000;
}

export function sourcePlaceholder(kind: AddableSourceKind): string {
  if (kind === 'rss') return 'https://example.com/feed.xml';
  if (kind === 'web') return 'https://example.com/blog';
  if (kind === 'reddit') return 'r/LocalLLaMA';
  if (kind === 'youtube') return i18n.t('errors:youtubePlaceholder');
  return '@OpenAI';
}

/** Icon names are intentionally a dependency-free subset of MaterialCommunityIcons. */
export function sourceIcon(kind: string): SourceIconName {
  if (kind === 'rss') return 'rss';
  if (kind === 'reddit') return 'reddit';
  if (kind === 'youtube') return 'youtube';
  if (kind === 'x') return 'twitter';
  return 'web';
}
