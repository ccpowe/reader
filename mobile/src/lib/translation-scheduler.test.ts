import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError } from './api/errors.ts';
import type { TranslatedItem } from './api/schema.gen.ts';
import { TranslationScheduler, type PlanBatch, type SegmentResult, type TranslationAnswer } from './translation-scheduler.ts';

/** Manual clock and timers; `send` calls wait until the test answers them. */
function harness(maxConcurrent = 3) {
  let now = 0;
  let timers: { at: number; run: () => void; id: number }[] = [];
  let nextTimer = 0;
  const calls: {
    texts: string[];
    signal: AbortSignal;
    answer: (items: TranslationAnswer[]) => void;
    reject: (e: unknown) => void;
  }[] = [];
  const delivered: SegmentResult[] = [];
  const scheduler = new TranslationScheduler({
    send: (texts, signal) =>
      new Promise((answer, reject) => {
        calls.push({ texts, signal, answer, reject });
      }),
    deliver: (results) => delivered.push(...results),
    now: () => now,
    setTimer: (run, ms) => {
      const id = ++nextTimer;
      timers.push({ at: now + ms, run, id });
      return id;
    },
    clearTimer: (id) => {
      timers = timers.filter((t) => t.id !== id);
    },
    maxConcurrent,
  });
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  const advance = async (ms: number) => {
    now += ms;
    const due = timers.filter((t) => t.at <= now);
    timers = timers.filter((t) => t.at > now);
    for (const t of due) t.run();
    await flush();
  };
  return { scheduler, calls, delivered, flush, advance };
}

const batch = (id: string, texts: string[], priority: 'urgent' | 'prefetch' = 'urgent', windowId = 'w1'): PlanBatch => ({
  id,
  priority,
  windowId,
  segments: texts.map((text, i) => ({ id: `${id}-s${i}`, text })),
});

const ok = (text: string): TranslatedItem => ({ status: 'succeeded', text });

test('sends a batch and delivers each result with its source text', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['Hello', 'World'])]);
  assert.deepEqual(h.calls[0].texts, ['Hello', 'World']);
  assert.equal(h.scheduler.status().working, 2);
  h.calls[0].answer([ok('你好'), ok('世界')]);
  await h.flush();
  assert.deepEqual(
    h.delivered.map((r) => [r.segment_id, r.source_text, r.translated_text]),
    [
      ['b1-s0', 'Hello', '你好'],
      ['b1-s1', 'World', '世界'],
    ],
  );
  assert.equal(h.scheduler.status().working, 0);
});

test('pending items are asked again later, finished ones are not', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A', 'B'])]);
  h.calls[0].answer([ok('甲'), { status: 'pending', retry_after_seconds: 2 }]);
  await h.flush();
  assert.equal(h.delivered.length, 1);
  await h.advance(1000);
  assert.equal(h.calls.length, 1, 'waits as long as the server asked');
  await h.advance(1000);
  assert.deepEqual(h.calls[1].texts, ['B']);
  h.calls[1].answer([ok('乙')]);
  await h.flush();
  assert.deepEqual(
    h.delivered.map((r) => r.translated_text),
    ['甲', '乙'],
  );
});

test('a failed item reaches the page as failed with its code', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  h.calls[0].answer([{ status: 'failed', error_code: 'engine_error' }]);
  await h.flush();
  assert.equal(h.delivered[0].translation_status, 'failed');
  assert.equal(h.delivered[0].error_code, 'engine_error');
});

test('urgent batches go first; at most N requests at a time', async () => {
  const h = harness(1);
  h.scheduler.enqueue([batch('p', ['prefetch'], 'prefetch'), batch('u', ['urgent'], 'urgent')]);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0].texts, ['urgent']);
  h.calls[0].answer([ok('x')]);
  await h.flush();
  assert.deepEqual(h.calls[1].texts, ['prefetch']);
});

test('a new window drops queued batches of the old one, not sent ones', async () => {
  const h = harness(1);
  h.scheduler.enqueue([batch('a', ['sent'], 'urgent', 'w1'), batch('b', ['queued'], 'prefetch', 'w1')]);
  h.scheduler.enqueue([batch('c', ['new'], 'urgent', 'w2')]);
  h.calls[0].answer([ok('已发')]);
  await h.flush();
  assert.equal(h.delivered[0].translated_text, '已发', 'the in-flight request still lands');
  assert.deepEqual(h.calls[1].texts, ['new']);
  assert.equal(h.calls.length, 2);
});

test('a segment planned again is replayed from the finished result, without a request', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['Hello'])]);
  h.calls[0].answer([ok('你好')]);
  await h.flush();
  h.scheduler.enqueue([batch('b2', [])].map((b) => ({ ...b, segments: [{ id: 'b1-s0', text: 'Hello' }] })));
  assert.equal(h.calls.length, 1);
  assert.equal(h.delivered.length, 2);
  // changed text is a new request
  h.scheduler.enqueue([{ ...batch('b3', []), segments: [{ id: 'b1-s0', text: 'Hello again' }] }]);
  assert.deepEqual(h.calls[1].texts, ['Hello again']);
});

test('no answer: retried with backoff, then failed', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  for (let attempt = 0; attempt < 3; attempt++) {
    h.calls[attempt].reject(new ApiError({ kind: 'network' }));
    await h.flush();
    assert.equal(h.delivered.length, 0);
    await h.advance(1000 * 2 ** attempt);
  }
  h.calls[3].reject(new ApiError({ kind: 'network' }));
  await h.flush();
  assert.equal(h.delivered[0].translation_status, 'failed');
  assert.deepEqual(h.scheduler.status().blocked, { kind: 'network' });
});

test('429 waits for retry-after; 503 translation_unavailable fails at once', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  h.calls[0].reject(new ApiError({ kind: 'http', status: 429, code: 'too_many_attempts', message: '', retryAfter: 10 }));
  await h.flush();
  assert.equal(h.scheduler.status().blocked?.kind, 'limit');
  await h.advance(9000);
  assert.equal(h.calls.length, 1);
  await h.advance(1000);
  assert.equal(h.calls.length, 2);
  h.calls[1].reject(new ApiError({ kind: 'http', status: 503, code: 'translation_unavailable', message: '' }));
  await h.flush();
  assert.equal(h.delivered[0].error_code, 'translation_unavailable');
  assert.deepEqual(h.scheduler.status().blocked, { kind: 'unavailable' });
});

test('retryFailed lets failed segments be asked again', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  h.calls[0].answer([{ status: 'failed' }]);
  await h.flush();
  h.scheduler.enqueue([{ ...batch('b2', []), segments: [{ id: 'b1-s0', text: 'A' }] }]);
  assert.equal(h.calls.length, 1, 'failed result replayed, not re-asked');
  h.scheduler.retryFailed();
  h.scheduler.enqueue([{ ...batch('b3', []), segments: [{ id: 'b1-s0', text: 'A' }] }]);
  assert.equal(h.calls.length, 2);
});

test('reset aborts requests and ignores their late answers', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  const call = h.calls[0];
  h.scheduler.reset();
  assert.equal(call.signal.aborted, true);
  call.answer([ok('late')]);
  await h.flush();
  assert.equal(h.delivered.length, 0);
  assert.equal(h.scheduler.status().working, 0);
});

test('forget drops queued segments of a removed part of the page', async () => {
  const h = harness(1);
  h.scheduler.enqueue([batch('a', ['first']), batch('b', ['second', 'third'])]);
  h.scheduler.forget(['b-s0', 'b-s1']);
  h.calls[0].answer([ok('一')]);
  await h.flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.scheduler.status().working, 0);
});

test('retry waits out the pause the server gave a failed segment', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'])]);
  h.calls[0].answer([{ status: 'failed', error_code: 'transport_error', retry_after_seconds: 60 }]);
  await h.flush();
  h.scheduler.retryFailed();
  h.scheduler.enqueue([{ ...batch('b2', []), segments: [{ id: 'b1-s0', text: 'A' }] }]);
  assert.equal(h.calls.length, 1, 'not asked again during the pause');
  assert.deepEqual(h.scheduler.status(), { working: 1, held: 1, blocked: null });
  await h.advance(60_000);
  assert.deepEqual(h.calls[1].texts, ['A']);
  assert.equal(h.scheduler.status().held, 0);
});

test("a new viewport keeps a segment's wait and attempts (no early re-ask during the server pause)", async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A'], 'urgent', 'w1')]);
  h.calls[0].answer([{ status: 'failed', retry_after_seconds: 60 }]);
  await h.flush();
  h.scheduler.retryFailed();
  const seg = { id: 'b1-s0', text: 'A' };
  h.scheduler.enqueue([{ ...batch('b2', [], 'urgent', 'w2'), segments: [seg] }]);
  // scrolled: a plan of another window drops the queued batch, then the segment is planned again
  h.scheduler.enqueue([{ ...batch('b3', ['other'], 'urgent', 'w3') }]);
  h.scheduler.enqueue([{ ...batch('b4', [], 'urgent', 'w4'), segments: [seg] }]);
  await h.advance(1000);
  assert.ok(!h.calls.some((c) => c.texts.includes('A') && c !== h.calls[0]), 'not asked again before the pause ends');
  await h.advance(59_000);
  assert.ok(h.calls.some((c, i) => i > 0 && c.texts.includes('A')));
});

test('a segment the page removed while its request is out does not come back', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['A', 'B'])]);
  h.scheduler.forget(['b1-s0']);
  h.calls[0].answer([{ status: 'pending' }, { status: 'pending' }]);
  await h.flush();
  assert.equal(h.scheduler.status().working, 1, 'only B is still asked for');
  await h.advance(750);
  assert.deepEqual(h.calls[1].texts, ['B']);
  // all segments removed: the request is cancelled
  h.scheduler.forget(['b1-s1']);
  assert.equal(h.calls[1].signal.aborted, true);
  assert.equal(h.scheduler.status().working, 0);
});

test('429 pauses every request, and gives up after a few', async () => {
  const h = harness(1);
  h.scheduler.enqueue([batch('a', ['A']), batch('b', ['B'])]);
  const limit = () => new ApiError({ kind: 'http', status: 429, code: 'too_many_attempts', message: '', retryAfter: 60 });
  h.calls[0].reject(limit());
  await h.flush();
  assert.equal(h.calls.length, 1, 'batch b waits too');
  // the two batches take turns; each may wait out 5 limits, then fails
  for (let i = 1; i <= 11; i++) {
    await h.advance(60_000);
    const last = h.calls[h.calls.length - 1];
    last.reject(limit());
    await h.flush();
  }
  assert.deepEqual(
    h.delivered.map((r) => [r.source_text, r.error_code]),
    [
      ['A', 'too_many_attempts'],
      ['B', 'too_many_attempts'],
    ],
  );
  assert.equal(h.calls.length, 12);
});

test('a text the app answered itself (already in the target language) reaches the page marked', async () => {
  const h = harness();
  h.scheduler.enqueue([batch('b1', ['中文段落', 'English'])]);
  h.calls[0].answer([{ status: 'succeeded', text: '中文段落', same_as_source: true }, ok('英文')]);
  await h.flush();
  assert.deepEqual(
    h.delivered.map((r) => [r.translated_text, r.same_as_source]),
    [
      ['中文段落', true],
      ['英文', undefined],
    ],
  );
});
