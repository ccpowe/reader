/** Live translation of page segments and captions (POST /translations, the user's engine and language). */
import type { TranslatedItem, TranslateRequest, TranslateResponse } from '../api/schema.gen';
import { session } from '../app-session';

/** The server waits a few seconds for results; give it room before calling the request lost. */
const TIMEOUT_MS = 25_000;

export async function translateTexts(
  purpose: TranslateRequest['purpose'],
  texts: string[],
  signal: AbortSignal,
): Promise<TranslatedItem[]> {
  const response = await session.api().request<TranslateResponse>('/translations', {
    method: 'POST',
    body: { purpose, texts } satisfies TranslateRequest,
    signal,
    timeoutMs: TIMEOUT_MS,
  });
  return response.items;
}
