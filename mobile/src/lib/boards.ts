/**
 * 榜单's pure logic: which boards and versions exist, what a board request asks for, how an API
 * entry becomes a row, and what the freshness line says (P17). Pure: runs under `node --test`.
 */
import type { BoardEntry, BoardResponse } from './api/schema.gen.ts';
import { relativeTime } from './feed.ts';

export type BoardKey = 'hn' | 'reddit' | 'github' | 'techtwitter' | 'product_hunt';

/** Tab order of the old app. */
export const BOARDS: { key: BoardKey; label: string }[] = [
  { key: 'hn', label: 'Hacker News' },
  { key: 'reddit', label: 'Reddit' },
  { key: 'github', label: 'GitHub' },
  { key: 'techtwitter', label: 'X 技术热门' },
  { key: 'product_hunt', label: 'Product Hunt' },
];

/** The versions a board offers (the 调整 sheet); the first is the server's default. */
export const VARIANTS: Record<BoardKey, { key: string; label: string }[]> = {
  hn: [],
  techtwitter: [],
  github: [
    { key: 'daily', label: '日榜' },
    { key: 'weekly', label: '周榜' },
    { key: 'monthly', label: '月榜' },
  ],
  product_hunt: [
    { key: 'today', label: '今天' },
    { key: 'yesterday', label: '昨天' },
  ],
  reddit: [
    { key: 'hot', label: '热门' },
    { key: 'rising', label: '上升' },
    { key: 'top-day', label: 'Top · 日' },
    { key: 'top-week', label: 'Top · 周' },
    { key: 'top-month', label: 'Top · 月' },
    { key: 'top-year', label: 'Top · 年' },
  ],
};

/** What one board page shows: the board, its version, and for Reddit the community. */
export type BoardView = { board: BoardKey; variant: string; subreddit: string | null };

export function defaultView(board: BoardKey, subreddit: string | null = null): BoardView {
  return { board, variant: VARIANTS[board][0]?.key ?? '', subreddit: board === 'reddit' ? subreddit : null };
}

export function viewParams(view: BoardView): Record<string, string> {
  const params: Record<string, string> = {};
  if (view.variant) params.variant = view.variant;
  if (view.board === 'reddit' && view.subreddit) params.subreddit = view.subreddit;
  return params;
}

/** "周榜", "r/MachineLearning · 热门"; boards without versions have none. */
export function viewLabel(view: BoardView): string | undefined {
  const variant = VARIANTS[view.board].find((v) => v.key === view.variant)?.label;
  if (view.board === 'reddit') return view.subreddit ? `r/${view.subreddit} · ${variant ?? view.variant}` : undefined;
  return variant;
}

/**
 * A community name as typed: "r/Python", "/r/python/", "https://reddit.com/r/python" → "python"-ish
 * (case kept). Null when it is not a valid name (3–21 letters, digits, underscores).
 */
export function communityName(input: string): string | null {
  const trimmed = input.trim().replace(/^https?:\/\/(www\.|old\.)?reddit\.com/i, '');
  const name = trimmed.replace(/^\/?r\//i, '').replace(/\/+$/, '');
  return /^[A-Za-z0-9_]{2,21}$/.test(name) ? name : null;
}

/** Subscribed Reddit communities, from subscription URLs (quick choices in the sheet, old app default). */
export function subscribedCommunities(subscriptions: readonly { type: string; url: string }[]): string[] {
  const names = new Set<string>();
  for (const s of subscriptions) {
    if (s.type !== 'reddit') continue;
    const match = /reddit\.com\/r\/([A-Za-z0-9_]{2,21})/i.exec(s.url);
    if (match) names.add(match[1]);
  }
  return [...names];
}

export type Freshness =
  { kind: 'fresh'; age: string } | { kind: 'stale'; age: string } | { kind: 'paused'; reason: string; resumesIn: string };

function pauseReason(code: string | null): string {
  if (code?.endsWith('_http_429')) return '被对方限流';
  if (code?.endsWith('_challenge_required')) return '对方要求人机验证';
  return '对方暂时不可用';
}

/** "约 8 分钟", "约 2 小时" */
export function duration(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60_000));
  if (minutes < 60) return `约 ${minutes} 分钟`;
  return `约 ${Math.round(minutes / 60)} 小时`;
}

export function freshness(
  response: Pick<BoardResponse, 'fetched_at' | 'stale' | 'error_code' | 'paused_until'>,
  now: number,
): Freshness | null {
  if (!response.fetched_at) return null;
  const age = relativeTime(response.fetched_at, now);
  const paused = response.paused_until ? Date.parse(response.paused_until) - now : 0;
  if (paused > 0) return { kind: 'paused', reason: pauseReason(response.error_code), resumesIn: duration(paused) };
  return response.stale ? { kind: 'stale', age } : { kind: 'fresh', age };
}

/** Why a board has nothing to show, from the `board_unavailable` message's code in brackets. */
export function unavailableReason(message: string): string {
  const code = /\(([a-z0-9_]+)\)/.exec(message)?.[1] ?? '';
  if (code.endsWith('_not_configured')) return '服务器没有配置这个榜单';
  if (code.endsWith('_challenge_required')) return '对方开启了人机验证，服务器暂时抓不到';
  if (code.endsWith('_http_429')) return '被对方限流，稍后再试';
  return '服务器暂时抓不到这个榜单';
}

/** 1234 → "1.2k", 12_345 → "12k", 193_000 → "193k", 1_250_000 → "1.3m" */
export function compact(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (value < 1000) return String(value);
  if (value < 10_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  if (value < 1_000_000) return `${Math.round(value / 1000)}k`;
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}m`;
}

/** The row's data (src/components/BoardEntry.tsx). Titles and descriptions show the translation when there is one (P15). */
export function toRow(entry: BoardEntry, board: BoardKey, now: number) {
  const images = (entry.image_urls ?? []).filter((url) => /^https?:\/\//i.test(url));
  return {
    rank: entry.rank,
    url: entry.url,
    title: entry.title_translation?.text ?? entry.title,
    description: entry.description_translation?.text ?? entry.description ?? null,
    byline:
      board === 'reddit'
        ? entry.author
          ? `u/${entry.author.replace(/^\/?u\//, '')}`
          : null
        : board === 'hn' || board === 'product_hunt'
          ? (entry.source_label ?? null)
          : null,
    score: compact(entry.score),
    comments: compact(entry.comments),
    language: entry.language ?? null,
    stars: compact(entry.stars),
    forks: compact(entry.forks),
    // GitHub's images are the contributors' avatars; elsewhere the first image is the thumbnail.
    contributors:
      board === 'github' ? images.slice(0, 3).map((url, index) => ({ name: `${entry.title}#${index}`, avatarUrl: url })) : [],
    tags: board === 'techtwitter' ? (entry.tags ?? []).slice(0, 4) : [],
    quality:
      board === 'techtwitter' && entry.quality_score !== null && entry.quality_score !== undefined
        ? `质量 ${entry.quality_score}`
        : null,
    imageUrl: board === 'github' ? null : (images[0] ?? (entry.video_thumbnail_url || null)),
    age: board === 'techtwitter' && entry.published_at ? relativeTime(entry.published_at, now) : null,
    isSaved: entry.is_saved,
  };
}

export type BoardRow = ReturnType<typeof toRow>;

/** Texts the list still lacks a translation for (asked for with purposes board_title / board_description). */
export function missingTranslations(entries: readonly BoardEntry[]): { titles: string[]; descriptions: string[] } {
  const titles = new Set<string>();
  const descriptions = new Set<string>();
  for (const entry of entries) {
    if (!entry.title_translation && entry.title.trim()) titles.add(entry.title);
    if (!entry.description_translation && entry.description?.trim()) descriptions.add(entry.description);
  }
  return { titles: [...titles], descriptions: [...descriptions] };
}

/** Put finished translations into a board response (the cache), leaving everything else as is. */
export function withTranslations(
  response: BoardResponse,
  titles: ReadonlyMap<string, string>,
  descriptions: ReadonlyMap<string, string>,
  fingerprint: string,
): BoardResponse {
  let changed = false;
  const items = response.items.map((entry) => {
    const title = !entry.title_translation ? titles.get(entry.title) : undefined;
    const description = !entry.description_translation && entry.description ? descriptions.get(entry.description) : undefined;
    if (!title && !description) return entry;
    changed = true;
    return {
      ...entry,
      ...(title ? { title_translation: { text: title, engine_fingerprint: fingerprint } } : {}),
      ...(description ? { description_translation: { text: description, engine_fingerprint: fingerprint } } : {}),
    };
  });
  return changed ? { ...response, items } : response;
}
