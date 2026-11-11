/**
 * Ask a server who it is: GET <base>/.well-known/reader.json with the deployment token.
 *
 * The answer gives the server id (shown to the user to confirm, and part of the cache identity)
 * and the API base URL. A server that redirects elsewhere is refused: the user enters the final
 * address instead. (Android's HTTP stack may already have followed the redirect with the token
 * header; refusing at least stops the app from adopting the other host.)
 * Pure apart from the injected `fetch`: runs under `node --test`.
 */
import { createApiClient } from '../api/client.ts';
import { ApiError, isApiError } from '../api/errors.ts';
import { parseServerUrl, type ServerUrlProblem } from './url.ts';

export const PROTOCOL_VERSION = 3;

export type DiscoveryProblem =
  | { kind: 'address'; problem: ServerUrlProblem }
  /** nothing answered */
  | { kind: 'unreachable' }
  | { kind: 'timeout' }
  /** it answered, but is not a Reader server (or not this version) */
  | { kind: 'not-reader' }
  | { kind: 'old-protocol'; version: unknown }
  | { kind: 'redirected'; to: string }
  /** an https address whose server sends the app to plain http for the API */
  | { kind: 'insecure-api'; to: string }
  | { kind: 'wrong-token' }
  | { kind: 'not-configured' }
  | { kind: 'rate-limited'; retryAfter?: number };

export type Discovered = { base: string; serverId: string; apiBaseUrl: string };

export type DiscoveryResult = { ok: true; server: Discovered } | { ok: false; problem: DiscoveryProblem };

export async function discover(
  address: string,
  serverToken: string,
  options: { fetch?: typeof fetch; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<DiscoveryResult> {
  const parsed = parseServerUrl(address);
  if (!parsed.ok) return { ok: false, problem: { kind: 'address', problem: parsed.problem } };
  const url = parsed.url;

  // Detect a followed redirect: compare where the response came from.
  let responseUrl = '';
  const baseFetch = options.fetch ?? fetch;
  const watching = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await baseFetch(input, init);
    responseUrl = response.url;
    return response;
  }) as typeof fetch;

  const api = createApiClient({ apiBaseUrl: url.base, serverToken, fetch: watching, timeoutMs: options.timeoutMs ?? 10_000 });
  let body: unknown;
  try {
    body = await api.request<unknown>('/.well-known/reader.json', { authenticated: false, signal: options.signal });
  } catch (error) {
    return { ok: false, problem: problemOf(error) };
  }
  if (responseUrl && new URL(responseUrl).origin !== url.origin) {
    return { ok: false, problem: { kind: 'redirected', to: new URL(responseUrl).origin } };
  }

  const payload = body as { protocol_version?: unknown; server_id?: unknown; api_base_url?: unknown } | null;
  if (!payload || typeof payload !== 'object') return { ok: false, problem: { kind: 'not-reader' } };
  if (payload.protocol_version !== PROTOCOL_VERSION) {
    return { ok: false, problem: payload.protocol_version === undefined ? { kind: 'not-reader' } : { kind: 'old-protocol', version: payload.protocol_version } };
  }
  if (typeof payload.server_id !== 'string' || !payload.server_id.trim() || payload.server_id.length > 128) {
    return { ok: false, problem: { kind: 'not-reader' } };
  }
  const api_base = typeof payload.api_base_url === 'string' ? parseServerUrl(payload.api_base_url) : null;
  if (!api_base?.ok) return { ok: false, problem: { kind: 'not-reader' } };
  // Never let the server move tokens and passwords from https to plain http.
  if (url.origin.startsWith('https:') && api_base.url.origin.startsWith('http:')) {
    return { ok: false, problem: { kind: 'insecure-api', to: api_base.url.origin } };
  }
  return { ok: true, server: { base: url.base, serverId: payload.server_id, apiBaseUrl: api_base.url.base } };
}

function problemOf(error: unknown): DiscoveryProblem {
  if (!isApiError(error)) return { kind: 'unreachable' };
  const p = (error as ApiError).problem;
  if (p.kind === 'timeout') return { kind: 'timeout' };
  if (p.kind === 'network' || p.kind === 'aborted') return { kind: 'unreachable' };
  if (p.kind === 'invalid-response') return { kind: 'not-reader' };
  if (p.code === 'invalid_server_token') return { kind: 'wrong-token' };
  if (p.code === 'server_not_configured') return { kind: 'not-configured' };
  if (p.status === 429) return { kind: 'rate-limited', retryAfter: p.retryAfter };
  return { kind: 'not-reader' };
}
