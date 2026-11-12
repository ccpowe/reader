/**
 * Unsaves waiting for their undo window to close (收藏, queries/saved.ts), per identity and article.
 * Any later save or unsave of that article (setSaved) supersedes the waiting one, so it is
 * cancelled first; a separate module so both sides can reach it without an import cycle.
 */
const waiting = new Map<string, () => void>();

export const pendingKey = (identity: string, articleId: string) => `${identity}\u0000${articleId}`;

export const pendingUnsaves = {
  get: (key: string) => waiting.get(key),
  set: (key: string, cancel: () => void) => void waiting.set(key, cancel),
  delete: (key: string) => void waiting.delete(key),
  /** Cancel the waiting unsave of this article, if any (it is shown again). */
  supersede(identity: string, articleId: string) {
    waiting.get(pendingKey(identity, articleId))?.();
  },
};
