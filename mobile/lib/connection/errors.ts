import { bindLocalizedErrorMessage, localizedMessage, resolveMessage, type LocalizedMessage } from '../../i18n/message';
export type ConnectionErrorCode =
  | 'invalid_url'
  | 'unsupported_protocol'
  | 'userinfo_not_allowed'
  | 'query_not_allowed'
  | 'fragment_not_allowed'
  | 'missing_host'
  | 'discovery_timeout'
  | 'discovery_cancelled'
  | 'discovery_network'
  | 'discovery_http'
  | 'discovery_invalid_response'
  | 'discovery_configuration'
  | 'redirect_confirmation_required'
  | 'identity_confirmation_required'
  | 'activation_failed'
  | 'stale_runtime'
  | 'server_token_invalid'
  | 'not_configured';

export type ConnectionErrorOptions = {
  cause?: unknown;
  status?: number;
  responseUrl?: string;
  safeMessage?: string | LocalizedMessage;
};

/** Public, non-secret failure used by connection and activation UI. */
export class ConnectionError extends Error {
  readonly code: ConnectionErrorCode;
  readonly status: number | null;
  readonly responseUrl: string | null;

  constructor(code: ConnectionErrorCode, message: string | LocalizedMessage, options: ConnectionErrorOptions = {}) {
    super(redactConnectionText(resolveMessage(options.safeMessage ?? message)));
    bindLocalizedErrorMessage(this, options.safeMessage ?? message, redactConnectionText);
    this.name = 'ConnectionError';
    this.code = code;
    this.status = options.status ?? null;
    this.responseUrl = options.responseUrl ? redactUrl(options.responseUrl) : null;
    if (options.cause !== undefined) {
      // ES2022's Error.cause is not present in every React Native runtime's
      // type library, but retaining it is useful for diagnostics without
      // exposing it through JSON/UI.
      Object.defineProperty(this, 'cause', { configurable: true, value: options.cause });
    }
  }
}

const SECRET_ASSIGNMENT = /((?:access[_-]?token|refresh[_-]?token|token|secret|key|password))(\s*["']?\s*[:=]\s*["']?)[^"'&,\s}\]]+/gi;
const BEARER_PATTERN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;

export function redactConnectionText(value: string): string {
  return value
    .replace(SECRET_ASSIGNMENT, '$1$2[redacted]')
    .replace(BEARER_PATTERN, 'Bearer [redacted]')
    .replace(/(sb_(?:publishable|secret)_)[A-Za-z0-9_-]+/gi, '$1[redacted]')
    .slice(0, 500);
}

/** Strip query/fragment/userinfo before a URL enters an error or log. */
export function redactUrl(value: string): string {
  try {
    const parsed = new URL(value);
    parsed.username = '';
    parsed.password = '';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString().replace(/\/$/, '');
  } catch {
    return redactConnectionText(value).replace(/[?#].*$/, '').slice(0, 500);
  }
}

export function asConnectionError(error: unknown, fallback: string | LocalizedMessage = localizedMessage('errors:connectServerFailed')): ConnectionError {
  if (error instanceof ConnectionError) return error;
  if (error instanceof Error && error.name === 'AbortError') {
    return new ConnectionError('discovery_cancelled', localizedMessage('errors:connectionCancelled'), { cause: error });
  }
  return new ConnectionError(
    'discovery_network',
    error instanceof Error ? error.message : fallback,
    { cause: error },
  );
}
