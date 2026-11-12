/**
 * 订阅's pure logic: a subscription as a row (name, address, health line, P17 / P23 R4-B), the
 * folders they are grouped in, search, and the add form's input rules. Pure: runs under `node --test`.
 */
import type { SubscriptionResponse } from './api/schema.gen.ts';
import { folderLabel, folderList, relativeTime, sourceKind, type CardSourceKind } from './feed.ts';

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
  const status = /_http_(\d{3})$/.exec(code)?.[1];
  if (status === '429' || code === 'rate_limited') return '被对方限流';
  if (status === '404' || status === '410') return '地址已经不存在';
  if (status === '401' || status === '403') return '对方拒绝访问';
  if (status?.startsWith('5')) return '对方服务器出错';
  if (status) return `对方返回错误 ${status}`;
  if (code.endsWith('_challenge_required')) return '对方要求人机验证';
  if (code.endsWith('_timeout') || code === 'scan_timeout') return '对方响应太慢';
  if (code.endsWith('_network_error') || code === 'dns_error') return '连不上对方';
  if (code.endsWith('_not_configured')) return '服务器没有配置这类来源';
  if (code.endsWith('_invalid_response') || code === 'invalid_feed') return '内容无法识别';
  if (code === 'unsafe_url') return '地址不安全';
  if (code === 'response_too_large') return '内容太大';
  if (code === 'protected_account') return '账号受保护';
  if (code === 'access_blocked') return '对方屏蔽了服务器';
  if (code === 'auth_failed') return '登录凭据失效';
  if (code === 'user_not_found') return '账号不存在';
  if (code === 'manifest_failed') return 'X 服务出错';
  if (code === 'youtube_channel_invalid' || code === 'youtube_channel_unavailable') return '频道不存在或不可用';
  if (code === 'youtube_quota_exceeded') return 'YouTube 配额用完了';
  return '暂时读不到';
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
      since: s.last_success_at ? `${relativeTime(s.last_success_at, now)}成功过` : '还没成功过',
    };
  if (s.status === 'pending' && !s.last_success_at) return { kind: 'waiting' };
  if (s.may_have_gap) return { kind: 'gap', detail: '上次扫描时新内容太多，更早的没取到' };
  return { kind: 'ok' };
}

/** Problems a folder card counts and a row expands: failing and gap. */
export function hasProblem(health: SubscriptionHealth): boolean {
  return health.kind === 'failing' || health.kind === 'gap';
}

export function healthLine(health: SubscriptionHealth): string {
  switch (health.kind) {
    case 'failing':
      return `扫描失败 · ${health.reason} · ${health.since}`;
    case 'gap':
      return `可能有遗漏 · ${health.detail}`;
    case 'waiting':
      return '等待首次扫描';
    case 'paused':
      return '已暂停';
    case 'ok':
      return '同步正常';
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

export const SUBSCRIPTION_TYPES = [
  { key: 'rss', label: 'RSS', placeholder: 'https://example.com/feed.xml' },
  { key: 'website', label: '网站', placeholder: 'https://example.com' },
  { key: 'youtube', label: 'YouTube', placeholder: '频道链接或 @handle' },
  { key: 'x', label: 'X', placeholder: '@handle 或 x.com 链接' },
  { key: 'reddit', label: 'Reddit', placeholder: 'r/python' },
] as const;
export type SubscriptionType = (typeof SUBSCRIPTION_TYPES)[number]['key'];

/** A folder name as typed: trimmed, at most 120 characters (API), "未分类" means none. Null = none. */
export function folderInput(text: string): string | null {
  const name = text.trim().slice(0, 120);
  return name && name !== folderLabel('') ? name : null;
}
