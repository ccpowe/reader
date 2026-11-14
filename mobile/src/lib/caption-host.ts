/**
 * The app's side of the YouTube caption runtime (injected/youtubeTranslation.ts, injected into the
 * embedded player): the runtime reads the video's caption track and posts the caption list around
 * the playing moment; this host translates what it plans (POST /translations, purpose `caption`),
 * returns results, and passes on seek / mode / list-window commands.
 *
 * - Every message must carry this host's random channel token and the navigation id first seen
 *   after the document loaded (the runtime has no handshake; a new document starts over).
 * - `media_epoch` grows when the video or caption track changes; a higher one resets everything,
 *   a lower one is stale and ignored. Results go back tagged with the epoch they belong to.
 * - Two independent translation modes, as in the old app: the player overlay (the 双语 button the
 *   runtime adds to the player) and the caption list under the player (the floating tool).
 *
 * Pure apart from the injected `inject`, `send` and timers: runs under `node --test`.
 */
import { TranslationScheduler, type PlanBatch, type SchedulerStatus, type TranslationAnswer } from './translation-scheduler.ts';
import type { TranslationMode } from './web-translation-host.ts';
import { jsValue } from './web-translation-host.ts';
import { t } from './i18n/index.ts';

export type Caption = { id: string; startMs: number; endMs: number; source: string; translated: string | null };

export type CaptionTimeline = {
  captions: Caption[];
  currentId: string | null;
  hasPrevious: boolean;
  hasNext: boolean;
};

export type CaptionState = {
  /** waiting: nothing seen yet; detected: the track is read; unavailable: the video has none */
  captions: 'waiting' | 'detected' | 'unavailable';
  failed: number;
  scheduler: SchedulerStatus;
};

export type CaptionBootstrapConfig = {
  channelToken: string;
  initialMode: TranslationMode;
  initialTimelineMode: TranslationMode;
  /** the YouTube web page also carries the web runtime, which owns the default name */
  bridgeName?: '__readerCaptionBridge';
  interfaceMessages: { disableBilingualCaptions: string; enableBilingualCaptions: string; bilingual: string };
};

export type CaptionHostDeps = {
  inject?: (script: string) => void;
  bootstrap: (config: CaptionBootstrapConfig) => string;
  send: (texts: string[], signal: AbortSignal) => Promise<TranslationAnswer[]>;
  onState: (state: CaptionState) => void;
  onTimeline: (timeline: CaptionTimeline) => void;
  /** the player's 双语 button was pressed: the route decides and calls setMode */
  onModeRequest: (mode: TranslationMode) => void;
  mode: TranslationMode;
  timelineMode: TranslationMode;
  /** set on the YouTube web page, where the web runtime has the default bridge */
  bridgeName?: '__readerCaptionBridge';
  random?: () => string;
  setTimer?: (run: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
  now?: () => number;
};

const MAX_MESSAGE_CHARS = 100_000;
const MAX_CUES = 180;
const MAX_TIMELINE_CHARS = 40_000;
const MAX_SEGMENT_CHARS = 8_000;
const MAX_SEGMENTS = 5_000;

export const EMPTY_TIMELINE: CaptionTimeline = { captions: [], currentId: null, hasPrevious: false, hasNext: false };

/** "1:05", "1:02:03" */
export function captionTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const seconds = String(total % 60).padStart(2, '0');
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`;
}

type Envelope = Record<string, unknown>;

function randomId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
}

export class CaptionHost {
  private readonly deps: CaptionHostDeps;
  private readonly token: string;
  private readonly scheduler: TranslationScheduler;
  private target: ((script: string) => void) | null = null;
  private mode: TranslationMode;
  private timelineMode: TranslationMode;
  private readonly bridge: string;
  private navigationId: string | null = null;
  /** navigation ids of documents that were replaced: their late messages must not bind again */
  private retired = new Set<string>();
  private epoch: number | null = null;
  private captions: CaptionState['captions'] = 'waiting';
  private failed = new Set<string>();
  /** segment id → text, for this epoch */
  private accepted = new Map<string, string>();
  private disposed = false;
  private lastState = '';

  constructor(deps: CaptionHostDeps) {
    this.deps = deps;
    this.mode = deps.mode;
    this.timelineMode = deps.timelineMode;
    this.token = `reader-${(deps.random ?? randomId)()}`;
    this.bridge = `window.${deps.bridgeName ?? '__readerTranslationBridge'}`;
    this.scheduler = new TranslationScheduler({
      send: deps.send,
      deliver: (results) => {
        if (this.disposed || this.epoch === null) return;
        for (const r of results) {
          if (r.translation_status === 'failed') this.failed.add(r.segment_id);
          else this.failed.delete(r.segment_id);
        }
        this.inject(`${this.bridge}?.applyTranslations(${jsValue(results)},${this.epoch});true;`);
        this.report();
      },
      onStatus: () => this.report(),
      now: deps.now,
      setTimer: deps.setTimer,
      clearTimer: deps.clearTimer,
      // captions are short and the player asks a few at a time
      maxConcurrent: 2,
    });
  }

  attach(inject: ((script: string) => void) | null): void {
    this.target = inject;
  }

  private inject(script: string) {
    if (!this.disposed) (this.deps.inject ?? this.target)?.(script);
  }

  script(interfaceMessages: CaptionBootstrapConfig['interfaceMessages']): string {
    return this.deps.bootstrap({
      channelToken: this.token,
      initialMode: this.mode,
      initialTimelineMode: this.timelineMode,
      ...(this.deps.bridgeName ? { bridgeName: this.deps.bridgeName } : {}),
      interfaceMessages,
    });
  }

  state(): CaptionState {
    return { captions: this.captions, failed: this.failed.size, scheduler: this.scheduler.status() };
  }

  /** A new document: the runtime will name a new navigation id. */
  loadStart(): void {
    if (this.disposed) return;
    if (this.navigationId) this.retired.add(this.navigationId);
    this.navigationId = null;
    this.epoch = null;
    this.resetWork();
    this.captions = 'waiting';
    this.deps.onTimeline(EMPTY_TIMELINE);
    this.report();
  }

  /**
   * The load finished. If no new document spoke up, this was a navigation inside the same document
   * (Android reports history changes as loads too): its navigation id may bind again.
   */
  loadEnd(): void {
    if (this.navigationId === null) this.retired.clear();
  }

  setMode(mode: TranslationMode): void {
    if (this.disposed || mode === this.mode) return;
    this.mode = mode;
    this.inject(`${this.bridge}?.setMode(${jsValue(mode)});true;`);
    this.afterModeChange();
  }

  setTimelineMode(mode: TranslationMode): void {
    if (this.disposed || mode === this.timelineMode) return;
    this.timelineMode = mode;
    this.inject(`${this.bridge}?.setTimelineMode?.(${jsValue(mode)});true;`);
    this.afterModeChange();
  }

  seekTo(ms: number): void {
    if (Number.isFinite(ms)) this.inject(`${this.bridge}?.seekTo?.(${Math.max(0, Math.round(ms))});true;`);
  }

  /** The caption rows on screen: the runtime translates these first. */
  setVisible(ids: readonly string[]): void {
    if (this.epoch === null) return;
    this.inject(`${this.bridge}?.setTimelineVisibleSegments?.(${jsValue(ids.slice(0, 20))},${this.epoch});true;`);
  }

  /** The list reached its top or bottom: the runtime moves its window of captions. */
  moveWindow(direction: 'previous' | 'next', firstId: string): void {
    if (this.epoch === null) return;
    this.inject(`${this.bridge}?.setTimelineWindow?.(${jsValue(direction)},${jsValue(firstId)},${this.epoch});true;`);
  }

  retry(): void {
    if (this.disposed) return;
    this.scheduler.retryFailed();
    this.failed.clear();
    this.inject(`${this.bridge}?.retryFailed?.();true;`);
    this.report();
  }

  dispose(): void {
    if (this.disposed) return;
    this.scheduler.reset();
    this.disposed = true;
  }

  message(raw: unknown): void {
    if (this.disposed || typeof raw !== 'string' || raw.length > MAX_MESSAGE_CHARS) return;
    let envelope: Envelope;
    try {
      envelope = JSON.parse(raw) as Envelope;
    } catch {
      return;
    }
    if (!envelope || typeof envelope !== 'object' || envelope.channel_token !== this.token) return;
    const nav = envelope.navigation_id;
    if (typeof nav !== 'string' || nav.length < 8 || nav.length > 120) return;
    if (this.navigationId === null && !this.retired.has(nav)) this.navigationId = nav;
    if (nav !== this.navigationId) return;
    const epoch = envelope.media_epoch;
    if (typeof epoch !== 'number' || !Number.isSafeInteger(epoch) || epoch < 0) return;
    if (this.epoch !== null && epoch < this.epoch) return;
    if (epoch !== this.epoch) {
      // Another video or caption track: nothing from the previous one applies.
      this.epoch = epoch;
      this.resetWork();
      this.captions = 'waiting';
      this.deps.onTimeline(EMPTY_TIMELINE);
    }

    switch (envelope.type) {
      case 'reader_translation_status':
        if (envelope.state === 'captions_unavailable' && this.captions !== 'detected') this.captions = 'unavailable';
        if (envelope.state === 'captions_detected') this.captions = 'detected';
        this.report();
        return;
      case 'reader_translation_caption_timeline':
        return this.onTimeline(envelope, nav);
      case 'reader_translation_mode_request':
        if (envelope.mode === 'original' || envelope.mode === 'bilingual') this.deps.onModeRequest(envelope.mode);
        return;
      case 'reader_translation_batch_plan':
        return this.onPlan(envelope, nav);
    }
  }

  private onTimeline(envelope: Envelope, nav: string) {
    if (!Array.isArray(envelope.captions)) return;
    const captions: Caption[] = [];
    let chars = 0;
    for (const raw of envelope.captions.slice(0, MAX_CUES)) {
      const c = raw as Envelope | null;
      if (!c || typeof c.segment_id !== 'string' || c.segment_id.length > 160 || !c.segment_id.includes(nav)) continue;
      const source = typeof c.source_text === 'string' ? c.source_text.trim() : '';
      const translated =
        c.translated_text === null ? null : typeof c.translated_text === 'string' ? c.translated_text.trim() : undefined;
      const start = c.start_ms;
      const end = c.end_ms;
      if (
        !source ||
        source.length > MAX_SEGMENT_CHARS ||
        translated === undefined ||
        (translated?.length ?? 0) > MAX_SEGMENT_CHARS
      )
        continue;
      if (
        typeof start !== 'number' ||
        typeof end !== 'number' ||
        !Number.isFinite(start) ||
        !Number.isFinite(end) ||
        start < 0 ||
        end < start
      )
        continue;
      chars += source.length + (translated?.length ?? 0);
      if (chars > MAX_TIMELINE_CHARS) break;
      captions.push({ id: c.segment_id, startMs: start, endMs: end, source, translated: translated || null });
    }
    const current = envelope.current_segment_id;
    this.deps.onTimeline({
      captions,
      currentId: typeof current === 'string' && captions.some((c) => c.id === current) ? current : null,
      hasPrevious: envelope.has_previous === true,
      hasNext: envelope.has_next === true,
    });
  }

  private onPlan(envelope: Envelope, nav: string) {
    if ((this.mode === 'original' && this.timelineMode === 'original') || !Array.isArray(envelope.batches)) return;
    const plan: PlanBatch[] = [];
    for (const raw of envelope.batches.slice(0, 20)) {
      const batch = raw as Envelope | null;
      if (
        !batch ||
        typeof batch.id !== 'string' ||
        batch.id.length > 160 ||
        typeof batch.window_id !== 'string' ||
        batch.window_id.length > 160
      )
        continue;
      if ((batch.priority !== 'urgent' && batch.priority !== 'prefetch') || !Array.isArray(batch.segments)) continue;
      const segments: PlanBatch['segments'] = [];
      for (const rawSegment of batch.segments.slice(0, 5)) {
        const segment = rawSegment as Envelope | null;
        if (!segment || typeof segment.segment_id !== 'string' || typeof segment.text !== 'string') continue;
        const id = segment.segment_id;
        const text = segment.text.trim();
        if (id.length > 160 || !id.includes(nav) || !text || text.length > MAX_SEGMENT_CHARS) continue;
        if (!this.accepted.has(id) && this.accepted.size >= MAX_SEGMENTS) continue;
        this.accepted.set(id, text);
        segments.push({ id, text });
      }
      if (segments.length) plan.push({ id: batch.id, priority: batch.priority, windowId: batch.window_id, segments });
    }
    this.scheduler.enqueue(plan);
  }

  private afterModeChange() {
    if (this.mode === 'original' && this.timelineMode === 'original') {
      this.scheduler.reset();
      this.failed.clear();
    }
    this.report();
  }

  private resetWork() {
    this.scheduler.reset();
    this.accepted.clear();
    this.failed.clear();
  }

  private report() {
    if (this.disposed) return;
    const state = this.state();
    const signature = JSON.stringify(state);
    if (signature === this.lastState) return;
    this.lastState = signature;
    this.deps.onState(state);
  }
}

/** One line about caption translation for the problem bar, or null. */
export function captionProblem(state: CaptionState, translating: boolean): { message: string; retry: boolean } | null {
  if (!translating) return null;
  const { blocked } = state.scheduler;
  const text = t().reader.translation;
  if (blocked?.kind === 'unavailable') return { message: text.unavailable, retry: true };
  if (blocked?.kind === 'limit') return { message: text.limit, retry: false };
  if (state.failed > 0) return { message: text.captionsFailed(state.failed), retry: true };
  if (state.scheduler.held > 0) return { message: text.held, retry: false };
  return null;
}
