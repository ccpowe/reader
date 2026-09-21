import type {
  Ranking,
  RankingItem,
  TranslationSegment,
  TranslationSegmentResult,
} from '../lib/api';

function translatedText(value: string | null | undefined): string | null {
  const normalized = value?.trim();
  return normalized ? value ?? null : null;
}

function hasTranslationState(item: RankingItem): boolean {
  return Boolean(
    translatedText(item.translated_title)
    || translatedText(item.translated_description)
    || item.title_translation_status
    || item.description_translation_status,
  );
}

export function translationResultMatchesRankingRoute(
  result: TranslationSegmentResult,
  targetLocale: string,
  translationEnabled: boolean,
  engineFingerprint: string | null,
): boolean {
  return Boolean(
    translationEnabled
    && engineFingerprint
    && result.translation_locale === targetLocale
    && result.effective_engine_fingerprint === engineFingerprint,
  );
}

function validTranslationItem(
  item: RankingItem,
  targetLocale: string,
  routeMatches: boolean,
): RankingItem {
  if (
    hasTranslationState(item)
    && (!routeMatches || item.translation_locale !== targetLocale)
  ) {
    return {
      ...item,
      translated_title: null,
      title_translation_status: null,
      translated_description: null,
      description_translation_status: null,
      translation_locale: null,
    };
  }
  return item;
}

/** Keep successful in-memory translations when a refresh returns the same originals. */
export function mergeRankingTranslationProjection(
  previous: Ranking | undefined,
  incoming: Ranking,
  targetLocale: string,
  translationEnabled: boolean,
  engineFingerprint: string | null,
): Ranking {
  const incomingRouteMatches = Boolean(
    translationEnabled
    && engineFingerprint
    && incoming.effective_engine_fingerprint === engineFingerprint,
  );
  const previousByKey = new Map(
    (previous?.items ?? []).map((item) => [
      item.translation_key,
      validTranslationItem(
        item,
        targetLocale,
        Boolean(
          translationEnabled
          && engineFingerprint
          && previous?.effective_engine_fingerprint === engineFingerprint,
        ),
      ),
    ]),
  );
  let changed = false;
  const items = incoming.items.map((rawIncoming) => {
    const next = validTranslationItem(rawIncoming, targetLocale, incomingRouteMatches);
    if (next !== rawIncoming) changed = true;
    const prior = previousByKey.get(next.translation_key);
    const incomingItemRouteConflicts = hasTranslationState(rawIncoming)
      && rawIncoming.translation_locale !== targetLocale;
    if (!prior || !incomingRouteMatches || incomingItemRouteConflicts) return next;

    const keepTitle = (
      !translatedText(next.translated_title)
      && !translatedText(rawIncoming.translated_title)
      && next.title === prior.title
      && Boolean(translatedText(prior.translated_title))
    );
    const keepDescription = (
      !translatedText(next.translated_description)
      && !translatedText(rawIncoming.translated_description)
      && next.description === prior.description
      && Boolean(translatedText(prior.translated_description))
    );
    if (!keepTitle && !keepDescription) return next;
    changed = true;
    return {
      ...next,
      ...(keepTitle ? {
        translated_title: prior.translated_title,
        title_translation_status: 'succeeded' as const,
      } : {}),
      ...(keepDescription ? {
        translated_description: prior.translated_description,
        description_translation_status: 'succeeded' as const,
      } : {}),
      translation_locale: targetLocale,
    };
  });
  return changed ? { ...incoming, items } : incoming;
}

/** Build requests only for fields that still have no usable translation. */
export function missingRankingTranslationSegments(
  ranking: Ranking | undefined,
  itemLimit: number,
  targetLocale: string,
  translationEnabled: boolean,
  engineFingerprint: string | null,
): TranslationSegment[] {
  const segments: TranslationSegment[] = [];
  const routeMatches = Boolean(
    translationEnabled
    && engineFingerprint
    && ranking?.effective_engine_fingerprint === engineFingerprint,
  );
  for (const rawItem of (ranking?.items ?? []).slice(0, itemLimit)) {
    const item = validTranslationItem(rawItem, targetLocale, routeMatches);
    if (!translatedText(item.translated_title) && item.title_translation_status !== 'failed') {
      segments.push({
        purpose: 'ranking_title',
        segment_id: `${item.translation_key}:title`,
        text: item.title,
      });
    }
    if (
      item.description
      && !translatedText(item.translated_description)
      && item.description_translation_status !== 'failed'
    ) {
      segments.push({
        purpose: 'ranking_description',
        segment_id: `${item.translation_key}:description`,
        text: item.description,
      });
    }
  }
  return segments;
}
