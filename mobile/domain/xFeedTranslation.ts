import type {
  FeedItem,
  TranslationSegment,
  TranslationSegmentResult,
  TranslationStatus,
} from '../lib/api';

export type XFeedTranslationPart = 'body' | 'reference';

export type XFeedTranslationRecord = {
  result: TranslationSegmentResult;
  sourceText: string;
};

export type XFeedCardTranslation = {
  bodyText?: string;
  referenceText?: string;
  retryable: boolean;
};

export function xFeedTranslationSegmentId(contentId: string, part: XFeedTranslationPart): string {
  return `x-feed:${contentId}:${part}`;
}

/** Return only text currently rendered by the compact X card. */
export function xFeedTranslationSegments(item: FeedItem): TranslationSegment[] {
  if (item.source_kind !== 'x') return [];
  const preview = item.x_preview;
  const segments: TranslationSegment[] = [];
  if (!(preview?.is_repost && preview.repost)) {
    const text = (preview?.text || item.excerpt || item.title).trim();
    if (text) {
      segments.push({
        purpose: 'paragraph',
        segment_id: xFeedTranslationSegmentId(item.content_id, 'body'),
        text,
      });
    }
  }
  const reference = preview?.repost || preview?.quote;
  if (reference?.availability === 'available') {
    const text = reference.text?.trim();
    if (text) {
      segments.push({
        purpose: 'paragraph',
        segment_id: xFeedTranslationSegmentId(item.content_id, 'reference'),
        text,
      });
    }
  }
  return segments;
}

function isFailed(status: TranslationStatus): boolean {
  return status === 'failed' || status === 'cancelled' || status === null;
}

export function xFeedCardTranslation(
  item: FeedItem,
  records: ReadonlyMap<string, XFeedTranslationRecord>,
  timedOutSegmentIds: ReadonlySet<string>,
  transportFailed: boolean,
): XFeedCardTranslation | undefined {
  const segments = xFeedTranslationSegments(item);
  if (!segments.length) return undefined;
  let bodyText: string | undefined;
  let referenceText: string | undefined;
  let retryable = transportFailed;
  for (const segment of segments) {
    const record = records.get(segment.segment_id);
    const current = record?.sourceText === segment.text ? record.result : undefined;
    if (timedOutSegmentIds.has(segment.segment_id) || (current && isFailed(current.translation_status))) {
      retryable = true;
    }
    if (current?.translation_status !== 'succeeded' || !current.translated_text?.trim()) continue;
    if (segment.segment_id.endsWith(':body')) bodyText = current.translated_text.trim();
    else referenceText = current.translated_text.trim();
  }
  return { bodyText, referenceText, retryable };
}
