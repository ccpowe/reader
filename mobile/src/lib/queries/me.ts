/**
 * 我的's commands (react-guidelines §4): profile name, avatar, and the translation setting.
 * The password change lives in the session store (it replaces the tokens).
 *
 * Every command belongs to the account that started it: right before it sends, and before it writes
 * the cache, it checks that this account is still the signed-in one; otherwise it stops quietly.
 */
import type { MeResponse, PreferenceResponse, UpdatePreferenceRequest } from '../api/schema.gen';
import { isApiError } from '../api/errors';
import { queryClient, session } from '../app-session';
import { identityOf } from '../connection/session';
import { translationPreferenceQuery } from './boards';
import { meQuery, UnknownOutcome } from './today';

/** Signed out or into another account since the command started. */
export class AccountChanged extends Error {
  constructor() {
    super('account changed');
    this.name = 'AccountChanged';
  }
}

function assertCurrent(identity: string) {
  if (identityOf(session.getSnapshot()) !== identity) throw new AccountChanged();
}

/** One profile write at a time; each cancels profile reads that started before it. */
let profileQueue: Promise<unknown> = Promise.resolve();

function profileWrite(identity: string, send: () => Promise<MeResponse>): Promise<void> {
  const meKey = meQuery(identity).queryKey;
  const run = profileQueue
    .catch(() => undefined)
    .then(async () => {
      assertCurrent(identity);
      // An older read finishing after this write would bring back what it replaced.
      await queryClient.cancelQueries({ queryKey: meKey });
      const me = await send();
      assertCurrent(identity);
      queryClient.setQueryData(meKey, me);
    });
  profileQueue = run;
  return run;
}

export function updateDisplayName(identity: string, displayName: string | null): Promise<void> {
  return profileWrite(identity, () =>
    session.api().request<MeResponse>('/me', { method: 'PATCH', body: { display_name: displayName } }),
  );
}

/** Upload a picked image (a local file URI) as the avatar: JPEG, PNG or WebP, at most 5 MiB. */
export function uploadAvatar(identity: string, uri: string, mimeType: string): Promise<void> {
  return profileWrite(identity, async () => {
    const image = await (await fetch(uri)).blob();
    assertCurrent(identity);
    return session
      .api()
      .request<MeResponse>('/me/avatar', { method: 'PUT', body: image, contentType: mimeType, timeoutMs: 60_000 });
  });
}

export function removeAvatar(identity: string): Promise<void> {
  return profileWrite(identity, () => session.api().request<MeResponse>('/me/avatar', { method: 'DELETE' }));
}

/**
 * Everything whose text depends on the translation setting. Reset, not just invalidated: in-flight
 * reads under the old setting are cancelled and nothing of it stays on screen while re-reading.
 */
async function resetTranslatedData(identity: string) {
  for (const part of ['board-translations', 'board', 'feed', 'saved', 'article']) {
    await queryClient.resetQueries({ queryKey: [identity, part] });
  }
}

let preferenceQueue: Promise<unknown> = Promise.resolve();

/**
 * Change the translation setting. Not shown before the server accepts it (a list must never ask for
 * translations of a setting that is not in force); one change at a time. Then the server's setting
 * (with its engine fingerprint) is installed and translated data is re-read. A definite "no" is
 * thrown as is; no answer re-reads the setting and throws UnknownOutcome.
 */
export function setTranslationPreference(identity: string, change: UpdatePreferenceRequest): Promise<void> {
  const preferenceKey = translationPreferenceQuery(identity).queryKey;
  const run = preferenceQueue
    .catch(() => undefined)
    .then(async () => {
      assertCurrent(identity);
      await queryClient.cancelQueries({ queryKey: preferenceKey });
      let saved: PreferenceResponse;
      try {
        saved = await session.api().request<PreferenceResponse>('/me/translation-preference', { method: 'PATCH', body: change });
      } catch (error) {
        if (isApiError(error) && error.problem.kind === 'http') throw error;
        assertCurrent(identity);
        void queryClient.invalidateQueries({ queryKey: preferenceKey });
        void resetTranslatedData(identity);
        throw new UnknownOutcome();
      }
      assertCurrent(identity);
      queryClient.setQueryData(preferenceKey, saved);
      void resetTranslatedData(identity); // re-reading is not part of saving
    });
  preferenceQueue = run;
  return run;
}
