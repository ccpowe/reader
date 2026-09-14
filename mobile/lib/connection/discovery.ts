import { readerFetch } from '../readerTransport';
import { readServerToken } from './storage';
import { localizedMessage } from '../../i18n/message';
import { ConnectionError, redactUrl } from './errors';
import { parseReaderBaseUrl } from './url';
import type { ReaderBaseUrl, ReaderDiscovery, ReaderDiscoveryResult } from './types';

export type DiscoverOptions = {
  serverToken?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
};

const DEFAULT_DISCOVERY_TIMEOUT_MS = 10_000;
const MAX_SERVER_ID_LENGTH = 128;
const MAX_URL_LENGTH = 4_096;

function originsDiffer(left: string, right: string): boolean {
  try {
    return new URL(left).origin !== new URL(right).origin;
  } catch {
    return true;
  }
}

function validateDiscoveryUrl(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > MAX_URL_LENGTH) {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryFieldInvalid', { field }));
  }
  try {
    const parsed = parseReaderBaseUrl(value);
    return parsed.value;
  } catch {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryFieldInvalid', { field }));
  }
}

function validateDiscoveryPayload(value: unknown): ReaderDiscovery {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryObjectInvalid'));
  }
  const payload = value as Record<string, unknown>;
  const expected = [
    'protocol_version',
    'server_id',
    'api_base_url',
  ];
  const actual = Object.keys(payload).sort();
  if (actual.length !== expected.length || expected.some((key) => !actual.includes(key))) {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryFieldsInvalid'));
  }
  if (payload.protocol_version !== 2) {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryProtocolIncompatible'));
  }
  if (
    typeof payload.server_id !== 'string' ||
    !payload.server_id.trim() ||
    payload.server_id.length > MAX_SERVER_ID_LENGTH
  ) {
    throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryServerIdInvalid'));
  }
  return {
    api_base_url: validateDiscoveryUrl(payload.api_base_url, 'api_base_url'),
    protocol_version: 2,
    server_id: payload.server_id.trim(),
  };
}

function discoveryErrorForFetch(error: unknown, timedOut: boolean, signal?: AbortSignal): ConnectionError {
  if (timedOut) {
    return new ConnectionError('discovery_timeout', localizedMessage('errors:discoveryTimeout'), { cause: error });
  }
  if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) {
    return new ConnectionError('discovery_cancelled', localizedMessage('errors:connectionCancelled'), { cause: error });
  }
  return new ConnectionError('discovery_network', localizedMessage('errors:discoveryNetworkDirect'), { cause: error });
}

function throwIfDiscoveryAborted(
  controller: AbortController,
  timedOut: boolean,
  upstreamSignal?: AbortSignal,
): void {
  if (timedOut) {
    throw new ConnectionError('discovery_timeout', localizedMessage('errors:discoveryTimeout'));
  }
  if (controller.signal.aborted || upstreamSignal?.aborted) {
    throw new ConnectionError('discovery_cancelled', localizedMessage('errors:connectionCancelled'));
  }
}

/**
 * Fetch and validate one Reader discovery document.
 *
 * Credentials are sent only to the exact entered origin. Redirects and
 * discovery documents advertising another API origin are rejected.
 */
export async function discoverReaderServer(
  input: string | ReaderBaseUrl,
  options: DiscoverOptions = {},
): Promise<ReaderDiscoveryResult> {
  const parsed = parseReaderBaseUrl(String(input));
  const serverToken = options.serverToken ?? readServerToken(parsed.value);
  if (!serverToken) throw new ConnectionError('not_configured', localizedMessage('errors:serverTokenRequired'));
  const controller = new AbortController();
  const upstreamSignal = options.signal;
  let timedOut = false;
  const timeoutMs = Math.max(1, options.timeoutMs ?? DEFAULT_DISCOVERY_TIMEOUT_MS);
  const abortFromUpstream = () => controller.abort();
  if (upstreamSignal?.aborted) controller.abort();
  else upstreamSignal?.addEventListener('abort', abortFromUpstream, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const fetchImpl = options.fetchImpl ?? readerFetch;
  try {
    let response: Response;
    try {
      response = await fetchImpl(parsed.discovery_url, {
        headers: { Accept: 'application/json', 'X-Reader-Server-Token': serverToken },
        redirect: 'error',
        signal: controller.signal,
      });
    } catch (error) {
      throw discoveryErrorForFetch(error, timedOut, upstreamSignal);
    }
    throwIfDiscoveryAborted(controller, timedOut, upstreamSignal);
    if (!response.ok) {
      throw new ConnectionError(
        response.status === 401 ? 'server_token_invalid' : response.status === 503 ? 'discovery_configuration' : 'discovery_http',
        response.status === 401 ? localizedMessage('errors:serverTokenInvalid') : response.status === 503
          ? localizedMessage('errors:discoveryNotConfigured')
          : localizedMessage('errors:discoveryHttpFailed', { status: response.status }),
        { status: response.status, responseUrl: response.url },
      );
    }
    let payload: unknown;
    try {
      // Do not impose a fixed response-byte cap here.  A30 deliberately keeps
      // discovery limits at the platform/network layer rather than inventing
      // a client-side protocol size that could reject a valid deployment.
      payload = await response.json();
    } catch (error) {
      if (timedOut) {
        throw new ConnectionError('discovery_timeout', localizedMessage('errors:discoveryTimeout'), { cause: error });
      }
      if (controller.signal.aborted || upstreamSignal?.aborted) {
        throw new ConnectionError('discovery_cancelled', localizedMessage('errors:connectionCancelled'), { cause: error });
      }
      throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryJsonInvalid'), { cause: error });
    }
    throwIfDiscoveryAborted(controller, timedOut, upstreamSignal);
    const discovery = validateDiscoveryPayload(payload);
    const responseUrl = response.url || parsed.discovery_url;
    if (originsDiffer(parsed.discovery_url, responseUrl) || originsDiffer(parsed.discovery_url, discovery.api_base_url)) {
      throw new ConnectionError('discovery_invalid_response', localizedMessage('errors:serverRedirectBlocked'));
    }
    const redirected = false;
    throwIfDiscoveryAborted(controller, timedOut, upstreamSignal);
    return {
      ...discovery,
      base_url: parsed.value,
      discovery_url: parsed.discovery_url,
      redirected,
      // Cross-origin redirects and API coordinates have already been rejected.
      requires_confirmation: redirected,
      response_url: responseUrl,
    };
  } catch (error) {
    if (error instanceof ConnectionError) throw error;
    throw discoveryErrorForFetch(error, timedOut, upstreamSignal);
  } finally {
    clearTimeout(timeout);
    upstreamSignal?.removeEventListener('abort', abortFromUpstream);
  }
}

export function redirectConfirmationFor(
  result: ReaderDiscoveryResult,
): { from_url: string; to_url: string; from_origin: string; to_origin: string } | null {
  if (!result.redirected && !result.requires_confirmation) return null;
  try {
    return {
      from_origin: new URL(result.discovery_url).origin,
      from_url: redactUrl(result.discovery_url),
      to_origin: new URL(result.response_url).origin,
      to_url: redactUrl(result.response_url),
    };
  } catch {
    return {
      from_origin: result.discovery_url,
      from_url: redactUrl(result.discovery_url),
      to_origin: result.response_url,
      to_url: redactUrl(result.response_url),
    };
  }
}
