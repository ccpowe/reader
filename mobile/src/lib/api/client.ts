/**
 * The Reader HTTP client: one place that knows the base URL, the deployment token header, the
 * user's access token, timeouts, cancellation and the error envelope.
 *
 * - Every request carries `X-Reader-Server-Token`; signed-in requests also `Authorization: Bearer`.
 * - A 401 `not_authenticated` on a signed-in request asks the session for a fresh access token
 *   once (the session single-flights refreshes) and repeats the request once.
 * - `signal` from the caller (TanStack Query passes one) cancels the request; a cancelled request
 *   rejects with problem `aborted`, which callers do not show as a failure.
 * - Redirects are refused: a token must never follow a redirect to another host.
 *
 * Pure apart from the injected `fetch`: runs under `node --test`.
 */
import { ApiError, problemFromBody } from './errors.ts';
import { joinUrl } from '../connection/url.ts';

export type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Query;
  /** JSON body; a `FormData` is sent as multipart */
  body?: unknown;
  signal?: AbortSignal;
  /** false for sign-in, register, refresh and discovery */
  authenticated?: boolean;
  timeoutMs?: number;
};

/**
 * An access token together with the sign-in it belongs to. The owner is opaque to the client; the
 * session uses it so a 401 from an old sign-in (or an old server) never gets a newer token.
 */
export type Credential = { token: string; owner: unknown };

/** What the client needs from whoever holds the user's tokens. */
export type TokenSource = {
  accessToken(): Credential | null;
  /** A fresh credential of the same sign-in after a 401, or null when that sign-in is over. */
  refreshAccessToken(rejected: Credential): Promise<Credential | null>;
};

export type ApiClientConfig = {
  apiBaseUrl: string;
  serverToken: string;
  tokens?: TokenSource;
  fetch?: typeof fetch;
  timeoutMs?: number;
};

const DEFAULT_TIMEOUT_MS = 15_000;

export function buildQuery(query: Query | undefined): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) for (const item of value) params.append(key, String(item));
    else params.append(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

type Answer = { status: number; body: unknown; retryAfter: string | null };

export function createApiClient(config: ApiClientConfig) {
  const doFetch = config.fetch ?? fetch;

  /**
   * One HTTP exchange. The deadline and the caller's cancellation cover the whole exchange,
   * including reading the body: a server that sends headers and then stalls is still cut off.
   */
  async function exchange(path: string, options: RequestOptions, accessToken: string | null): Promise<Answer> {
    const timeoutMs = options.timeoutMs ?? config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onAbort = () => controller.abort();
    options.signal?.addEventListener('abort', onAbort);
    if (options.signal?.aborted) controller.abort();

    const headers: Record<string, string> = { Accept: 'application/json', 'X-Reader-Server-Token': config.serverToken };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    let body: BodyInit | undefined;
    if (options.body instanceof FormData) body = options.body;
    else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.body);
    }

    const failed = () => new ApiError(options.signal?.aborted ? { kind: 'aborted' } : { kind: timedOut ? 'timeout' : 'network' });
    try {
      let response: Response;
      try {
        response = await doFetch(joinUrl(config.apiBaseUrl, path) + buildQuery(options.query), {
          method: options.method ?? 'GET',
          headers,
          body,
          signal: controller.signal,
          redirect: 'error',
        });
      } catch {
        throw failed();
      }
      if (response.status === 204) return { status: 204, body: undefined, retryAfter: null };
      let text: string;
      try {
        // Settles when the body is read, or rejects when the controller aborts the stream.
        text = await Promise.race([
          response.text(),
          new Promise<never>((_, reject) => controller.signal.addEventListener('abort', () => reject(failed()))),
        ]);
      } catch (error) {
        throw error instanceof ApiError ? error : failed();
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        throw new ApiError({ kind: 'invalid-response', status: response.status });
      }
      return { status: response.status, body: parsed, retryAfter: response.headers.get('Retry-After') };
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', onAbort);
    }
  }

  function result<T>(answer: Answer): T {
    if (answer.status >= 200 && answer.status < 300) return answer.body as T;
    throw new ApiError(problemFromBody(answer.status, answer.body, answer.retryAfter));
  }

  /** Send a request and return the parsed JSON body (undefined for 204). */
  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const authenticated = options.authenticated ?? true;
    const credential = authenticated ? (config.tokens?.accessToken() ?? null) : null;
    const answer = await exchange(path, options, credential?.token ?? null);
    if (answer.status === 401 && credential && config.tokens) {
      const problem = problemFromBody(401, answer.body, answer.retryAfter);
      if (problem.kind === 'http' && problem.code === 'not_authenticated') {
        const fresh = await config.tokens.refreshAccessToken(credential);
        if (options.signal?.aborted) throw new ApiError({ kind: 'aborted' });
        if (fresh) return result<T>(await exchange(path, options, fresh.token));
      }
      throw new ApiError(problem);
    }
    return result<T>(answer);
  }

  return { request };
}

export type ApiClient = ReturnType<typeof createApiClient>;
