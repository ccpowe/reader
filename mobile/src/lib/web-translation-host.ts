/**
 * The app's side of the web page runtime (domain/webTranslationRuntime.ts, injected into the
 * WebView): it proves which document is live, takes the page's translation plans, returns results,
 * and asks the page for its article (reader mode extracted from the page).
 *
 * The page is untrusted: any script on it can call `ReactNativeWebView.postMessage`. So every
 * message must carry this host's random channel token and the document identity the host
 * confirmed in a handshake (a nonce the host injected, echoed back); sizes and shapes are checked
 * before anything is used. Results go back only to the confirmed document epoch.
 *
 *   load-start / load-end → handshake again (the document may have changed)
 *   handshake(nonce)      → the page names its navigation id + epoch; host confirms the epoch
 *   handshake_ready       → verified: plans, status and extraction results are accepted
 *   batch_plan            → the scheduler translates; results are injected as applyTranslations
 *
 * Pure apart from the injected `inject`, `send` and timers: runs under `node --test`.
 */
import type { TranslatedItem } from './api/schema.gen.ts';
import { TranslationScheduler, type PlanBatch, type SchedulerStatus, type SegmentResult } from './translation-scheduler.ts';

export type TranslationMode = 'original' | 'bilingual';

/** An article the page extracted (Readability), still untrusted HTML: the runtime purifies it again when mounting. */
export type ExtractedArticle = {
  url: string;
  title: string;
  byline: string | null;
  lang: string | null;
  html: string;
  text: string;
};

export type ExtractResult =
  { ok: true; article: ExtractedArticle } | { ok: false; reason: 'unavailable' | 'too_large' | 'changed' | 'timeout' };

export type HostState = {
  /** the live document answered the handshake */
  verified: boolean;
  /** the handshake never completed (the runtime did not start on this page) */
  stalled: boolean;
  /** segments the page found */
  found: number;
  /** segments that failed to translate or to render */
  failed: number;
  /** the page stopped planning: this document is too large to translate whole */
  budgetExhausted: boolean;
  scheduler: SchedulerStatus;
};

export type BootstrapConfig = {
  channelToken: string;
  initialMode: TranslationMode;
  profile?: 'reader';
  readerContent?: { html: string; url: string };
};

export type HostDeps = {
  /** run script in the WebView; or set later with `attach` (the WebView mounts after the host) */
  inject?: (script: string) => void;
  /** the bootstrap script for this config (domain/webTranslation.ts createWebTranslationScript) */
  bootstrap: (config: BootstrapConfig) => string;
  send: (texts: string[], signal: AbortSignal) => Promise<TranslatedItem[]>;
  onState: (state: HostState) => void;
  mode: TranslationMode;
  profile?: 'reader';
  readerContent?: { html: string; url: string };
  random?: () => string;
  setTimer?: (run: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
  now?: () => number;
};

const MAX_MESSAGE_CHARS = 100_000;
export const MAX_EXTRACTED_BYTES = 512 * 1024;
const MAX_SEGMENTS = 5_000;
const MAX_SEGMENT_CHARS = 8_000;
const MAX_DOCUMENT_CHARS = 750_000;
const HANDSHAKE_ATTEMPTS = 5;
const HANDSHAKE_INTERVAL_MS = 1_000;
const EXTRACT_TIMEOUT_MS = 5_000;
const BRIDGE = 'window.__readerTranslationBridge';

/** A JS literal safe inside an injected script (no `</script>`, no line separators). */
export function jsValue(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

/** UTF-8 size without TextEncoder (Hermes). */
export function utf8Length(value: string): number {
  let bytes = 0;
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < value.length) {
      const next = value.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4;
        i++;
      } else bytes += 3;
    } else bytes += 3;
  }
  return bytes;
}

const httpUrl = (value: unknown): value is string =>
  typeof value === 'string' && /^https?:\/\//i.test(value) && value.length <= 4096;
const identity = (value: unknown): value is string => typeof value === 'string' && value.length >= 8 && value.length <= 120;

export function isExtractedArticle(value: unknown, url: string): value is ExtractedArticle {
  if (!value || typeof value !== 'object') return false;
  const a = value as Record<string, unknown>;
  return (
    a.url === url &&
    httpUrl(url) &&
    typeof a.title === 'string' &&
    a.title.length <= 2000 &&
    (a.byline === null || (typeof a.byline === 'string' && a.byline.length <= 2000)) &&
    (a.lang === null || (typeof a.lang === 'string' && /^[a-zA-Z0-9-]{1,50}$/.test(a.lang))) &&
    typeof a.html === 'string' &&
    a.html.length > 0 &&
    a.html.length <= MAX_EXTRACTED_BYTES &&
    typeof a.text === 'string' &&
    a.text.trim().length > 0 &&
    a.text.length <= MAX_EXTRACTED_BYTES
  );
}

type Envelope = Record<string, unknown>;

function randomId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
}

export class WebTranslationHost {
  private readonly deps: HostDeps;
  private readonly token: string;
  private readonly scheduler: TranslationScheduler;
  private readonly setTimer: (run: () => void, ms: number) => unknown;
  private readonly clearTimer: (timer: unknown) => void;
  private mode: TranslationMode;
  private navigationId: string | null = null;
  private epoch: string | null = null;
  private url: string | null = null;
  private verified = false;
  private stalled = false;
  private challenge: string | null = null;
  private handshakeTimer: unknown = null;
  private found = 0;
  private failedSegments = new Set<string>();
  private budgetExhausted = false;
  /** segment id → text, for this document epoch */
  private accepted = new Map<string, string>();
  private acceptedChars = 0;
  private extraction: {
    id: string;
    revision: number;
    epoch: string;
    url: string;
    timer: unknown;
    resolve: (r: ExtractResult) => void;
  } | null = null;
  private revision = 0;
  private disposed = false;
  private lastState = '';

  constructor(deps: HostDeps) {
    this.deps = deps;
    this.mode = deps.mode;
    this.token = `reader-${(deps.random ?? randomId)()}`;
    this.setTimer = deps.setTimer ?? ((run, ms) => setTimeout(run, ms));
    this.clearTimer = deps.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
    this.scheduler = new TranslationScheduler({
      send: deps.send,
      deliver: (results) => this.apply(results),
      onStatus: () => this.report(),
      now: deps.now,
      setTimer: deps.setTimer,
      clearTimer: deps.clearTimer,
    });
  }

  private target: ((script: string) => void) | null = null;

  /** The WebView to inject into (its `ref`), or null when it unmounts. */
  attach(inject: ((script: string) => void) | null): void {
    this.target = inject;
  }

  private inject(script: string) {
    (this.deps.inject ?? this.target)?.(script);
  }

  /** The script to inject after the content loads (and again at load-end; it is idempotent). */
  script(): string {
    return this.deps.bootstrap({
      channelToken: this.token,
      initialMode: this.mode,
      ...(this.deps.profile ? { profile: this.deps.profile } : {}),
      ...(this.deps.readerContent ? { readerContent: this.deps.readerContent } : {}),
    });
  }

  state(): HostState {
    return {
      verified: this.verified,
      stalled: this.stalled,
      found: this.found,
      failed: this.failedSegments.size,
      budgetExhausted: this.budgetExhausted,
      scheduler: this.scheduler.status(),
    };
  }

  loadStart(): void {
    if (this.disposed) return;
    this.endExtraction({ ok: false, reason: 'changed' });
    this.handshake();
  }

  loadEnd(): void {
    if (this.disposed) return;
    // Android's document-start injection can run before the message bridge exists: start again.
    this.inject(this.script());
    this.handshake();
  }

  setMode(mode: TranslationMode): void {
    if (this.disposed || mode === this.mode) return;
    this.mode = mode;
    this.inject(`${BRIDGE}?.setMode(${jsValue(mode)});true;`);
    if (mode === 'original') {
      // Nothing is shown; requests waiting for a viewport nobody reads are dropped.
      this.scheduler.reset();
      this.forgetAccepted();
    } else this.handshake();
    this.report();
  }

  /** Try the failed segments again, or restart a runtime that never answered. */
  retry(): void {
    if (this.disposed) return;
    if (this.stalled) {
      this.inject(this.script());
      this.handshake();
      return;
    }
    this.scheduler.retryFailed();
    this.failedSegments.clear();
    this.inject(`${BRIDGE}?.retryFailed?.();true;`);
    this.report();
  }

  /** Ask the live page for its article. */
  extract(): Promise<ExtractResult> {
    if (this.disposed || !this.verified || !this.epoch || !this.url) return Promise.resolve({ ok: false, reason: 'unavailable' });
    this.endExtraction({ ok: false, reason: 'changed' });
    const id = `reader-${this.token.slice(7, 40).replace(/[^a-z0-9-]/g, '')}-${++this.revision}`;
    const revision = this.revision;
    const epoch = this.epoch;
    const url = this.url;
    return new Promise((resolve) => {
      const timer = this.setTimer(() => {
        if (this.extraction?.id === id) this.endExtraction({ ok: false, reason: 'timeout' });
      }, EXTRACT_TIMEOUT_MS);
      this.extraction = { id, revision, epoch, url, timer, resolve };
      this.inject(`${BRIDGE}?.extractReader?.(${jsValue(id)},${jsValue(epoch)},${revision});true;`);
    });
  }

  dispose(): void {
    if (this.disposed) return;
    this.endExtraction({ ok: false, reason: 'changed' });
    this.disposed = true;
    this.challenge = null;
    if (this.handshakeTimer !== null) this.clearTimer(this.handshakeTimer);
    this.scheduler.reset();
  }

  /** A message from the WebView (`onMessage`'s `nativeEvent.data`). */
  message(raw: unknown): void {
    if (this.disposed || typeof raw !== 'string' || raw.length > MAX_EXTRACTED_BYTES) return;
    let envelope: Envelope;
    try {
      envelope = JSON.parse(raw) as Envelope;
    } catch {
      return;
    }
    if (!envelope || typeof envelope !== 'object') return;
    const big =
      envelope.type === 'reader_extraction_result' ? utf8Length(raw) > MAX_EXTRACTED_BYTES : raw.length > MAX_MESSAGE_CHARS;
    if (big || envelope.channel_token !== this.token || !identity(envelope.navigation_id) || !identity(envelope.document_epoch))
      return;

    switch (envelope.type) {
      case 'reader_translation_navigation_reset':
        if (envelope.navigation_id !== this.navigationId || envelope.document_epoch !== this.epoch) this.handshake();
        return;
      case 'reader_translation_handshake':
        return this.onHandshake(envelope);
      case 'reader_translation_handshake_ready':
        if (!this.challenge || envelope.challenge_nonce !== this.challenge || !this.current(envelope)) return;
        this.challenge = null;
        if (this.handshakeTimer !== null) this.clearTimer(this.handshakeTimer);
        this.handshakeTimer = null;
        this.verified = true;
        this.stalled = false;
        this.url = httpUrl(envelope.document_url) ? envelope.document_url : null;
        this.report();
        return;
    }
    if (!this.verified || !this.current(envelope)) return;

    switch (envelope.type) {
      case 'reader_translation_location_changed':
        if (httpUrl(envelope.document_url)) this.url = envelope.document_url;
        return;
      case 'reader_extraction_result':
        return this.onExtraction(envelope);
      case 'reader_translation_status':
        if (envelope.state === 'budget_exhausted') this.budgetExhausted = true;
        if (typeof envelope.count === 'number' && Number.isFinite(envelope.count))
          this.found = Math.max(0, Math.min(Math.round(envelope.count), 100_000));
        this.report();
        return;
      case 'reader_translation_segment_event':
        return this.onSegmentEvent(envelope);
      case 'reader_translation_scope_disposed':
        return this.onScopeDisposed(envelope);
      case 'reader_translation_batch_plan':
        return this.onPlan(envelope);
    }
  }

  private current(envelope: Envelope): boolean {
    return envelope.navigation_id === this.navigationId && envelope.document_epoch === this.epoch;
  }

  private handshake() {
    if (this.disposed) return;
    if (this.handshakeTimer !== null) this.clearTimer(this.handshakeTimer);
    const nonce = `challenge-${randomId()}`;
    this.challenge = nonce;
    this.verified = false;
    this.stalled = false;
    let attempts = 0;
    const probe = () => {
      if (this.challenge !== nonce || this.disposed) return;
      if (attempts++ >= HANDSHAKE_ATTEMPTS) {
        this.challenge = null;
        this.handshakeTimer = null;
        this.stalled = true;
        this.report();
        return;
      }
      this.inject(`${BRIDGE}?.reconnect?.(${jsValue(nonce)});true;`);
      this.handshakeTimer = this.setTimer(probe, HANDSHAKE_INTERVAL_MS);
    };
    probe();
    this.report();
  }

  private onHandshake(envelope: Envelope) {
    if (!this.challenge || envelope.challenge_nonce !== this.challenge) return;
    const changed = !this.current(envelope);
    this.navigationId = envelope.navigation_id as string;
    this.url = httpUrl(envelope.document_url) ? envelope.document_url : null;
    if (changed) {
      // Another document: nothing planned, translated or extracted for the old one applies.
      this.endExtraction({ ok: false, reason: 'changed' });
      this.epoch = envelope.document_epoch as string;
      this.scheduler.reset();
      this.forgetAccepted();
      this.failedSegments.clear();
      this.budgetExhausted = false;
      this.found = 0;
    }
    this.inject(`${BRIDGE}?.confirmEpoch(${jsValue(this.epoch)},${jsValue(this.challenge)});true;`);
  }

  private onPlan(envelope: Envelope) {
    if (this.mode === 'original' || !Array.isArray(envelope.batches)) return;
    const prefix = this.navigationId ?? '';
    const epoch = this.epoch ?? '';
    const plan: PlanBatch[] = [];
    for (const raw of envelope.batches.slice(0, 20)) {
      if (!raw || typeof raw !== 'object') continue;
      const batch = raw as Envelope;
      if (
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
        if (id.length > 160 || !id.includes(prefix) || !id.includes(epoch)) continue;
        const text = segment.text.trim();
        if (!text || text.length > MAX_SEGMENT_CHARS) continue;
        const known = this.accepted.get(id);
        if (known === undefined && this.accepted.size >= MAX_SEGMENTS) continue;
        const grows = text.length - (known?.length ?? 0);
        if (known !== text && this.acceptedChars + grows > MAX_DOCUMENT_CHARS) continue;
        if (known !== text) this.acceptedChars += grows;
        this.accepted.set(id, text);
        segments.push({ id, text });
      }
      if (segments.length) plan.push({ id: batch.id, priority: batch.priority, windowId: batch.window_id, segments });
    }
    this.scheduler.enqueue(plan);
  }

  private onSegmentEvent(envelope: Envelope) {
    const id = envelope.segment_id;
    if (typeof id !== 'string' || !this.accepted.has(id)) return;
    if (envelope.state === 'failed') this.failedSegments.add(id);
    else if (envelope.state === 'translated' || envelope.state === 'disposed') this.failedSegments.delete(id);
    else return;
    // A result the page could not render (its markers did not match) is asked for again on retry.
    if (envelope.state === 'failed' && envelope.reason === 'placeholder_mismatch') this.scheduler.forget([id]);
    this.report();
  }

  private onScopeDisposed(envelope: Envelope) {
    if (typeof envelope.scope_id !== 'string' || !/^scope-[1-9][0-9]{0,8}$/.test(envelope.scope_id)) return;
    const prefix = `${this.navigationId}:${this.epoch}:${envelope.scope_id}:`;
    const gone: string[] = [];
    for (const [id, text] of this.accepted) {
      if (!id.startsWith(prefix)) continue;
      gone.push(id);
      this.acceptedChars -= text.length;
      this.accepted.delete(id);
      this.failedSegments.delete(id);
    }
    if (gone.length) this.scheduler.forget(gone);
    this.report();
  }

  private onExtraction(envelope: Envelope) {
    const request = this.extraction;
    if (!request || envelope.request_id !== request.id || envelope.document_epoch !== request.epoch) return;
    const url = envelope.document_url;
    if (typeof url !== 'string' || url.split('#')[0] !== request.url.split('#')[0]) return;
    if (isExtractedArticle(envelope.article, url)) this.endExtraction({ ok: true, article: envelope.article });
    else
      this.endExtraction({
        ok: false,
        reason: envelope.error === 'too_large' || envelope.error === 'changed' ? envelope.error : 'unavailable',
      });
  }

  private endExtraction(result: ExtractResult) {
    const request = this.extraction;
    if (!request) return;
    this.extraction = null;
    this.clearTimer(request.timer);
    if (!result.ok && !this.disposed) this.inject(`${BRIDGE}?.cancelReader?.(${jsValue(request.id)},${request.revision});true;`);
    request.resolve(result);
  }

  private forgetAccepted() {
    this.accepted.clear();
    this.acceptedChars = 0;
  }

  private apply(results: SegmentResult[]) {
    if (this.disposed || !this.epoch || results.length === 0) return;
    this.inject(`${BRIDGE}?.applyTranslations(${jsValue(results)},${jsValue(this.epoch)});true;`);
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

/** One line for the problem bar, or null when translation is fine. */
export function translationProblem(state: HostState, mode: TranslationMode): { message: string; retry: boolean } | null {
  if (mode === 'original') return null;
  const { blocked } = state.scheduler;
  if (state.stalled) return { message: '这个网页的翻译没有启动。', retry: true };
  if (blocked?.kind === 'unavailable') return { message: '服务器的翻译暂时不可用。', retry: true };
  if (blocked?.kind === 'limit') return { message: '翻译请求太多，稍后会自动继续。', retry: false };
  if (blocked?.kind === 'network') return { message: `有 ${state.failed || '一些'} 段没翻译成：连不上服务器。`, retry: true };
  if (state.failed > 0) return { message: `有 ${state.failed} 段没翻译成。`, retry: true };
  if (state.scheduler.held > 0) return { message: '翻译服务刚才出错，稍后自动重试。', retry: false };
  if (state.budgetExhausted) return { message: '这个网页太长，只翻译了前面一部分。', retry: false };
  return null;
}
