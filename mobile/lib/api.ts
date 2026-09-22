import { readerFetch } from './readerTransport';
import { isServerTokenInvalid, notifyServerTokenInvalid } from './connection/tokenEvents';
import { bindLocalizedErrorMessage, localizedMessage, resolveMessage, type LocalizedMessage } from '../i18n/message';
import { TranslationRequestError, translationRequestFailure } from '../domain/translationRequestErrors';
import { recordTranslationDiagnostic } from '../domain/translationDiagnostics';
import type { Session } from './readerAuth';

import { ConnectionError, redactConnectionText } from './connection/errors';
import {
  getActiveRuntimeSnapshot,
  getConnectionGenerationSnapshot,
  getReaderSessionEpochSnapshot,
  subscribeReaderSessionEpochSnapshot,
} from './connection/snapshot';
import type { ActiveReaderRuntime } from './connection/types';
import { resolveReaderUrl } from './connection/url';
import { HttpResponseError, httpResponseError, readJsonResponse } from './http';
import type {
  ArticleResponse as ApiArticle,
  FeedPageResponse as ApiFeedPage,
  FeedItemResponse as ApiFeedItem,
  ManagedEngineResponse as ApiManagedTranslationEngine,
  ProfileResponse as ApiProfile,
  RankingItemResponse as ApiRankingItem,
  RankingResponse as ApiRanking,
  SaveRankingRequest,
  RankingSavedStateResponse,
  SourceListItemResponse as ApiSourceListItem,
  SourceSubscriptionResponse as ApiSourceSubscription,
  TitleTranslationResponse as ApiTitleTranslation,
  TranslationPreferenceResponse as ApiTranslationPreference,
  TranslationSegmentResponse as ApiTranslationSegmentResult,
} from './generated/api';

const requestTimeoutMs = 15_000;

function localizedApiError(message: LocalizedMessage): Error {
  const error = new Error(resolveMessage(message));
  bindLocalizedErrorMessage(error, message);
  return error;
}

function requireApiRuntime(runtimeOverride?: ActiveReaderRuntime) {
  const runtime = runtimeOverride ?? getActiveRuntimeSnapshot();
  if (!runtime) throw new ConnectionError('not_configured', localizedMessage('errors:connectServerFirst'));
  if (
    runtimeOverride &&
    (getActiveRuntimeSnapshot() !== runtime || getConnectionGenerationSnapshot() !== runtime.generation)
  ) {
    throw new ConnectionError('stale_runtime', localizedMessage('errors:staleServerRequest'));
  }
  return runtime;
}

function runtimeApiUrl(runtime: ReturnType<typeof requireApiRuntime>, path: string): string {
  return resolveReaderUrl(runtime.api_base_url, path);
}

function backendError(payload: unknown, status: number): Error {
  const detail = payload && typeof payload === 'object' && 'detail' in payload
    ? (payload as { detail?: unknown }).detail
    : null;
  return typeof detail === 'string' && detail.trim()
    ? new Error(redactConnectionText(detail))
    : localizedApiError(localizedMessage('errors:backendStatus', { status }));
}

export type SourceSubscription = ApiSourceSubscription;
export type TranslationPreference = ApiTranslationPreference;
export type ManagedTranslationEngine = ApiManagedTranslationEngine;

export const DEFAULT_TRANSLATION_LOCALE = 'zh-CN';

export type TranslationStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | null;

export type TranslationPurpose =
  | 'title'
  | 'paragraph'
  | 'ranking_title'
  | 'ranking_description'
  | 'web_segment'
  | 'caption';

export type TranslationSegment = {
  segment_id: string;
  text: string;
  purpose: TranslationPurpose;
};

export type TranslationSegmentResult = Omit<ApiTranslationSegmentResult, 'purpose' | 'translation_status'> & {
  purpose: TranslationPurpose;
  translation_status: TranslationStatus;
};

export type TitleTranslation = Omit<ApiTitleTranslation, 'translation_status'> & {
  translation_status: TranslationStatus;
};

export type FeedItem = Omit<ApiFeedItem, 'translation_status'> & {
  ranking_kind?: RankingKind | null;
  translation_status: TranslationStatus;
};

export type CursorPage<T> = {
  channel_update_token?: ApiFeedPage['channel_update_token'];
  items: T[];
  next_cursor: string | null;
};

export type Article = Omit<ApiArticle, 'translation_status'> & {
  translation_status: TranslationStatus;
};

export type RankingKind = ApiRanking['kind'];
export type RedditRankingSort = 'hot' | 'rising' | 'top';

export type RankingItem = Omit<ApiRankingItem, 'description_translation_status' | 'title_translation_status'> & {
  ranking_context?: Omit<SaveRankingRequest, 'url'>;
  saved_content_id?: string;
  saved_state?: boolean;
  title_translation_status: TranslationStatus;
  description_translation_status: TranslationStatus;
};

export type Ranking = Omit<ApiRanking, 'items'> & {
  items: RankingItem[];
};

export type SourceListItem = ApiSourceListItem;
export type Profile = ApiProfile;

export async function checkBackend(session: Session, runtimeOverride?: ActiveReaderRuntime): Promise<string> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me'), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) {
    throw localizedApiError(localizedMessage('errors:backendStatus', { status: response.status }));
  }
  const data = payload as { id: string; email: string | null };
  return data.email ?? data.id;
}

export async function getProfile(session: Session, runtimeOverride?: ActiveReaderRuntime): Promise<Profile> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me/profile'), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || typeof payload.id !== 'string') {
    throw localizedApiError(localizedMessage('errors:profileResponseInvalid'));
  }
  return payload as Profile;
}

export async function updateProfile(
  session: Session,
  update: { displayName?: string | null; avatarUrl?: string | null },
  runtimeOverride?: ActiveReaderRuntime,
): Promise<Profile> {
  const runtime = requireApiRuntime(runtimeOverride);
  const body: Record<string, string | null> = {};
  if (update.displayName !== undefined) body.display_name = update.displayName;
  if (update.avatarUrl !== undefined) body.avatar_url = update.avatarUrl;
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me/profile'), {
    body: JSON.stringify(body),
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    method: 'PATCH',
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || typeof payload.id !== 'string') {
    throw localizedApiError(localizedMessage('errors:profileResponseInvalid'));
  }
  return payload as Profile;
}

export async function uploadAvatar(
  session: Session,
  input: { contentType: 'image/jpeg' | 'image/png' | 'image/webp'; body: ArrayBuffer | Blob },
  runtimeOverride?: ActiveReaderRuntime,
): Promise<Profile> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me/profile/avatar'), {
    method: 'PUT', body: input.body,
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': input.contentType },
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || typeof payload.id !== 'string') {
    throw localizedApiError(localizedMessage('errors:avatarUploadResponseInvalid'));
  }
  return payload as Profile;
}

export async function getTranslationPreference(session: Session, runtimeOverride?: ActiveReaderRuntime): Promise<TranslationPreference> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me/translation-preference'), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || typeof payload.target_locale !== 'string') {
    throw localizedApiError(localizedMessage('errors:translationPreferenceResponseInvalid'));
  }
  return payload as TranslationPreference;
}

export async function updateTranslationPreference(
  session: Session,
  update: { targetLocale?: string; isEnabled?: boolean; engineId?: string | null },
  runtimeOverride?: ActiveReaderRuntime,
): Promise<TranslationPreference> {
  const runtime = requireApiRuntime(runtimeOverride);
  const body: Record<string, string | boolean | null> = {};
  if (update.targetLocale !== undefined) body.target_locale = update.targetLocale;
  if (update.isEnabled !== undefined) body.is_enabled = update.isEnabled;
  if (update.engineId !== undefined) body.engine_id = update.engineId;
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/me/translation-preference'), {
    body: JSON.stringify(body),
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    method: 'PATCH',
  });
  if (!response.ok) throw backendError(payload, response.status);
  return payload as TranslationPreference;
}

export function displayTitle(item: { title: string; translated_title?: string | null }): string {
  const translated = item.translated_title?.trim();
  return translated || item.title;
}

export async function addRssSource(session: Session, url: string, folderName?: string | null, runtimeOverride?: ActiveReaderRuntime): Promise<SourceSubscription> {
  return addSource(session, 'rss', url, folderName, runtimeOverride);
}

export async function addWebSource(session: Session, url: string, folderName?: string | null, runtimeOverride?: ActiveReaderRuntime): Promise<SourceSubscription> {
  return addSource(session, 'web', url, folderName, runtimeOverride);
}

export async function addRedditSource(
  session: Session,
  subreddit: string,
  folderName?: string | null,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<SourceSubscription> {
  // A subscription represents a community. Hot controls inbox admission;
  // the Rankings page reads the separately refreshed Hot, Top, and Rising snapshots.
  return addTypedSource(session, 'reddit', { subreddit, folder_name: folderName ?? null }, runtimeOverride);
}

export async function addYouTubeSource(session: Session, channelReference: string, folderName?: string | null, runtimeOverride?: ActiveReaderRuntime): Promise<SourceSubscription> {
  return addTypedSource(session, 'youtube', { channel_id: channelReference, folder_name: folderName ?? null }, runtimeOverride);
}

export async function addXSource(session: Session, handle: string, folderName?: string | null, runtimeOverride?: ActiveReaderRuntime): Promise<SourceSubscription> {
  return addTypedSource(session, 'x', { handle, folder_name: folderName ?? null }, runtimeOverride);
}

export async function getFeedPage(
  session: Session,
  options: { folderName?: string | null; sourceId?: string | null; cursor?: string | null; limit?: number } = {},
  runtimeOverride?: ActiveReaderRuntime,
): Promise<CursorPage<FeedItem>> {
  const runtime = requireApiRuntime(runtimeOverride);
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.folderName) params.set('folder_name', options.folderName);
  if (options.sourceId) params.set('source_id', options.sourceId);
  if (options.cursor) params.set('cursor', options.cursor);
  const { response, payload } = await fetchJsonWithTimeout(`${runtimeApiUrl(runtime, '/v1/feed/page')}?${params.toString()}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || !Array.isArray(payload.items)) throw localizedApiError(localizedMessage('errors:feedResponseInvalid'));
  const items = payload.items as FeedItem[];
  if (options.sourceId && items.some((item) => item.source_id !== options.sourceId)) {
    throw localizedApiError(localizedMessage('errors:sourceFilterUnsupported'));
  }
  return { ...payload, items: withProxiedSourceAvatars(items, runtime) };
}

export async function getArticle(session: Session, contentId: string, runtimeOverride?: ActiveReaderRuntime): Promise<Article> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/feed/${encodeURIComponent(contentId)}`), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  return payload as Article;
}

export async function getSavedContentPage(
  session: Session,
  options: { cursor?: string | null; limit?: number; query?: string } = {},
  runtimeOverride?: ActiveReaderRuntime,
): Promise<CursorPage<FeedItem>> {
  const runtime = requireApiRuntime(runtimeOverride);
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.cursor) params.set('cursor', options.cursor);
  if (options.query?.trim()) params.set('q', options.query.trim());
  const { response, payload } = await fetchJsonWithTimeout(`${runtimeApiUrl(runtime, '/v1/saved/page')}?${params.toString()}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || !Array.isArray(payload.items)) throw localizedApiError(localizedMessage('errors:savedResponseInvalid'));
  return { ...payload, items: withProxiedSourceAvatars(payload.items as FeedItem[], runtime) };
}

export async function resolveTitleTranslations(
  session: Session,
  contentIds: string[],
  runtimeOverride?: ActiveReaderRuntime,
): Promise<TitleTranslation[]> {
  const runtime = requireApiRuntime(runtimeOverride);
  if (!contentIds.length) return [];
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/translations/titles'), {
    body: JSON.stringify({ content_ids: contentIds.slice(0, 100) }),
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!Array.isArray(payload)) throw localizedApiError(localizedMessage('errors:translationStatusResponseInvalid'));
  return payload as TitleTranslation[];
}

export async function resolveTranslationSegments(
  session: Session,
  segments: TranslationSegment[],
  signal?: AbortSignal,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<TranslationSegmentResult[]> {
  const runtime = requireApiRuntime(runtimeOverride);
  if (!segments.length) return [];
  if (segments.length > 100) throw new TranslationRequestError(localizedMessage('errors:translationSegmentLimit'), 'contract');
  if (segments.some((segment) => segment.text.length > 8_000)) {
    throw new TranslationRequestError(localizedMessage('errors:translationSegmentSizeLimit'), 'contract');
  }
  if (segments.reduce((total, segment) => total + segment.text.length, 0) > 30_000) {
    throw new TranslationRequestError(localizedMessage('errors:translationRequestSizeLimit'), 'contract');
  }
  const requestId = `translation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  for (const segment of segments) recordTranslationDiagnostic('segment_request', {
    stage: 'api', requestId, segmentId: segment.segment_id });
  const started = Date.now();
  let status: number | undefined;
  try {
    let body = '';
    const response = await fetchWithTimeout(runtimeApiUrl(runtime, '/v1/translations/segments'), {
      body: JSON.stringify({ segments }),
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
        'X-Reader-Request-ID': requestId,
      },
      method: 'POST', signal,
    }, undefined, async response => {
      status = response.status;
      if (response.ok) body = await response.text();
    });
    status = response.status;
    // Error pages may be HTML. Their HTTP status still controls recovery.
    if (!response.ok) throw httpResponseError(response, localizedMessage('errors:translationService'));
    let payload: unknown;
    try { payload = JSON.parse(body); }
    catch { throw new TranslationRequestError(localizedMessage('errors:translationJsonInvalid'), 'parse', status); }
    requireApiRuntime(runtime);
    const expected = new Map(segments.map(segment => [segment.segment_id, segment]));
    const seen = new Set<string>();
    if (!Array.isArray(payload) || payload.length !== segments.length || payload.some((item) => {
      if (!item || typeof item !== 'object' || typeof item.segment_id !== 'string' ||
        seen.has(item.segment_id) || !expected.has(item.segment_id) ||
        item.purpose !== expected.get(item.segment_id)?.purpose ||
        (item.translated_text !== null && typeof item.translated_text !== 'string') ||
        (item.error_code !== null && typeof item.error_code !== 'string') ||
        (item.translation_status !== null && !['pending', 'running', 'succeeded', 'failed', 'cancelled'].includes(item.translation_status)) ||
        (item.translation_status === 'succeeded' && typeof item.translated_text !== 'string') ||
        (item.retry_after_ms != null && (typeof item.retry_after_ms !== 'number' || !Number.isFinite(item.retry_after_ms) || item.retry_after_ms < 0))) return true;
      seen.add(item.segment_id);
      return false;
    })) throw new TranslationRequestError(localizedMessage('errors:translationSegmentResponseInvalid'), 'contract', status);
    recordTranslationDiagnostic('request_finished', { stage: 'api', requestId, durationMs: Date.now() - started,
      httpStatus: status, status: 'succeeded', count: segments.length });
    return payload as TranslationSegmentResult[];
  } catch (error) {
    const failure = translationRequestFailure(error);
    recordTranslationDiagnostic('request_finished', { stage: 'api', requestId, durationMs: Date.now() - started,
      httpStatus: failure.status ?? status, status: 'failed', reason: failure.kind, retryAfterMs: failure.retryAfterMs });
    throw error;
  }
}

export async function getSources(session: Session, runtimeOverride?: ActiveReaderRuntime): Promise<SourceListItem[]> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/sources'), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  if (!Array.isArray(payload)) {
    throw localizedApiError(localizedMessage('errors:sourceResponseInvalid'));
  }
  return withProxiedSourceAvatars(payload as SourceListItem[], runtime);
}

export function avatarImageSource(uri: string, accessToken: string): { uri: string; headers?: Record<string, string> }[] {
  const runtime = getActiveRuntimeSnapshot();
  // Only Reader's authenticated source-avatar proxy may receive credentials.
  if (runtime && isReaderApiUrl(uri, runtime)) {
    return [{ uri, headers: { Authorization: `Bearer ${accessToken}`, 'X-Reader-Server-Token': runtime.serverToken } }];
  }
  return [{ uri }];
}

function isReaderApiUrl(url: string, runtime: ActiveReaderRuntime): boolean {
  try {
    const target = new URL(url);
    const base = new URL(runtime.api_base_url);
    const prefix = base.pathname.replace(/\/$/, '') + '/v1/';
    return target.origin === base.origin && target.pathname.startsWith(prefix);
  } catch { return false; }
}

function withProxiedSourceAvatars<T extends { avatar_url?: string | null; source_avatar_url?: string | null; source_id: string }>(items: T[], runtimeOverride?: ActiveReaderRuntime): T[] {
  const runtime = runtimeOverride ?? getActiveRuntimeSnapshot();
  if (!runtime) return items;
  return items.map((item) => {
    if (!item.avatar_url && !item.source_avatar_url) return item;
    const avatarUrl = runtimeApiUrl(runtime, `/v1/sources/${encodeURIComponent(item.source_id)}/avatar`);
    return {
      ...item,
      ...(item.source_avatar_url !== undefined ? { source_avatar_url: avatarUrl } : {}),
      ...(item.avatar_url !== undefined ? { avatar_url: avatarUrl } : {}),
    };
  });
}

export async function getRanking(
  session: Session,
  kind: RankingKind,
  options?: { subreddit?: string; sort?: RedditRankingSort; timeFilter?: 'day' | 'week' | 'month' | 'year' },
  force = false,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<Ranking> {
  const runtime = requireApiRuntime(runtimeOverride);
  const params = new URLSearchParams();
  if (kind === 'reddit') {
    params.set('subreddit', options?.subreddit ?? 'MachineLearning');
    params.set('sort', options?.sort ?? 'hot');
    params.set('time_filter', options?.timeFilter ?? 'week');
  }
  if (force) params.set('refresh', 'true');
  const query = params.size ? `?${params.toString()}` : '';
  const { response, payload } = await fetchJsonWithTimeout(`${runtimeApiUrl(runtime, `/v1/rankings/${kind}`)}${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { items?: unknown }).items)) {
    throw localizedApiError(localizedMessage('errors:rankingResponseInvalid'));
  }
  return payload as Ranking;
}

export async function removeSourceSubscription(session: Session, subscriptionId: string, runtimeOverride?: ActiveReaderRuntime): Promise<void> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${encodeURIComponent(subscriptionId)}`), {
    headers: { Authorization: `Bearer ${session.access_token}` },
    method: 'DELETE',
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
}

export async function updateSourceSubscriptionFolder(session: Session, subscriptionId: string, folderName: string | null, runtimeOverride?: ActiveReaderRuntime): Promise<SourceListItem> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${encodeURIComponent(subscriptionId)}`), {
    body: JSON.stringify({ folder_name: folderName }),
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    method: 'PATCH',
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  return withProxiedSourceAvatars([payload as SourceListItem], runtime)[0];
}

export async function updateSourceSubscriptionHomeInclusion(
  session: Session,
  subscriptionId: string,
  includeInHome: boolean,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<SourceListItem> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${encodeURIComponent(subscriptionId)}`), {
    body: JSON.stringify({ include_in_home: includeInHome }),
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    method: 'PATCH',
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || (payload as { include_in_home?: unknown }).include_in_home !== includeInHome) {
    throw localizedApiError(localizedMessage('errors:sourceResponseInvalid'));
  }
  return withProxiedSourceAvatars([payload as SourceListItem], runtime)[0];
}

export async function markSourceSubscriptionViewed(
  session: Session,
  subscriptionId: string,
  channelUpdateToken: string,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<SourceListItem> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${encodeURIComponent(subscriptionId)}/viewed`), {
    body: JSON.stringify({ channel_update_token: channelUpdateToken }),
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    method: 'POST',
  });
  if (!response.ok) throw backendError(payload, response.status);
  if (!payload || typeof payload !== 'object' || typeof (payload as { subscription_id?: unknown }).subscription_id !== 'string') {
    throw localizedApiError(localizedMessage('errors:sourceResponseInvalid'));
  }
  return withProxiedSourceAvatars([payload as SourceListItem], runtime)[0];
}

export async function setSavedContent(session: Session, contentId: string, isSaved: boolean, runtimeOverride?: ActiveReaderRuntime): Promise<void> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/saved/${encodeURIComponent(contentId)}`), {
    headers: { Authorization: `Bearer ${session.access_token}` },
    method: isSaved ? 'PUT' : 'DELETE',
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
}

async function addSource(
  session: Session,
  kind: 'rss' | 'web',
  url: string,
  folderName?: string | null,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<SourceSubscription> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${kind}`), {
    body: JSON.stringify({ url, folder_name: folderName ?? null }),
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  return payload as SourceSubscription;
}

async function addTypedSource(
  session: Session,
  kind: 'reddit' | 'youtube' | 'x',
  body: Record<string, string | number | null>,
  runtimeOverride?: ActiveReaderRuntime,
): Promise<SourceSubscription> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, `/v1/sources/${kind}`), {
    body: JSON.stringify(body),
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
  });
  if (!response.ok) {
    throw backendError(payload, response.status);
  }
  return payload as SourceSubscription;
}

/** Keep JSON decoding inside the timeout, cancellation and stale-result boundary. */
async function fetchJsonWithTimeout(url: string, options: RequestInit) {
  let payload: any;
  const response = await fetchWithTimeout(url, options, undefined, async response => {
    payload = await readJsonResponse(response);
  });
  return { response, payload };
}

export async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  requestGeneration = getConnectionGenerationSnapshot(),
  consumeResponse?: (response: Response) => Promise<void>,
): Promise<Response> {
  const requestSessionEpoch = getReaderSessionEpochSnapshot();
  const requestRuntime = getActiveRuntimeSnapshot();
  const controller = new AbortController();
  const upstreamSignal = options.signal;
  let timedOut = false;
  const abortError = () => Object.assign(new Error('Request aborted'), { name: 'AbortError' });
  let rejectAborted: (error: Error) => void = () => {};
  const aborted = new Promise<never>((_resolve, reject) => { rejectAborted = reject; });
  const onAbort = () => rejectAborted(abortError());
  controller.signal.addEventListener('abort', onAbort, { once: true });
  const abortFromUpstream = () => controller.abort();
  if (upstreamSignal?.aborted) controller.abort();
  else upstreamSignal?.addEventListener('abort', abortFromUpstream, { once: true });
  const unsubscribeSessionEpoch = subscribeReaderSessionEpochSnapshot(() => {
    if (requestSessionEpoch !== getReaderSessionEpochSnapshot()) controller.abort();
  });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, requestTimeoutMs);
  try {
    const response = await Promise.race([
      (async () => {
        if (controller.signal.aborted) throw abortError();
        const headers = new Headers(options.headers);
        const authenticated = Boolean(requestRuntime && isReaderApiUrl(url, requestRuntime));
        if (!authenticated && (headers.has('Authorization') || headers.has('X-Reader-Server-Token'))) {
          throw new ConnectionError('stale_runtime', localizedMessage('errors:staleRuntimeRequest'));
        }
        if (authenticated && requestRuntime) {
          if (isServerTokenInvalid(requestRuntime)) {
            throw new ConnectionError('server_token_invalid', localizedMessage('errors:serverTokenInvalid'));
          }
          headers.set('X-Reader-Server-Token', requestRuntime.serverToken);
          // Use the live session after refresh; caller props may still hold an older JWT.
          const accessToken = await requestRuntime.authClient?.auth.getAccessToken?.();
          if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
        }
        if (controller.signal.aborted || requestGeneration !== getConnectionGenerationSnapshot() ||
            requestSessionEpoch !== getReaderSessionEpochSnapshot()) throw abortError();
        let result = await readerFetch(url, { ...options, headers, redirect: 'error', signal: controller.signal });
        if (result.status === 401 && authenticated && requestRuntime?.authClient) {
          const failed = await result.clone().json().catch(() => null);
          if (failed?.detail === 'Invalid server access token' || failed?.detail === 'invalid_server_token') {
            notifyServerTokenInvalid(requestRuntime);
          } else {
            const next = await requestRuntime.authClient.auth.refreshSession();
            if (controller.signal.aborted || requestGeneration !== getConnectionGenerationSnapshot() ||
                requestSessionEpoch !== getReaderSessionEpochSnapshot()) throw abortError();
            if (next) {
              headers.set('Authorization', `Bearer ${next.access_token}`);
              result = await readerFetch(url, { ...options, headers, redirect: 'error', signal: controller.signal });
            }
          }
        }
        // A native transport may ignore abort while reading its body; the race still settles.
        if (consumeResponse) await consumeResponse(result);
        return result;
      })(),
      aborted,
    ]);
    if (controller.signal.aborted) {
      if (timedOut) throw new TranslationRequestError(localizedMessage('errors:backendTimeout'), 'timeout');
      throw new ConnectionError('discovery_cancelled', localizedMessage('errors:requestCancelled'));
    }
    if (requestGeneration !== getConnectionGenerationSnapshot() ||
      requestSessionEpoch !== getReaderSessionEpochSnapshot()) {
      throw new ConnectionError('stale_runtime', localizedMessage('errors:staleRuntimeRequest'));
    }
    return response;
  } catch (error) {
    if (requestGeneration !== getConnectionGenerationSnapshot() ||
      requestSessionEpoch !== getReaderSessionEpochSnapshot()) {
      throw new ConnectionError('stale_runtime', localizedMessage('errors:staleRuntimeRequest'));
    }
    if (error instanceof ConnectionError || error instanceof TranslationRequestError || error instanceof HttpResponseError) throw error;
    if (controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      if (upstreamSignal?.aborted) throw new ConnectionError('discovery_cancelled', localizedMessage('errors:requestCancelled'));
      if (!timedOut) throw new TranslationRequestError(localizedMessage('errors:requestCancelled'), 'abort');
      throw new TranslationRequestError(localizedMessage('errors:backendTimeout'), 'timeout');
    }
    throw new TranslationRequestError(localizedMessage('errors:backendNetwork'), 'network');
  } finally {
    clearTimeout(timeout);
    unsubscribeSessionEpoch();
    controller.signal.removeEventListener('abort', onAbort);
    upstreamSignal?.removeEventListener('abort', abortFromUpstream);
  }
}

export async function getRankingSavedState(session: Session, url: string, runtimeOverride?: ActiveReaderRuntime): Promise<RankingSavedStateResponse> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(`${runtimeApiUrl(runtime, '/v1/saved/ranking')}?${new URLSearchParams({ url })}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) throw backendError(payload, response.status);
  return payload as RankingSavedStateResponse;
}

export async function saveRankingContent(session: Session, body: SaveRankingRequest, runtimeOverride?: ActiveReaderRuntime): Promise<RankingSavedStateResponse> {
  const runtime = requireApiRuntime(runtimeOverride);
  const { response, payload } = await fetchJsonWithTimeout(runtimeApiUrl(runtime, '/v1/saved/ranking'), {
    method: 'PUT',
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw backendError(payload, response.status);
  return payload as RankingSavedStateResponse;
}
