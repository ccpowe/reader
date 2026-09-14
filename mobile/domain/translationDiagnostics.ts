// Keep this pure module usable in Node-only contract tests as well as Metro.
declare const __DEV__: boolean;

/** Developer tooling only; Metro release bundles set __DEV__ to false. */
export const TRANSLATION_DIAGNOSTICS_ENABLED = typeof __DEV__ !== 'undefined' && __DEV__;

/** Bounded, process-local diagnostics. Never pass source text or credentials here. */
const MAX_EVENTS = 400;
const MAX_BYTES = 128_000;
const MAX_EVENTS_PER_SECOND = 40;
const IDENTIFIER = /^[A-Za-z0-9_.:@+-]+$/;
const CREDENTIAL_SHAPE = /(?:^(?:sk|rk|pk)-(?:proj-|svcacct-)?|^sb_(?:secret|publishable)_|^gh[pousr]_|^github_pat_|^Bearer[:@+.-]|^eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$)/i;
const FIELD_NAMES = new Set([
  'stage', 'level', 'segmentId', 'requestId', 'taskId', 'documentEpoch', 'scopeId',
  'batchId', 'sourceFingerprint', 'revision', 'attempt', 'retryAfterMs', 'cacheAgeMs',
  'displayStatus', 'durationMs', 'reason', 'status', 'cacheSource', 'profileId', 'engineId',
  'runtimeVersion', 'appVersion', 'buildVersion', 'formatVersion', 'expectedTokens',
  'actualTokens', 'missingTokens', 'unknownTokens', 'count', 'failedCount',
  'queuedCount', 'translatedCount', 'detectedCount', 'discardedCount',
  'ruleIndex', 'tagName', 'httpStatus',
]);
const DISPLAY_STATUSES = new Set(['not_inserted', 'mode_hidden', 'host_hidden', 'clipped', 'offscreen', 'partial', 'visible', 'unknown']);
type FieldValue = string | number | boolean | string[];
type DiagnosticEvent = { at: string; event: string; fields: Record<string, FieldValue> };

function safeIdentifier(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 && value.length <= 200 &&
    IDENTIFIER.test(value) && !CREDENTIAL_SHAPE.test(value)
    ? value : undefined;
}

export function createTranslationDiagnostics(now: () => number = Date.now) {
  const events: { value: DiagnosticEvent; bytes: number }[] = [];
  let bytes = 0;
  let detailed = false;
  let dropped = 0;
  let windowStart = -Infinity;
  let windowCount = 0;

  const record = (event: string, input: Record<string, unknown> = {}): boolean => {
    if (typeof event !== 'string' || !/^[a-z][a-z0-9_.-]{0,63}$/.test(event)) return false;
    if (input.level === 'debug' && !detailed) return false;
    const time = now();
    if (time - windowStart >= 1_000 || time < windowStart) {
      windowStart = time;
      windowCount = 0;
    }
    const snapshot = event === 'state_snapshot';
    if (!snapshot && windowCount >= MAX_EVENTS_PER_SECOND) { dropped += 1; return false; }
    const fields: Record<string, FieldValue> = {};
    // Iterate our allowlist rather than arbitrary input keys or nested values.
    for (const key of FIELD_NAMES) {
      const value = input[key];
      if (key === 'displayStatus') {
        if (typeof value === 'string' && DISPLAY_STATUSES.has(value)) fields[key] = value;
      } else if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= Number.MAX_SAFE_INTEGER) {
        fields[key] = value;
      } else if (typeof value === 'boolean') {
        fields[key] = value;
      } else if (key === 'missingTokens' || key === 'unknownTokens') {
        if (Array.isArray(value)) fields[key] = value.slice(0, 16).flatMap((token) => {
          if (typeof token !== 'string' || !/^⟪READER_(?:(?:OPEN|CLOSE)_)?\d{1,5}⟫$/.test(token)) return [];
          return [token];
        });
      } else {
        const identifier = safeIdentifier(value);
        if (identifier !== undefined) fields[key] = identifier;
      }
    }
    const value = { at: new Date(time).toISOString(), event, fields };
    const previous = events.at(-1);
    if (previous && previous.value.event === event &&
        JSON.stringify(previous.value.fields) === JSON.stringify(fields) &&
        time - Date.parse(previous.value.at) < 1_000) { dropped += 1; return false; }
    // ASCII metadata plus bounded Unicode token lists: UTF-16 length * 3 safely
    // overestimates UTF-8 bytes here, also bounding export pretty-print overhead.
    const size = JSON.stringify(value).length * 3;
    while (events.length && (events.length >= MAX_EVENTS || bytes + size > MAX_BYTES)) {
      bytes -= events.shift()!.bytes;
      dropped += 1;
    }
    events.push({ value, bytes: size });
    bytes += size;
    if (!snapshot) windowCount += 1;
    return true;
  };

  return {
    record,
    setDetailed(enabled: boolean) { detailed = enabled; },
    clear() {
      events.length = 0;
      detailed = false;
      bytes = 0;
      dropped = 0;
      windowStart = -Infinity;
      windowCount = 0;
    },
    exportJson() {
      return JSON.stringify({
        schemaVersion: 1,
        exportedAt: new Date(now()).toISOString(),
        detailed,
        droppedEvents: dropped,
        events: events.map((entry) => entry.value),
      }, null, 2);
    },
  };
}

const diagnostics = createTranslationDiagnostics();
export const recordTranslationDiagnostic: typeof diagnostics.record = (event, input) =>
  TRANSLATION_DIAGNOSTICS_ENABLED ? diagnostics.record(event, input) : false;
export const exportTranslationDiagnostics = diagnostics.exportJson;
export const setTranslationDiagnosticsDetailed: typeof diagnostics.setDetailed = (enabled) => {
  if (TRANSLATION_DIAGNOSTICS_ENABLED) diagnostics.setDetailed(enabled);
};
export const clearTranslationDiagnostics = diagnostics.clear;
