import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { TranslatedItem } from './api/schema.gen.ts';
import { CaptionHost, captionProblem, captionTime, type CaptionTimeline, type CaptionState } from './caption-host.ts';

const NAV = 'youtube-nav0001';

function harness(timelineMode: 'original' | 'bilingual' = 'bilingual') {
  const injected: string[] = [];
  const timelines: CaptionTimeline[] = [];
  const states: CaptionState[] = [];
  const requests: string[] = [];
  const sends: { texts: string[]; answer: (items: TranslatedItem[]) => void }[] = [];
  const host = new CaptionHost({
    inject: (s) => injected.push(s),
    bootstrap: (config) => `BOOT(${JSON.stringify(config)})`,
    send: (texts) => new Promise((answer) => sends.push({ texts, answer })),
    onState: (s) => states.push(s),
    onTimeline: (t) => timelines.push(t),
    onModeRequest: (m) => requests.push(m),
    mode: 'original',
    timelineMode,
    random: () => 'tok1',
    setTimer: () => 0,
    clearTimer: () => {},
    now: () => 0,
  });
  const page = (payload: Record<string, unknown>, overrides: Record<string, unknown> = {}) =>
    host.message(JSON.stringify({ channel_token: 'reader-tok1', navigation_id: NAV, media_epoch: 1, ...payload, ...overrides }));
  return { host, injected, timelines, states, requests, sends, page, flush: () => new Promise((r) => setImmediate(r)) };
}

const cue = (n: number, extra: Record<string, unknown> = {}) => ({
  segment_id: `${NAV}:c${n}`,
  source_text: `line ${n}`,
  translated_text: null,
  start_ms: n * 1000,
  end_ms: n * 1000 + 900,
  ...extra,
});

test('caption times', () => {
  assert.equal(captionTime(65_000), '1:05');
  assert.equal(captionTime(3_723_000), '1:02:03');
  assert.equal(captionTime(-5), '0:00');
});

test('the caption list is checked and passed on', () => {
  const h = harness();
  h.page({
    type: 'reader_translation_caption_timeline',
    captions: [cue(1), cue(2, { translated_text: '第二行' }), cue(3, { segment_id: 'other:c3' }), cue(4, { end_ms: 1 })],
    current_segment_id: `${NAV}:c2`,
    has_previous: false,
    has_next: true,
  });
  const t = h.timelines.at(-1)!;
  assert.deepEqual(
    t.captions.map((c) => [c.id, c.translated]),
    [
      [`${NAV}:c1`, null],
      [`${NAV}:c2`, '第二行'],
    ],
  );
  assert.equal(t.currentId, `${NAV}:c2`);
  assert.equal(t.hasNext, true);
});

test('forged token, another navigation id, or an older epoch are ignored', () => {
  const h = harness();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' });
  h.page({ type: 'reader_translation_caption_timeline', captions: [cue(1)] }, { channel_token: 'x' });
  h.page({ type: 'reader_translation_caption_timeline', captions: [cue(1)] }, { navigation_id: 'youtube-other01' });
  h.page({ type: 'reader_translation_caption_timeline', captions: [cue(1)] }, { media_epoch: 2 });
  const before = h.timelines.length;
  h.page({ type: 'reader_translation_caption_timeline', captions: [cue(1)] }, { media_epoch: 1 });
  assert.equal(h.timelines.length, before, 'epoch 1 after 2 is stale');
});

test('plans are translated as captions and results carry the epoch', async () => {
  const h = harness();
  h.page({
    type: 'reader_translation_batch_plan',
    batches: [{ id: 'b', priority: 'urgent', window_id: '1:0', segments: [{ segment_id: `${NAV}:c1`, text: 'Hello' }] }],
  });
  assert.deepEqual(h.sends[0].texts, ['Hello']);
  h.sends[0].answer([{ status: 'succeeded', text: '你好' }]);
  await h.flush();
  assert.match(
    h.injected.at(-1)!,
    /applyTranslations\(\[\{"segment_id":"youtube-nav0001:c1","source_text":"Hello","translation_status":"succeeded","translated_text":"你好"\}\],1\);true;$/,
  );
});

test('nothing is translated while both modes are original', () => {
  const h = harness('original');
  h.page({
    type: 'reader_translation_batch_plan',
    batches: [{ id: 'b', priority: 'urgent', window_id: 'w', segments: [{ segment_id: `${NAV}:c1`, text: 'Hello' }] }],
  });
  assert.equal(h.sends.length, 0);
});

test('a new epoch (another video) drops work and the list; late results do not land', async () => {
  const h = harness();
  h.page({
    type: 'reader_translation_batch_plan',
    batches: [{ id: 'b', priority: 'urgent', window_id: 'w', segments: [{ segment_id: `${NAV}:c1`, text: 'Old' }] }],
  });
  h.page({ type: 'reader_translation_status', state: 'captions_detected' }, { media_epoch: 2 });
  assert.deepEqual(h.timelines.at(-1)!.captions, []);
  h.sends[0].answer([{ status: 'succeeded', text: '旧' }]);
  await h.flush();
  assert.ok(!h.injected.some((s) => s.includes('applyTranslations')));
});

test('the player button asks; commands go to the runtime with the epoch', () => {
  const h = harness();
  h.page({ type: 'reader_translation_mode_request', mode: 'bilingual' });
  h.page({ type: 'reader_translation_mode_request', mode: 'evil' });
  assert.deepEqual(h.requests, ['bilingual']);
  h.host.seekTo(12_345.6);
  h.host.setVisible([`${NAV}:c1`]);
  h.host.moveWindow('next', `${NAV}:c1`);
  assert.ok(h.injected.includes('window.__readerTranslationBridge?.seekTo?.(12346);true;'));
  assert.ok(h.injected.some((s) => s.includes(`setTimelineVisibleSegments?.(["${NAV}:c1"],1)`)));
  assert.ok(h.injected.some((s) => s.includes(`setTimelineWindow?.("next","${NAV}:c1",1)`)));
});

test('captions unavailable, failures and retry', async () => {
  const h = harness();
  h.page({ type: 'reader_translation_status', state: 'captions_unavailable' });
  assert.equal(h.host.state().captions, 'unavailable');
  h.page({
    type: 'reader_translation_batch_plan',
    batches: [{ id: 'b', priority: 'urgent', window_id: 'w', segments: [{ segment_id: `${NAV}:c1`, text: 'A' }] }],
  });
  h.sends[0].answer([{ status: 'failed', error_code: 'engine_error' }]);
  await h.flush();
  assert.deepEqual(captionProblem(h.host.state(), true), { message: '有 1 句字幕没翻译成。', retry: true });
  assert.equal(captionProblem(h.host.state(), false), null);
  h.host.retry();
  assert.equal(h.host.state().failed, 0);
});

test('a reloaded document names a new navigation id', () => {
  const h = harness();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' });
  h.host.loadStart();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' }, { navigation_id: 'youtube-nav0002', media_epoch: 0 });
  assert.equal(h.host.state().captions, 'detected');
});

test('after a reload, a late message of the old document does not bind', () => {
  const h = harness();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' });
  h.host.loadStart();
  h.page({
    type: 'reader_translation_batch_plan',
    batches: [{ id: 'b', priority: 'urgent', window_id: 'w', segments: [{ segment_id: `${NAV}:c1`, text: 'Old' }] }],
  });
  assert.equal(h.sends.length, 0);
  h.page({ type: 'reader_translation_status', state: 'captions_detected' }, { navigation_id: 'youtube-nav0002', media_epoch: 0 });
  assert.equal(h.host.state().captions, 'detected');
});

test('on the YouTube web page the caption runtime uses its own bridge name', () => {
  const injected: string[] = [];
  const host = new CaptionHost({
    inject: (s) => injected.push(s),
    bootstrap: (config) => JSON.stringify(config),
    send: () => Promise.resolve([]),
    onState: () => {},
    onTimeline: () => {},
    onModeRequest: () => {},
    mode: 'bilingual',
    timelineMode: 'original',
    bridgeName: '__readerCaptionBridge',
    random: () => 't',
  });
  assert.equal(
    JSON.parse(host.script({ disableBilingualCaptions: '', enableBilingualCaptions: '', bilingual: '' })).bridgeName,
    '__readerCaptionBridge',
  );
  host.setMode('original');
  assert.match(injected[0], /^window\.__readerCaptionBridge\?\.setMode/);
});

test('a history change inside the same document (load start/end, same id) keeps working', () => {
  const h = harness();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' });
  h.host.loadStart();
  h.host.loadEnd();
  h.page({ type: 'reader_translation_status', state: 'captions_detected' });
  assert.equal(h.host.state().captions, 'detected');
});
