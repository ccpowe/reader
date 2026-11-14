/**
 * 订阅's pure logic: a subscription as a row (name, address, health line, P17 / P23 R4-B), the
 * folders they are grouped in, search, and the add form's input rules. Pure: runs under `node --test`.
 */
import type { SubscriptionResponse } from './api/schema.gen.ts';
import { folderLabel, folderList, relativeTime, sourceKind, type CardSourceKind } from './feed.ts';
import { t } from './i18n/index.ts';

export type SubscriptionHealth =
  | { kind: 'ok' }
  | { kind: 'waiting' }
  | { kind: 'paused' }
  | { kind: 'failing'; reason: string; since: string }
  | { kind: 'gap'; detail: string };

export type SubscriptionItem = {
  id: string;
  name: string;
  kind: CardSourceKind;
  /** the feed / channel / account address, shown without the scheme */
  address: string;
  newCount: number;
  health: SubscriptionHealth;
  avatarUrl?: string | null;
  includeInHome: boolean;
};

/** Why the last scan failed, from the server's `last_error_code` (`<provider>_http_429`, `unsafe_url` …). */
export function scanErrorReason(code: string): string {
  const r = t().subs.reasons;
  const status = /_http_(\d{3})$/.exec(code)?.[1];
  if (status === '429' || code === 'rate_limited') return r.rateLimited;
  if (status === '404' || status === '410') return r.gone;
  if (status === '401' || status === '403') return r.forbidden;
  if (status?.startsWith('5')) return r.serverError;
  if (status) return r.status(status);
  if (code.endsWith('_challenge_required')) return r.challenge;
  if (code.endsWith('_timeout') || code === 'scan_timeout') return r.slow;
  if (code.endsWith('_network_error') || code === 'dns_error') return r.unreachable;
  if (code.endsWith('_not_configured')) return r.notConfigured;
  if (code.endsWith('_invalid_response') || code === 'invalid_feed') return r.unreadable;
  if (code === 'unsafe_url') return r.unsafe;
  if (code === 'response_too_large') return r.tooLarge;
  if (code === 'protected_account') return r.protectedAccount;
  if (code === 'access_blocked') return r.blocked;
  if (code === 'auth_failed') return r.authFailed;
  if (code === 'user_not_found') return r.noAccount;
  if (code === 'manifest_failed') return r.xService;
  if (code === 'youtube_channel_invalid' || code === 'youtube_channel_unavailable') return r.channel;
  if (code === 'youtube_quota_exceeded') return r.quota;
  return r.other;
}

export function healthOf(
  s: Pick<SubscriptionResponse, 'status' | 'is_enabled' | 'last_error_code' | 'last_success_at' | 'may_have_gap'>,
  now: number,
): SubscriptionHealth {
  if (!s.is_enabled || s.status === 'paused') return { kind: 'paused' };
  if (s.last_error_code)
    return {
      kind: 'failing',
      reason: scanErrorReason(s.last_error_code),
      since: s.last_success_at ? t().subs.health.succeededAgo(relativeTime(s.last_success_at, now)) : t().subs.health.neverSucceeded,
    };
  if (s.status === 'pending' && !s.last_success_at) return { kind: 'waiting' };
  if (s.may_have_gap) return { kind: 'gap', detail: t().subs.health.gapDetail };
  return { kind: 'ok' };
}

/** Problems a folder card counts and a row expands: failing and gap. */
export function hasProblem(health: SubscriptionHealth): boolean {
  return health.kind === 'failing' || health.kind === 'gap';
}

export function healthLine(health: SubscriptionHealth): string {
  switch (health.kind) {
    case 'failing':
      return t().subs.health.failing(health.reason, health.since);
    case 'gap':
      return t().subs.health.gap(health.detail);
    case 'waiting':
      return t().subs.health.waiting;
    case 'paused':
      return t().subs.health.paused;
    case 'ok':
      return t().subs.health.ok;
  }
}

export function displayAddress(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');
}

export function toItem(
  s: SubscriptionResponse,
  now: number,
  absolute: (url: string | null | undefined) => string | null,
): SubscriptionItem {
  return {
    id: s.id,
    name: s.name?.trim() || displayAddress(s.url),
    kind: sourceKind(s.type),
    address: displayAddress(s.url),
    newCount: s.new_count,
    health: healthOf(s, now),
    avatarUrl: absolute(s.icon_url),
    includeInHome: s.include_in_home,
  };
}

export type Folder = { key: string; name: string; items: SubscriptionItem[] };

/** Folders in the Today tab order (folderList), each with its rows in name order. */
export function foldersOf(list: readonly SubscriptionResponse[], toRow: (s: SubscriptionResponse) => SubscriptionItem): Folder[] {
  return folderList(list).map((group) => ({
    key: group,
    name: folderLabel(group),
    items: list
      .filter((s) => (s.group?.trim() || '') === group)
      .map(toRow)
      .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
  }));
}

/** Search across name, address and folder name, case-insensitive. */
export function searchFolders(folders: readonly Folder[], query: string): SubscriptionItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return folders.flatMap((folder) =>
    folder.items.filter((item) => `${item.name} ${item.address} ${folder.name}`.toLowerCase().includes(needle)),
  );
}

const TYPE_KEYS = ['rss', 'website', 'youtube', 'x', 'reddit'] as const;
export type SubscriptionType = (typeof TYPE_KEYS)[number];

/** The add form's types: label and address hint in the interface language. */
export const SUBSCRIPTION_TYPES: readonly { key: SubscriptionType; label: string; placeholder: string }[] = TYPE_KEYS.map((key) => ({
  key,
  get label() {
    return t().subs.types[key] ?? key;
  },
  get placeholder() {
    return t().subs.placeholders[key] ?? '';
  },
}));

/** A folder name as typed: trimmed, at most 120 characters (API), "未分类" means none. Null = none. */
export function folderInput(text: string): string | null {
  const name = text.trim().slice(0, 120);
  return name && name !== folderLabel('') ? name : null;
}
