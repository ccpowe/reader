/**
 * Errors as text people can act on (react-guidelines §5). Every message says what happened and,
 * when there is one, what to do; none shows a raw code. Texts are in the interface language
 * (lib/i18n: errors).
 * Pure: runs under `node --test`.
 */
import { isApiError, type ApiProblem } from './api/errors.ts';
import type { DiscoveryProblem } from './connection/discovery.ts';
import type { ServerUrlProblem } from './connection/url.ts';
import { t } from './i18n/index.ts';

/** "约 42 秒后再试。" / "Try again in about 3 minutes." */
function later(value: number | undefined) {
  const { errors, time } = t();
  if (!value || value <= 0) return errors.tryLater;
  return errors.retryIn(errors.about(value < 60 ? time.seconds(Math.ceil(value)) : time.minutesCount(Math.ceil(value / 60))));
}

export function discoveryMessage(problem: DiscoveryProblem): string {
  const errors = t().errors;
  switch (problem.kind) {
    case 'address':
      return errors.address[problem.problem satisfies ServerUrlProblem];
    case 'unreachable':
      return errors.unreachable;
    case 'timeout':
      return errors.noResponse;
    case 'not-reader':
      return errors.notReader;
    case 'old-protocol':
      return errors.oldProtocol;
    case 'redirected':
      return errors.redirected(problem.to);
    case 'insecure-api':
      return errors.insecure;
    case 'wrong-token':
      return errors.wrongToken;
    case 'not-configured':
      return errors.notConfigured;
    case 'rate-limited':
      return errors.tooManyAttempts(later(problem.retryAfter));
  }
}

export function problemMessage(problem: ApiProblem): string {
  const errors = t().errors;
  switch (problem.kind) {
    case 'network':
      return errors.network;
    case 'timeout':
      return errors.timeout;
    case 'aborted':
      return errors.aborted;
    case 'invalid-response':
      return problem.status >= 500 ? errors.server : errors.unreadable;
    case 'http': {
      if (problem.status === 429) return errors.tooFrequent(later(problem.retryAfter));
      if (problem.code === 'invalid_request' && problem.fields?.length) {
        const fields: Record<string, string> = errors.fields;
        const field = problem.fields.map((f) => f.field.split('.').pop() ?? '').find((name) => fields[name]);
        if (field) return fields[field];
      }
      const codes: Record<string, string> = errors.codes;
      if (codes[problem.code]) return codes[problem.code];
      if (problem.status >= 500) return errors.server;
      return errors.generic;
    }
  }
}

/** Any thrown value as one sentence. */
export function errorMessage(error: unknown): string {
  return isApiError(error) ? problemMessage(error.problem) : t().errors.unknown;
}
