/**
 * The one address users type before they are signed in: parse and normalise it.
 *
 * Localhost, private ranges, any DNS name, ports and a path prefix (reverse proxy under /reader)
 * are all allowed. Anything that could make the request go somewhere unexpected — credentials in
 * the URL, a query, a fragment, a non-HTTP scheme — is rejected with a code the UI turns into text.
 * Pure: runs under `node --test`.
 */

export type ServerUrlProblem =
  | 'empty'
  | 'invalid'
  | 'whitespace'
  | 'protocol'
  | 'credentials'
  | 'query'
  | 'fragment'
  | 'port';

export type ServerUrl = {
  /** origin + path prefix, no trailing slash: https://example.com/reader */
  base: string;
  origin: string;
  discoveryUrl: string;
};

export type ParseResult = { ok: true; url: ServerUrl } | { ok: false; problem: ServerUrlProblem };

export function parseServerUrl(input: string): ParseResult {
  const value = input.trim();
  if (!value) return { ok: false, problem: 'empty' };
  if (/\s/.test(value)) return { ok: false, problem: 'whitespace' };
  // Most people type "reader.example.com"; assume https then.
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`;

  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return { ok: false, problem: 'invalid' };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return { ok: false, problem: 'protocol' };
  if (!parsed.hostname) return { ok: false, problem: 'invalid' };
  if (parsed.username || parsed.password) return { ok: false, problem: 'credentials' };
  if (parsed.search || withScheme.includes('?')) return { ok: false, problem: 'query' };
  if (parsed.hash || withScheme.includes('#')) return { ok: false, problem: 'fragment' };
  if (parsed.port && Number(parsed.port) > 65_535) return { ok: false, problem: 'port' };

  const path = parsed.pathname.replace(/\/+$/, '');
  const base = `${parsed.origin}${path}`;
  return { ok: true, url: { base, origin: parsed.origin, discoveryUrl: `${base}/.well-known/reader.json` } };
}

/** An API route under a base URL that may carry a path prefix. */
export function joinUrl(base: string, route: string): string {
  return `${base.replace(/\/+$/, '')}/${route.replace(/^\/+/, '')}`;
}
