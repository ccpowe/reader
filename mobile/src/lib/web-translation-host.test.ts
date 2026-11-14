import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { TranslatedItem } from './api/schema.gen.ts';
import { translationProblem, WebTranslationHost, type HostState } from './web-translation-host.ts';

const NAV = 'web-nav0001';
const EPOCH = 'epoch-0001';
const URL = 'https://e.com/post';

/** A host with a fake page: records injected scripts; `page()` posts as the runtime would. */
function harness(mode: 'original' | 'bilingual' = 'bilingual') {
  const injected: string[] = [];
  const states: HostState[] = [];
  const sends: { texts: string[]; answer: (items: TranslatedItem[]) => void }[] = [];
  let timers: { run: () => void; id: number }[] = [];
  let nextTimer = 0;
  const host = new WebTranslationHost({
    inject: (script) => injected.push(script),
    bootstrap: (config) => `BOOT(${JSON.stringify(config)})`,
    send: (texts) => new Promise((answer) => sends.push({ texts, answer })),
    onState: (state) => states.push(state),
    mode,
    random: () => 'tok1',
    setTimer: (run) => {
      const id = ++nextTimer;
      timers.push({ run, id });
      return id;
    },
    clearTimer: (id) => {
      timers = timers.filter((t) => t.id !== id);
    },
    now: () => 0,
  });
  const page = (payload: Record<string, unknown>, overrides: Record<string, unknown> = {}) =>
    host.message(
      JSON.stringify({
        channel_token: 'reader-tok1',
        navigation_id: NAV,
        document_epoch: EPOCH,
        document_url: URL,
        ...payload,
        ...overrides,
      }),
    );
  const nonce = () => {
    const last = [...injected].reverse().find((s) => s.includes('reconnect'));
    return last ? JSON.parse(last.match(/reconnect\?\.\((".*?")\)/)![1]) : null;
  };
  /** The full handshake as the runtime answers it. */
  const connect = () => {
    host.loadEnd();
    const challenge = nonce();
    page({ type: 'reader_translation_handshake', challenge_nonce: challenge });
    page({ type: 'reader_translation_handshake_ready', challenge_nonce: challenge });
  };
  const fireTimers = () => {
    const due = timers;
    timers = [];
    for (const t of due) t.run();
  };
  return { host, injected, states, sends, page, nonce, connect, fireTimers, flush: () => new Promise((r) => setImmediate(r)) };
}

const plan = (segments: { segment_id: string; text: string }[]) => ({
  type: 'reader_translation_batch_plan',
  batches: [{ id: 'b1', priority: 'urgent', window_id: 'w1', segments }],
});

test('handshake: the page must echo the nonce; then the epoch is confirmed and the host is verified', () => {
  const h = harness();
  h.host.loadEnd();
  assert.match(h.injected[0], /^BOOT\(\{"channelToken":"reader-tok1","initialMode":"bilingual"\}\)$/);
  const challenge = h.nonce();
  h.page({ type: 'reader_translation_handshake', challenge_nonce: 'wrong' });
  assert.ok(!h.injected.some((s) => s.includes('confirmEpoch')));
  h.page({ type: 'reader_translation_handshake', challenge_nonce: challenge });
  assert.ok(h.injected.some((s) => s.includes(`confirmEpoch("${EPOCH}","${challenge}")`)));
  assert.equal(h.host.state().verified, false);
  h.page({ type: 'reader_translation_handshake_ready', challenge_nonce: challenge });
  assert.equal(h.host.state().verified, true);
});

test('messages without the channel token or of another document are ignored', async () => {
  const h = harness();
  h.connect();
  h.page(plan([{ segment_id: `${NAV}:${EPOCH}:main:1`, text: 'Hi' }]), { channel_token: 'forged' });
  h.page(plan([{ segment_id: `${NAV}:${EPOCH}:main:1`, text: 'Hi' }]), { document_epoch: 'epoch-other' });
  assert.equal(h.sends.length, 0);
  // and segment ids must belong to this document
  h.page(plan([{ segment_id: 'someone-else:1', text: 'Hi' }]));
  assert.equal(h.sends.length, 0);
});

test('a plan is translated and the results go back to the confirmed epoch', async () => {
  const h = harness();
  h.connect();
  const id = `${NAV}:${EPOCH}:main:1`;
  h.page(plan([{ segment_id: id, text: ' Hello ' }]));
  assert.deepEqual(h.sends[0].texts, ['Hello']);
  h.sends[0].answer([{ status: 'succeeded', text: '你好' }]);
  await h.flush();
  const applied = h.injected.find((s) => s.includes('applyTranslations'))!;
  assert.match(
    applied,
    new RegExp(`"segment_id":"${id}","source_text":"Hello","translation_status":"succeeded","translated_text":"你好"`),
  );
  assert.match(applied, new RegExp(`,"${EPOCH}"\\);true;$`));
});

test('in original mode plans are not translated; switching to bilingual tells the page', () => {
  const h = harness('original');
  h.connect();
  h.page(plan([{ segment_id: `${NAV}:${EPOCH}:main:1`, text: 'Hi' }]));
  assert.equal(h.sends.length, 0);
  h.host.setMode('bilingual');
  assert.ok(h.injected.some((s) => s.includes('setMode("bilingual")')));
});

test('injected values cannot close the script', async () => {
  const h = harness();
  h.connect();
  const id = `${NAV}:${EPOCH}:main:1`;
  h.page(plan([{ segment_id: id, text: 'x' }]));
  h.sends[0].answer([{ status: 'succeeded', text: '</script><script>alert(1)</script>' + String.fromCharCode(0x2028) }]);
  await h.flush();
  const applied = h.injected.find((s) => s.includes('applyTranslations'))!;
  assert.doesNotMatch(applied, /<\/script>/);
  assert.ok(!applied.includes(String.fromCharCode(0x2028)));
});

test('a page whose runtime never answers stalls after a few probes; retry starts it again', () => {
  const h = harness();
  h.host.loadEnd();
  for (let i = 0; i < 6; i++) h.fireTimers();
  assert.equal(h.host.state().stalled, true);
  assert.deepEqual(translationProblem(h.host.state(), 'bilingual'), { message: '这个网页的翻译没有启动。', retry: true });
  const before = h.injected.length;
  h.host.retry();
  assert.match(h.injected[before], /^BOOT/);
});

test('a new document (another epoch) drops what the old one asked for', async () => {
  const h = harness();
  h.connect();
  h.page(plan([{ segment_id: `${NAV}:${EPOCH}:main:1`, text: 'Old' }]));
  h.host.loadStart();
  const challenge = h.nonce();
  h.page({ type: 'reader_translation_handshake', challenge_nonce: challenge }, { document_epoch: 'epoch-0002' });
  h.sends[0].answer([{ status: 'succeeded', text: '旧' }]);
  await h.flush();
  assert.ok(!h.injected.some((s) => s.includes('applyTranslations')));
});

test('failed segments are counted from the page and cleared by retry', () => {
  const h = harness();
  h.connect();
  const id = `${NAV}:${EPOCH}:main:1`;
  h.page(plan([{ segment_id: id, text: 'Hi' }]));
  h.page({ type: 'reader_translation_segment_event', segment_id: id, state: 'failed', reason: 'engine_error' });
  assert.equal(h.host.state().failed, 1);
  assert.deepEqual(translationProblem(h.host.state(), 'bilingual'), { message: '有 1 段没翻译成。', retry: true });
  assert.equal(translationProblem(h.host.state(), 'original'), null);
  h.host.retry();
  assert.equal(h.host.state().failed, 0);
  assert.ok(h.injected.some((s) => s.includes('retryFailed')));
});

test('extraction: the page answers for this request and document; the article is checked', async () => {
  const h = harness();
  h.connect();
  const pending = h.host.extract();
  const request = h.injected.find((s) => s.includes('extractReader'))!;
  const requestId = JSON.parse(request.match(/extractReader\?\.\((".*?")/)![1]);
  assert.match(requestId, /^reader-[a-z0-9-]+$/);
  const article = { url: URL, title: 'T', byline: null, lang: 'en', html: '<p>Body</p>', text: 'Body' };
  h.page({ type: 'reader_extraction_result', request_id: 'reader-other-1', article });
  h.page({ type: 'reader_extraction_result', request_id: requestId, article });
  assert.deepEqual(await pending, { ok: true, article });
});

test('extraction fails cleanly: not verified, bad article, or the page navigates away', async () => {
  const h = harness();
  assert.deepEqual(await h.host.extract(), { ok: false, reason: 'unavailable' });
  h.connect();
  const bad = h.host.extract();
  const requestId = JSON.parse(h.injected.find((s) => s.includes('extractReader'))!.match(/extractReader\?\.\((".*?")/)![1]);
  h.page({
    type: 'reader_extraction_result',
    request_id: requestId,
    article: { url: 'https://other.com', title: 'T', byline: null, lang: null, html: '<p>x</p>', text: 'x' },
  });
  assert.deepEqual(await bad, { ok: false, reason: 'unavailable' });
  const left = h.host.extract();
  h.host.loadStart();
  assert.deepEqual(await left, { ok: false, reason: 'changed' });
});

test('reader profile and extracted content go into the bootstrap', () => {
  const host = new WebTranslationHost({
    inject: () => {},
    bootstrap: (config) => JSON.stringify(config),
    send: () => Promise.resolve([]),
    onState: () => {},
    mode: 'original',
    profile: 'reader',
    readerContent: { html: '<p>x</p>', url: URL },
    random: () => 'tok',
  });
  assert.deepEqual(JSON.parse(host.script()), {
    channelToken: 'reader-tok',
    initialMode: 'original',
    profile: 'reader',
    readerContent: { html: '<p>x</p>', url: URL },
  });
  host.dispose();
});

test('without injected clock and timers the host uses the real ones', async () => {
  const sent: string[][] = [];
  let script = '';
  const host = new WebTranslationHost({
    inject: (s) => {
      script = s;
    },
    bootstrap: () => 'BOOT',
    send: (texts) => {
      sent.push(texts);
      return Promise.resolve([{ status: 'succeeded', text: '好' }]);
    },
    onState: () => {},
    mode: 'bilingual',
    random: () => 'tok',
  });
  const post = (payload: Record<string, unknown>) =>
    host.message(JSON.stringify({ channel_token: 'reader-tok', navigation_id: NAV, document_epoch: EPOCH, document_url: URL, ...payload }));
  host.loadEnd();
  const challenge = JSON.parse(script.match(/reconnect\?\.\((".*?")\)/)![1]);
  post({ type: 'reader_translation_handshake', challenge_nonce: challenge });
  post({ type: 'reader_translation_handshake_ready', challenge_nonce: challenge });
  post(plan([{ segment_id: `${NAV}:${EPOCH}:main:1`, text: 'Hi' }]));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(sent, [['Hi']]);
  assert.match(script, /applyTranslations/);
  host.dispose();
});
