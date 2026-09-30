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

/** Never substitute a summary or a partial translation for the original post. */
export function rankingBodyText(item: RankingItem, translated: boolean): string {
  const original = item.body_text ?? '';
  if (!translated || !item.body_segments?.length) return original;
  if (item.body_segments.some((segment) => segment.text.trim()
    && (segment.translation_status !== 'succeeded' || !segment.translated_text?.trim()))) return original;
  return item.body_segments.map((segment) => {
    if (!segment.text.trim()) return segment.text;
    const translatedSegment = segment.translated_text ?? segment.text;
    const lineEnding = segment.text.match(/(?:\r?\n)+$/)?.[0];
    return lineEnding ? translatedSegment.replace(/(?:\r?\n)+$/, '') + lineEnding : translatedSegment;
  }).join('');
}

function hasTranslationState(item: RankingItem): boolean {
  return Boolean(
    translatedText(item.translated_title)
    || translatedText(item.translated_description)
    || item.title_translation_status
    || item.description_translation_status
    || item.body_segments?.some((segment) => translatedText(segment.translated_text) || segment.translation_status)
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
      body_segments: item.body_segments?.map((segment) => ({
        ...segment, translated_text: null, translation_status: null,
      })),
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
    const keepBody = incoming.kind === 'techtwitter'
      && Boolean(next.body_segments?.length)
      && Boolean(prior.body_segments?.some((segment) => translatedText(segment.translated_text)))
      && next.body_text === prior.body_text
      && next.body_segments?.length === prior.body_segments?.length
      && next.body_segments?.every((segment, index) => (
        segment.segment_id === prior.body_segments?.[index]?.segment_id
        && segment.text === prior.body_segments[index].text
      ));
    if (!keepTitle && !keepDescription && !keepBody) return next;
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
      ...(keepBody ? {
        body_segments: next.body_segments?.map((segment, index) => {
          const old = prior.body_segments?.[index];
          return !translatedText(segment.translated_text) && translatedText(old?.translated_text)
            ? { ...segment, translated_text: old?.translated_text ?? null, translation_status: 'succeeded' as const }
            : segment;
        }),
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
  visibleItemKeys?: ReadonlySet<string>,
): TranslationSegment[] {
  const segments: TranslationSegment[] = [];
  const routeMatches = Boolean(
    translationEnabled
    && engineFingerprint
    && ranking?.effective_engine_fingerprint === engineFingerprint,
  );
  for (const rawItem of (ranking?.items ?? []).slice(0, itemLimit)) {
    if (ranking?.kind === 'techtwitter' && !visibleItemKeys?.has(rawItem.translation_key)) continue;
    const item = validTranslationItem(rawItem, targetLocale, routeMatches);
    if (ranking?.kind === 'techtwitter') {
      for (const segment of item.body_segments ?? []) {
        if (segment.text.trim() && !translatedText(segment.translated_text) && segment.translation_status !== 'failed') {
          segments.push({ purpose: 'paragraph', segment_id: segment.segment_id, text: segment.text });
        }
      }
      continue;
    }
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
