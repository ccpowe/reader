/**
 * What can go wrong with a Reader request, as data. Pages never see raw `fetch` errors: the client
 * turns every failure into one of these, and `user-error` (later) turns them into text.
 * Pure: runs under `node --test`.
 */

export type ApiProblem =
  /** no response at all: offline, DNS, refused, TLS */
  | { kind: 'network' }
  /** no response within the time limit; the server may still have done it */
  | { kind: 'timeout' }
  /** the request was cancelled by its owner (page left, parameters changed); not a failure */
  | { kind: 'aborted' }
  /** the server answered with an error envelope `{ error: { code, message, ... } }` */
  | {
      kind: 'http';
      status: number;
      code: string;
      message: string;
      retryAfter?: number;
      fields?: { field: string; message: string }[];
    }
  /** the server answered, but not with what the contract says */
  | { kind: 'invalid-response'; status: number };

export class ApiError extends Error {
  readonly problem: ApiProblem;

  constructor(problem: ApiProblem) {
    super(problem.kind === 'http' ? `${problem.status} ${problem.code}` : problem.kind);
    this.name = 'ApiError';
    this.problem = problem;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** The HTTP error code, if this is an HTTP error. */
export function errorCode(error: unknown): string | null {
  return isApiError(error) && error.problem.kind === 'http' ? error.problem.code : null;
}

/** Read the server's error envelope; anything else is an invalid response. */
export function problemFromBody(status: number, body: unknown, retryAfterHeader: string | null): ApiProblem {
  const error = (body as { error?: unknown } | null)?.error as
    | { code?: unknown; message?: unknown; retry_after?: unknown; fields?: unknown }
    | undefined;
  if (!error || typeof error.code !== 'string') return { kind: 'invalid-response', status };
  const retryAfter =
    typeof error.retry_after === 'number' ? error.retry_after : retryAfterHeader ? Number(retryAfterHeader) || undefined : undefined;
  const fields = Array.isArray(error.fields)
    ? error.fields
        .filter((f): f is { field: string; message: string } => typeof f?.field === 'string' && typeof f?.message === 'string')
        .map(({ field, message }) => ({ field, message }))
    : undefined;
  return {
    kind: 'http',
    status,
    code: error.code,
    message: typeof error.message === 'string' ? error.message : '',
    ...(retryAfter !== undefined ? { retryAfter } : {}),
    ...(fields && fields.length > 0 ? { fields } : {}),
  };
}

/**
 * Retrying makes sense for these; for the rest the same request gets the same answer.
 * 429 is retried by the caller after `retryAfter`, not blindly.
 */
export function isRetryable(error: unknown): boolean {
  if (!isApiError(error)) return false;
  const p = error.problem;
  return (
    p.kind === 'network' ||
    p.kind === 'timeout' ||
    // a proxy's HTML error page during a restart is as temporary as the server's own 5xx
    ((p.kind === 'http' || p.kind === 'invalid-response') && p.status >= 500 && p.status !== 501)
  );
}
