import type { TranslationSegmentResult } from '../lib/api';
import type { XFeedTranslationRecord } from './xFeedTranslation';

const MAX_SEGMENTS = 200;
const memory = new Map<string, XFeedTranslationRecord>();
let activeIdentity = '';

export type XFeedTranslationMemoryRoute = {
  engineFingerprint: string | null;
  serverId: string;
  targetLocale: string;
  userId: string;
};

function identityKey(route: XFeedTranslationMemoryRoute): string {
  return `${route.serverId}\u0000${route.userId}`;
}

function entryKey(route: XFeedTranslationMemoryRoute, segmentId: string, sourceText: string): string {
  return [
    route.serverId,
    route.userId,
    route.targetLocale,
    route.engineFingerprint ?? '',
    segmentId,
    sourceText,
  ].join('\u0000');
}

export function activateXFeedTranslationMemory(route: XFeedTranslationMemoryRoute): void {
  const nextIdentity = identityKey(route);
  if (activeIdentity && activeIdentity !== nextIdentity) memory.clear();
  activeIdentity = nextIdentity;
}

export function readXFeedTranslationMemory(
  route: XFeedTranslationMemoryRoute,
  segmentId: string,
  sourceText: string,
): XFeedTranslationRecord | null {
  activateXFeedTranslationMemory(route);
  const key = entryKey(route, segmentId, sourceText);
  const record = memory.get(key) ?? null;
  if (!record) return null;
  memory.delete(key);
  memory.set(key, record);
  return record;
}

export function writeXFeedTranslationMemory(
  route: XFeedTranslationMemoryRoute,
  sourceText: string,
  result: TranslationSegmentResult,
): void {
  if (result.translation_status !== 'succeeded' || !result.translated_text) return;
  if (
    route.engineFingerprint !== null &&
    result.effective_engine_fingerprint !== route.engineFingerprint
  ) return;
  activateXFeedTranslationMemory(route);
  const key = entryKey(route, result.segment_id, sourceText);
  memory.delete(key);
  memory.set(key, { result, sourceText });
  while (memory.size > MAX_SEGMENTS) {
    const oldest = memory.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    memory.delete(oldest);
  }
}

export function clearXFeedTranslationMemory(): void {
  memory.clear();
  activeIdentity = '';
}
