import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bundle = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationCommitQueue.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { createWebTranslationCommitQueue } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
function harness(options = {}) {
  const callbacks = new Set(); const outcomes = []; const committed = []; let clock = 0;
  const queue = createWebTranslationCommitQueue({ schedule: (callback) => { callbacks.add(callback); return () => callbacks.delete(callback); }, now: () => clock, onSettled: (task, outcome) => outcomes.push([task.key, outcome]), ...options });
  const tick = () => { const callback = [...callbacks][0]; if (callback) { callbacks.delete(callback); callback(); } };
  const drain = () => { let budget = 1000; while (callbacks.size && budget--) tick(); assert.ok(budget > 0); };
  const task = (key, extra = {}) => ({ key, isValid: () => true, commit: () => committed.push(key), ...extra });
  return { queue, callbacks, outcomes, committed, tick, drain, task, advance: (ms) => { clock += ms; } };
}
{
  const h = harness();
  for (let i = 0; i < 100; i++) h.queue.enqueue(h.task(String(i)));
  assert.equal(h.committed.length, 0); assert.equal(h.callbacks.size, 1);
  h.tick(); assert.equal(h.committed.length, 4); assert.equal(h.queue.size, 96);
  h.drain(); assert.equal(h.committed.length, 100); assert.equal(h.callbacks.size, 0);
}
{
  const h = harness();
  h.queue.enqueue(h.task('same', { commit: () => h.committed.push('old') }));
  h.queue.enqueue(h.task('same', { commit: () => h.committed.push('latest') }));
  h.drain(); assert.deepEqual(h.committed, ['latest']); assert.deepEqual(h.outcomes, [['same', 'replaced'], ['same', 'committed']]);
}
{
  const h = harness(); let valid = true;
  h.queue.enqueue(h.task('stale', { isValid: () => valid })); valid = false;
  h.queue.enqueue(h.task('bad', { commit: () => { throw Error('isolated'); } }));
  h.queue.enqueue(h.task('good')); h.drain();
  assert.deepEqual(h.committed, ['good']); assert.deepEqual(h.outcomes, [['stale', 'stale'], ['bad', 'error'], ['good', 'committed']]);
}
{
  const h = harness({ maxPending: 2 });
  assert.ok(h.queue.enqueue(h.task('one'))); assert.ok(h.queue.enqueue(h.task('two')));
  assert.equal(h.queue.enqueue(h.task('three')), false); assert.equal(h.queue.size, 2);
  assert.ok(h.queue.enqueue(h.task('one'))); h.queue.dispose();
  assert.equal(h.callbacks.size, 0); assert.equal(h.queue.size, 0); assert.equal(h.queue.enqueue(h.task('late')), false);
}
{
  const h = harness();
  h.queue.enqueue(h.task('frame', { scopeId: 'frame' })); h.queue.enqueue(h.task('main', { scopeId: 'main' }));
  h.queue.cancelWhere((task) => task.scopeId === 'frame'); h.drain(); assert.deepEqual(h.committed, ['main']);
  h.queue.enqueue(h.task('navigate', { commit: () => { h.queue.reset(); h.queue.enqueue(h.task('new-document')); } }));
  h.queue.enqueue(h.task('old-document')); h.tick(); assert.equal(h.committed.includes('new-document'), false);
  h.drain(); assert.deepEqual(h.committed, ['main', 'new-document']);
}
{
  const h = harness({ maxItemsPerSlice: 10, maxSliceMs: 4 });
  for (let i = 0; i < 10; i++) h.queue.enqueue(h.task(String(i), { commit: () => { h.committed.push(i); h.advance(3); } }));
  h.tick(); assert.deepEqual(h.committed, [0, 1]); h.drain();
}
{
  const h = harness({ maxItemsPerSlice: 1 });
  for (let i = 0; i < 20; i++) h.queue.enqueue(h.task(String(i)));
  h.tick(); h.queue.enqueue(h.task('0-late')); h.drain();
  assert.deepEqual(h.committed, [...Array.from({ length: 20 }, (_, i) => String(i)), '0-late']);
}
{
  const h = harness({ onSettled: () => { throw Error('observer'); } });
  h.queue.enqueue(h.task('a')); h.queue.enqueue(h.task('b')); h.drain(); assert.deepEqual(h.committed, ['a', 'b']);
}
{
  const h = harness({ maxItemsPerSlice: 1 }); let visible = false;
  h.queue.enqueue(h.task('background'));
  h.queue.enqueue(h.task('moving', { priority: () => visible ? 'visible' : 'background' }));
  visible = true; h.tick(); assert.deepEqual(h.committed, ['moving']);
  h.drain(); assert.deepEqual(h.committed, ['moving', 'background']);
}
{
  const h = harness({ maxItemsPerSlice: 1 });
  h.queue.enqueue(h.task('background-a')); h.queue.enqueue(h.task('background-b'));
  for (let i = 0; i < 12; i++) {
    h.queue.enqueue(h.task(`visible-${i}`, { priority: () => 'visible' })); h.tick();
  }
  assert.equal(h.committed[3], 'background-a'); assert.equal(h.committed[7], 'background-b');
  h.drain();
}
{
  const h = harness({ maxItemsPerSlice: 1 }); let reads = 0;
  for (let i = 0; i < 200; i++) h.queue.enqueue(h.task(String(i), {
    priority: () => { reads++; return i === 199 ? 'visible' : 'background'; },
  }));
  h.tick(); assert.ok(reads <= 32); assert.deepEqual(h.committed, ['0']);
  h.queue.dispose();
}
{
  const events = [];
  const h = harness({ maxItemsPerSlice: 2,
    beforeSlice: () => events.push('before'), afterSlice: () => events.push('after'),
  });
  for (let i = 0; i < 3; i++) h.queue.enqueue(h.task(String(i), { commit: () => events.push(i) }));
  h.drain(); assert.deepEqual(events, ['before', 0, 1, 'after', 'before', 2, 'after']);
}
{
  let after = 0;
  const h = harness({ beforeSlice: () => { throw Error('capture'); },
    afterSlice: () => { after++; throw Error('restore'); },
  });
  h.queue.enqueue(h.task('bad-priority', { priority: () => { throw Error('detached'); } }));
  h.queue.enqueue(h.task('visible', { priority: () => 'visible' })); h.drain();
  assert.deepEqual(h.committed, ['visible', 'bad-priority']); assert.equal(after, 1);
}
{
  let first = true; let after = 0;
  const h = harness({ beforeSlice: () => {
    if (first) { first = false; h.queue.reset(); h.queue.enqueue(h.task('new')); }
  }, afterSlice: () => { after++; } });
  h.queue.enqueue(h.task('old', { priority: () => 'visible' })); h.tick();
  assert.deepEqual(h.committed, []); assert.deepEqual(h.outcomes, [['old', 'cancelled']]);
  h.drain(); assert.deepEqual(h.committed, ['new']); assert.equal(after, 2);
}
{
  const h = harness({ beforeSlice: () => h.queue.cancel('visible') });
  h.queue.enqueue(h.task('visible', { priority: () => 'visible' }));
  h.queue.enqueue(h.task('background')); h.drain();
  assert.deepEqual(h.committed, ['background']);
  assert.deepEqual(h.outcomes, [['visible', 'cancelled'], ['background', 'committed']]);
}
{
  const h = harness();
  h.queue.enqueue(h.task('old', { isValid: () => { h.queue.reset(); return true; } }));
  h.drain(); assert.deepEqual(h.committed, []); assert.deepEqual(h.outcomes, [['old', 'stale']]);
}
{
  const h = harness();
  h.queue.enqueue(h.task('replace', { priority: () => 'visible', commit: () => h.committed.push('obsolete') }));
  h.queue.enqueue(h.task('replace', { priority: () => 'background', commit: () => h.committed.push('latest') }));
  h.queue.enqueue(h.task('visible', { priority: () => 'visible' })); h.drain();
  assert.deepEqual(h.committed, ['visible', 'latest']);
  assert.deepEqual(h.outcomes, [['replace', 'replaced'], ['visible', 'committed'], ['replace', 'committed']]);
}
console.log('web translation commit queue: 17 deterministic cases passed');
