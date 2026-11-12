/**
 * Translation requests (POST /translations, the user's engine and language): live page segments
 * and captions, and filling in the titles and descriptions a loaded list still lacks.
 *
 * Texts already in the target language (a Chinese page read in Chinese) are answered at once with
 * themselves, without a request; the page runtimes show nothing under a translation that equals
 * its original, and lists show the title as it is.
 */
import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import type { PreferenceResponse, TranslateRequest, TranslateResponse } from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { identityOf } from '../connection/session';
import { alreadyInLanguage } from '../language-script';
import { key } from '../query';
import type { TranslationAnswer } from '../translation-scheduler';

/** The server waits a few seconds for results; give it room before calling the request lost. */
const TIMEOUT_MS = 25_000;
/** A list's missing texts are asked again while pending, a few rounds at most. */
const ROUNDS = 6;

export function translationPreferenceQuery(identity: string) {
  return queryOptions({
    queryKey: key(identity, 'translation-preference'),
    queryFn: ({ signal }) => session.api().request<PreferenceResponse>('/me/translation-preference', { signal }),
    staleTime: 5 * 60_000,
  });
}

/** The language texts are translated into (null when the setting cannot be read: everything is asked for). */
async function targetLocale(): Promise<string | null> {
  const identity = identityOf(session.getSnapshot());
  if (!identity) return null;
  try {
    return (await queryClient.ensureQueryData(translationPreferenceQuery(identity))).target_locale;
  } catch {
    return null;
  }
}

async function request(purpose: TranslateRequest['purpose'], texts: string[], signal: AbortSignal) {
  const locale = await targetLocale();
  const items: (TranslationAnswer | null)[] = texts.map((text) =>
    alreadyInLanguage(text, locale) ? { status: 'succeeded', text, same_as_source: true } : null,
  );
  const asked = texts.filter((_, index) => items[index] === null);
  let fingerprint = '';
  if (asked.length > 0) {
    const response = await session.api().request<TranslateResponse>('/translations', {
      method: 'POST',
      body: { purpose, texts: asked } satisfies TranslateRequest,
      signal,
      timeoutMs: TIMEOUT_MS,
    });
    fingerprint = response.engine_fingerprint;
    let next = 0;
    items.forEach((item, index) => {
      if (item === null) items[index] = response.items[next++] ?? { status: 'failed', error_code: 'missing_result' };
    });
  }
  return { items: items as TranslationAnswer[], fingerprint };
}

/** One round for a page runtime: answers in the same order as `texts` (pending ones it asks again itself). */
export async function translateTexts(purpose: TranslateRequest['purpose'], texts: string[], signal: AbortSignal): Promise<TranslationAnswer[]> {
  return (await request(purpose, texts, signal)).items;
}

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason);
    });
  });
}

/** Ask for texts until each is finished or failed (pending ones again after a pause), a few rounds at most. */
export async function translateAll(purpose: TranslateRequest['purpose'], texts: string[], signal: AbortSignal) {
  const done = new Map<string, string>();
  let fingerprint = '';
  let left = texts;
  for (let round = 0; round < ROUNDS && left.length > 0; round++) {
    if (round > 0) await wait(2_000, signal);
    const pending: string[] = [];
    for (let start = 0; start < left.length; start += 50) {
      const chunk = left.slice(start, start + 50);
      const response = await request(purpose, chunk, signal);
      fingerprint = response.fingerprint || fingerprint;
      response.items.forEach((item, index) => {
        if (item.status === 'succeeded' && item.text?.trim()) done.set(chunk[index], item.text.trim());
        else if (item.status === 'pending') pending.push(chunk[index]);
      });
    }
    left = pending;
  }
  return { done, fingerprint };
}

export type TitleTranslations = { done: Map<string, string>; fingerprint: string };

/**
 * The title translations a loaded feed or saved list lacks (the server had none ready), asked for
 * by purpose. A new page of the list asks again for all of them (finished ones are cache hits);
 * the previous answer stays on screen meanwhile.
 */
export function titleTranslationsQuery(
  identity: string,
  list: unknown,
  missing: { title: string[]; board_title: string[] },
  preference: PreferenceResponse | undefined,
) {
  return queryOptions({
    queryKey: key(
      identity,
      'title-translations',
      list,
      preference?.target_locale ?? null,
      preference?.engine_fingerprint ?? null,
      missing.title,
      missing.board_title,
    ),
    enabled: preference?.enabled === true && (missing.title.length > 0 || missing.board_title.length > 0),
    queryFn: async ({ signal }): Promise<TitleTranslations> => {
      const [titles, boards] = await Promise.all([
        missing.title.length ? translateAll('title', missing.title, signal) : null,
        missing.board_title.length ? translateAll('board_title', missing.board_title, signal) : null,
      ]);
      return {
        done: new Map([...(titles?.done ?? []), ...(boards?.done ?? [])]),
        fingerprint: titles?.fingerprint || boards?.fingerprint || preference?.engine_fingerprint || '',
      };
    },
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    // Translation is a nicety: a failure leaves the original titles, without retry storms.
    retry: false,
  });
}
