import { localizedMessage } from '../../i18n/message';
import { ConnectionError } from './errors';
import type { ReaderBaseUrl } from './types';

export type ParsedReaderBaseUrl = {
  value: ReaderBaseUrl;
  origin: string;
  path_prefix: string;
  discovery_url: string;
};

function branded(value: string): ReaderBaseUrl {
  return value as ReaderBaseUrl;
}

/**
 * Parse and canonicalise the one URL users enter before authentication.
 *
 * Localhost, private IPv4/IPv6 ranges, arbitrary DNS names, ports, and path
 * prefixes are all valid.  Transport and URL component restrictions are kept
 * explicit so a malformed value cannot silently become an unrelated request.
 */
export function parseReaderBaseUrl(input: string): ParsedReaderBaseUrl {
  if (typeof input !== 'string') {
    throw new ConnectionError('invalid_url', localizedMessage('errors:serverAddressRequired'));
  }
  const value = input.trim();
  if (!value) throw new ConnectionError('invalid_url', localizedMessage('errors:serverAddressRequired'));
  if (/\s/.test(value) && !/^https?:\/\/[^\s]+$/i.test(value)) {
    throw new ConnectionError('invalid_url', localizedMessage('errors:serverAddressWhitespace'));
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new ConnectionError('invalid_url', localizedMessage('errors:serverAddressInvalid'));
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new ConnectionError('unsupported_protocol', localizedMessage('errors:serverProtocolInvalid'));
  }
  if (!parsed.hostname || !parsed.host) {
    throw new ConnectionError('missing_host', localizedMessage('errors:serverHostMissing'));
  }
  if (parsed.username || parsed.password) {
    throw new ConnectionError('userinfo_not_allowed', localizedMessage('errors:serverCredentialsNotAllowed'));
  }
  if (parsed.search) throw new ConnectionError('query_not_allowed', localizedMessage('errors:serverQueryNotAllowed'));
  if (parsed.hash) throw new ConnectionError('fragment_not_allowed', localizedMessage('errors:serverFragmentNotAllowed'));
  if (parsed.port && (!/^\d+$/.test(parsed.port) || Number(parsed.port) > 65_535)) {
    throw new ConnectionError('invalid_url', localizedMessage('errors:serverPortInvalid'));
  }

  const pathPrefix = parsed.pathname.replace(/\/+$|^$/, (match) => match === '/' ? '' : match);
  // URL normalisation intentionally preserves a path prefix but removes its
  // trailing separator.  The root URL is represented without a final slash.
  const normalizedPath = pathPrefix === '/' ? '' : pathPrefix;
  const normalized = `${parsed.origin}${normalizedPath}`;
  const discoveryUrl = `${normalized}/.well-known/reader.json`;
  return {
    discovery_url: discoveryUrl,
    origin: parsed.origin,
    path_prefix: normalizedPath,
    value: branded(normalized),
  };
}

export function normalizeReaderBaseUrl(input: string): ReaderBaseUrl {
  return parseReaderBaseUrl(input).value;
}

/** Resolve a Reader-relative API route while retaining a configured prefix. */
export function resolveReaderUrl(baseUrl: ReaderBaseUrl | string, route: string): string {
  const base = parseReaderBaseUrl(String(baseUrl));
  if (/^https?:\/\//i.test(route)) return route;
  const suffix = route.startsWith('/') ? route : `/${route}`;
  return `${base.value}${suffix}`;
}

export function readerDiscoveryUrl(baseUrl: ReaderBaseUrl | string): string {
  return parseReaderBaseUrl(String(baseUrl)).discovery_url;
}
