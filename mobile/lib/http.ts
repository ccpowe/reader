import { i18n } from '../i18n';
import { bindLocalizedErrorMessage, localizedMessage, resolveMessage, type LocalizedMessage } from '../i18n/message';
import { redactConnectionText } from './connection/errors';

type HttpResponseErrorOptions = {
  retryAfterMs?: number | null;
  retryable: boolean;
  status: number;
};

export class HttpResponseError extends Error {
  readonly retryAfterMs: number | null;
  readonly retryable: boolean;
  readonly status: number;

  constructor(message: string | LocalizedMessage, options: HttpResponseErrorOptions) {
    super(resolveMessage(message));
    bindLocalizedErrorMessage(this, message);
    this.name = 'HttpResponseError';
    this.retryAfterMs = options.retryAfterMs ?? null;
    this.retryable = options.retryable;
    this.status = options.status;
  }
}

/** Resolve the wrapper and its app-owned label whenever retained errors render. */
function localizedHttpResponseError(message: () => string, options: HttpResponseErrorOptions): HttpResponseError {
  const error = new HttpResponseError(message(), options);
  Object.defineProperty(error, 'message', { configurable: true, enumerable: false, get: message });
  return error;
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function retryAfterMs(response: Response): number | null {
  const value = response.headers.get('retry-after');
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(seconds * 1_000, 0);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(date - Date.now(), 0) : null;
}

export function httpResponseError(
  response: Response,
  label: string | LocalizedMessage,
  detail?: unknown,
): HttpResponseError {
  const status = response.status;
  return localizedHttpResponseError(
    () => {
      const detailText = formatErrorDetail(detail);
      const labelText = resolveMessage(label);
      return detailText
        ? redactConnectionText(i18n.t('errors:httpFailureDetail', { label: labelText, status, detail: detailText }))
        : i18n.t('errors:httpFailure', { label: labelText, status });
    },
    {
      retryAfterMs: retryAfterMs(response),
      retryable: isRetryableStatus(response.status),
      status: response.status,
    },
  );
}

function formatErrorDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail.trim().slice(0, 500) || null;
  if (Array.isArray(detail)) {
    const messages = detail
      .slice(0, 3)
      .map(formatErrorDetailItem)
      .filter((message): message is string => Boolean(message));
    return messages.length ? messages.join(i18n.t('errors:detailSeparator')).slice(0, 500) : null;
  }
  return formatErrorDetailItem(detail);
}

function formatErrorDetailItem(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object') return null;
  const item = detail as Record<string, unknown>;
  const message = typeof item.msg === 'string'
    ? item.msg.trim()
    : typeof item.message === 'string'
      ? item.message.trim()
      : typeof item.detail === 'string'
        ? item.detail.trim()
        : '';
  const location = Array.isArray(item.loc)
    ? item.loc
      .filter((part): part is string | number => (
        typeof part === 'string' || typeof part === 'number'
      ))
      .join('.')
    : '';
  if (location && message) return `${location}: ${message}`;
  return message || location || null;
}

/** Read a JSON API response without assuming an intermediary returned JSON. */
export async function readJsonResponse<T = any>(
  response: Response,
  label: string | LocalizedMessage = localizedMessage('errors:backend'),
): Promise<T> {
  const status = response.status;
  const body = await response.text();
  if (!body.trim()) {
    if (!response.ok) {
      throw localizedHttpResponseError(() => i18n.t('errors:httpEmptyResponse', { label: resolveMessage(label), status }), {
        retryAfterMs: retryAfterMs(response),
        retryable: isRetryableStatus(response.status),
        status: response.status,
      });
    }
    return null as T;
  }
  try {
    return JSON.parse(body) as T;
  } catch {
    throw localizedHttpResponseError(() => i18n.t('errors:httpNonJsonResponse', { label: resolveMessage(label), status }), {
      retryAfterMs: retryAfterMs(response),
      retryable: isRetryableStatus(response.status),
      status: response.status,
    });
  }
}
