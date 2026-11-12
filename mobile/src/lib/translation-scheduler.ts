/**
 * Live translation requests for one document (a web page or a caption track): the page plans
 * small batches of segments; this sends them to POST /translations and hands each finished result
 * back. Pure apart from the injected `send` and timers: runs under `node --test`.
 *
 * - A few requests at a time; visible batches ("urgent") before prefetch ones.
 * - A new plan replaces queued batches of an older viewport window (scrolled past), never ones
 *   already sent.
 * - `pending` items are asked again a little later (the server keeps working on them).
 * - No answer (offline, timeout, 5xx): the batch is retried a few times with backoff, then fails.
 *   429: waits as long as the server says. 503 translation_unavailable: fails at once.
 * - Finished results are kept, so a segment the page plans again (re-rendered by the site) is
 *   answered without a request; `retryFailed` forgets the failed ones so they are planned again.
 */
import { isApiError, isRetryable } from './api/errors.ts';
import type { TranslatedItem } from './api/schema.gen.ts';

export type Segment = { id: string; text: string };
export type PlanBatch = { id: string; priority: 'urgent' | 'prefetch'; windowId: string; segments: Segment[] };

/** What the page runtime's `applyTranslations` reads. */
export type SegmentResult = {
  segment_id: string;
  source_text: string;
  translation_status: 'succeeded' | 'failed';
  translated_text?: string;
  error_code?: string;
};

export type SchedulerStatus = {
  /** segments waiting for or in a request */
  working: number;
  /** segments freed by retryFailed, waiting for the server's pause to end */
  held: number;
  /** why requests are not going through right now, if they are not */
  blocked: null | { kind: 'unavailable' } | { kind: 'limit'; until: number } | { kind: 'network' };
};

export type SchedulerDeps = {
  send: (texts: string[], signal: AbortSignal) => Promise<TranslatedItem[]>;
  deliver: (results: SegmentResult[]) => void;
  onStatus?: (status: SchedulerStatus) => void;
  now?: () => number;
  setTimer?: (run: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
  maxConcurrent?: number;
};

const POLL_MS = 750;
const MAX_POLLS = 120;
const MAX_RECOVERIES = 3;
const DEFAULT_LIMIT_WAIT_MS = 30_000;
const MAX_LIMIT_WAITS = 5;

/** A batch's waiting and attempts; kept per segment when its batch is dropped, so replanning restarts nothing. */
type Attempts = { notBefore: number; polls: number; recoveries: number; limits: number; held: boolean };
type Queued = Attempts & { batch: PlanBatch };
const FRESH: Attempts = { notBefore: 0, polls: 0, recoveries: 0, limits: 0, held: false };

export class TranslationScheduler {
  private readonly deps: Required<Omit<SchedulerDeps, 'onStatus'>> & Pick<SchedulerDeps, 'onStatus'>;
  private queued = new Map<string, Queued>();
  /** `retired`: segments the page removed while the request was out; their answers are dropped */
  private inflight = new Map<string, { entry: Queued; controller: AbortController; retired: Set<string> }>();
  /** segment id → batch id, for segments queued or in flight */
  private owner = new Map<string, string>();
  /** finished results by segment id, with the text they answer */
  private done = new Map<string, SegmentResult>();
  /** failed segments the server asked to leave alone until then (it pauses a failed text) */
  private retryNotBefore = new Map<string, number>();
  /** attempts of segments whose queued batch was dropped (another viewport) or freed by retryFailed */
  private carried = new Map<string, Attempts>();
  /** after a 429 nothing is sent before this */
  private limitUntil = 0;
  private timer: unknown = null;
  private generation = 0;
  private sequence = 0;
  private blocked: SchedulerStatus['blocked'] = null;
  private lastStatus = '';

  constructor(deps: SchedulerDeps) {
    // `??`, not spread defaults: callers pass their own optional deps through as `undefined`.
    this.deps = {
      ...deps,
      now: deps.now ?? Date.now,
      setTimer: deps.setTimer ?? ((run, ms) => setTimeout(run, ms)),
      clearTimer: deps.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>)),
      maxConcurrent: deps.maxConcurrent ?? 3,
    };
  }

  enqueue(plan: readonly PlanBatch[]): void {
    if (plan.length === 0) return;
    const replay: SegmentResult[] = [];
    // Batches of an older window that have not been sent are no longer what the reader sees.
    const windowId = plan[0].windowId;
    for (const [id, entry] of this.queued) {
      if (entry.batch.windowId !== windowId) this.drop(id);
    }
    for (const requested of plan) {
      const segments: Segment[] = [];
      for (const segment of requested.segments) {
        const known = this.done.get(segment.id);
        if (known && known.source_text === segment.text) {
          replay.push(known);
          continue;
        }
        if (known) this.done.delete(segment.id);
        const ownerId = this.owner.get(segment.id);
        if (ownerId !== undefined) {
          // Already asked for; a now-visible segment moves its queued batch ahead.
          const entry = this.queued.get(ownerId);
          if (entry && requested.priority === 'urgent') entry.batch = { ...entry.batch, priority: 'urgent', windowId };
          continue;
        }
        segments.push(segment);
      }
      if (segments.length === 0) continue;
      const id =
        this.queued.has(requested.id) || this.inflight.has(requested.id) ? `${requested.id}#${++this.sequence}` : requested.id;
      const attempts = { ...FRESH };
      for (const segment of segments) {
        const carried = this.carried.get(segment.id);
        if (!carried) continue;
        this.carried.delete(segment.id);
        attempts.notBefore = Math.max(attempts.notBefore, carried.notBefore);
        attempts.polls = Math.max(attempts.polls, carried.polls);
        attempts.recoveries = Math.max(attempts.recoveries, carried.recoveries);
        attempts.limits = Math.max(attempts.limits, carried.limits);
        attempts.held ||= carried.held && carried.notBefore > this.deps.now();
      }
      this.queued.set(id, { ...attempts, batch: { ...requested, id, segments } });
      for (const segment of segments) this.owner.set(segment.id, id);
    }
    if (replay.length) this.deps.deliver(replay);
    this.pump();
  }

  /** Failed segments are forgotten, so the page's next plan asks for them again. */
  retryFailed(): void {
    for (const [id, result] of this.done) {
      if (result.translation_status !== 'failed') continue;
      this.done.delete(id);
      // Asked again right away, the server would answer "failed" until its pause ends.
      const notBefore = this.retryNotBefore.get(id);
      if (notBefore !== undefined && notBefore > this.deps.now()) this.carried.set(id, { ...FRESH, notBefore, held: true });
      this.retryNotBefore.delete(id);
    }
    if (this.blocked?.kind !== 'limit') this.blocked = null;
    this.pump();
  }

  /** The page removed these segments (a part of the page went away). */
  forget(segmentIds: Iterable<string>): void {
    const gone = new Set(segmentIds);
    for (const id of gone) {
      this.done.delete(id);
      this.retryNotBefore.delete(id);
      this.carried.delete(id);
      this.owner.delete(id);
    }
    for (const [id, entry] of this.queued) {
      entry.batch = { ...entry.batch, segments: entry.batch.segments.filter((s) => !gone.has(s.id)) };
      if (entry.batch.segments.length === 0) this.queued.delete(id);
    }
    // Requests already out: their answers for these are dropped; one with nothing left is cancelled.
    for (const [id, flight] of this.inflight) {
      for (const segment of flight.entry.batch.segments) if (gone.has(segment.id)) flight.retired.add(segment.id);
      if (flight.retired.size === flight.entry.batch.segments.length) {
        flight.controller.abort();
        this.inflight.delete(id);
      }
    }
    this.report();
  }

  /** A new document, user or language: nothing earlier is valid any more. */
  reset(): void {
    this.generation += 1;
    for (const { controller } of this.inflight.values()) controller.abort();
    this.inflight.clear();
    this.queued.clear();
    this.owner.clear();
    this.done.clear();
    this.retryNotBefore.clear();
    this.carried.clear();
    this.limitUntil = 0;
    this.blocked = null;
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = null;
    this.report();
  }

  status(): SchedulerStatus {
    let working = 0;
    for (const entry of this.queued.values()) working += entry.batch.segments.length;
    let held = 0;
    for (const entry of this.queued.values()) if (entry.held) held += entry.batch.segments.length;
    for (const { entry } of this.inflight.values()) working += entry.batch.segments.length;
    return { working, held, blocked: this.blocked };
  }

  private drop(batchId: string) {
    const entry = this.queued.get(batchId);
    if (!entry) return;
    this.queued.delete(batchId);
    const { batch, ...attempts } = entry;
    for (const segment of batch.segments) {
      if (this.owner.get(segment.id) === batchId) this.owner.delete(segment.id);
      this.carried.set(segment.id, attempts);
    }
  }

  private release(batchId: string, segments: readonly Segment[]) {
    for (const segment of segments) if (this.owner.get(segment.id) === batchId) this.owner.delete(segment.id);
  }

  private finish(batchId: string, results: SegmentResult[]) {
    for (const result of results) {
      this.done.set(result.segment_id, result);
      if (this.owner.get(result.segment_id) === batchId) this.owner.delete(result.segment_id);
    }
    if (results.length) this.deps.deliver(results);
  }

  private fail(batchId: string, segments: readonly Segment[], code: string) {
    this.finish(
      batchId,
      segments.map((s) => ({ segment_id: s.id, source_text: s.text, translation_status: 'failed', error_code: code })),
    );
  }

  private pump = () => {
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = null;
    const now = this.deps.now();
    const limited = this.limitUntil > now;
    while (!limited && this.inflight.size < this.deps.maxConcurrent) {
      let next: Queued | undefined;
      for (const entry of this.queued.values()) {
        if (entry.notBefore > now) continue;
        if (!next || (entry.batch.priority === 'urgent' && next.batch.priority !== 'urgent')) next = entry;
      }
      if (!next) break;
      this.send(next);
    }
    let wake = Infinity;
    for (const entry of this.queued.values()) wake = Math.min(wake, Math.max(entry.notBefore, this.limitUntil));
    if (wake <= now) wake = Infinity;
    if (wake !== Infinity) this.timer = this.deps.setTimer(this.pump, Math.max(1, wake - now));
    this.report();
  };

  private send(queued: Queued) {
    // Once sent it is an ordinary request again (a pending answer re-queues it unheld).
    const entry: Queued = { ...queued, held: false };
    const { batch } = entry;
    this.queued.delete(batch.id);
    const controller = new AbortController();
    const retired = new Set<string>();
    this.inflight.set(batch.id, { entry, controller, retired });
    const generation = this.generation;
    const current = () => generation === this.generation && this.inflight.get(batch.id)?.controller === controller;
    this.deps
      .send(
        batch.segments.map((s) => s.text),
        controller.signal,
      )
      .then(
        (items) => {
          if (!current()) return;
          this.inflight.delete(batch.id);
          if (this.blocked) this.blocked = null;
          const results: SegmentResult[] = [];
          const unfinished: Segment[] = [];
          let wait = POLL_MS;
          batch.segments.forEach((segment, index) => {
            if (retired.has(segment.id)) return;
            const item = items[index];
            if (item?.status === 'succeeded' && item.text?.trim()) {
              results.push({
                segment_id: segment.id,
                source_text: segment.text,
                translation_status: 'succeeded',
                translated_text: item.text,
              });
            } else if (item?.status === 'pending') {
              unfinished.push(segment);
              if (item.retry_after_seconds) wait = Math.max(wait, item.retry_after_seconds * 1000);
            } else {
              results.push({
                segment_id: segment.id,
                source_text: segment.text,
                translation_status: 'failed',
                error_code: item?.error_code ?? 'invalid_result',
              });
              if (item?.retry_after_seconds)
                this.retryNotBefore.set(segment.id, this.deps.now() + item.retry_after_seconds * 1000);
            }
          });
          this.finish(batch.id, results);
          if (unfinished.length && entry.polls + 1 >= MAX_POLLS) this.fail(batch.id, unfinished, 'timeout');
          else if (unfinished.length) {
            this.queued.set(batch.id, {
              ...entry,
              limits: 0,
              batch: { ...batch, segments: unfinished },
              notBefore: this.deps.now() + Math.min(wait, 300_000),
              polls: entry.polls + 1,
            });
          }
        },
        (error: unknown) => {
          if (!current()) return;
          this.inflight.delete(batch.id);
          if (isApiError(error) && error.problem.kind === 'aborted') {
            this.release(batch.id, batch.segments);
            return;
          }
          const problem = isApiError(error) ? error.problem : null;
          const live = batch.segments.filter((s) => !retired.has(s.id));
          if (live.length === 0) return;
          if (problem?.kind === 'http' && problem.status === 429 && entry.limits < MAX_LIMIT_WAITS) {
            // The whole scheduler waits, not just this batch.
            const until = this.deps.now() + (problem.retryAfter ? problem.retryAfter * 1000 : DEFAULT_LIMIT_WAIT_MS);
            this.limitUntil = Math.max(this.limitUntil, until);
            this.blocked = { kind: 'limit', until };
            this.queued.set(batch.id, {
              ...entry,
              batch: { ...batch, segments: live },
              notBefore: until,
              limits: entry.limits + 1,
            });
          } else if (problem?.kind === 'http' && problem.code === 'translation_unavailable') {
            this.blocked = { kind: 'unavailable' };
            this.fail(batch.id, live, problem.code);
          } else if (isRetryable(error) && entry.recoveries < MAX_RECOVERIES) {
            this.queued.set(batch.id, {
              ...entry,
              batch: { ...batch, segments: live },
              notBefore: this.deps.now() + 1000 * 2 ** entry.recoveries,
              recoveries: entry.recoveries + 1,
            });
          } else {
            if (isRetryable(error)) this.blocked = { kind: 'network' };
            if (problem?.kind === 'http' && problem.status === 429) this.blocked = null;
            this.fail(batch.id, live, problem?.kind === 'http' ? problem.code : 'transport_failure');
          }
        },
      )
      .finally(() => {
        if (generation === this.generation) this.pump();
      });
  }

  private report() {
    if (!this.deps.onStatus) return;
    const status = this.status();
    const signature = JSON.stringify(status);
    if (signature === this.lastStatus) return;
    this.lastStatus = signature;
    this.deps.onStatus(status);
  }
}
