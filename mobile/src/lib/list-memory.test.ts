import assert from 'node:assert/strict';
import { test } from 'node:test';

import { anchorStep, createListMemory } from './list-memory.ts';

test('keeps the most recent lists only', () => {
  const memory = createListMemory(2);
  memory.remember('a', { offset: 1, anchorId: 'x' });
  memory.remember('b', { offset: 2, anchorId: 'y' });
  memory.remember('a', { offset: 3, anchorId: 'z' }); // a becomes most recent
  memory.remember('c', { offset: 4, anchorId: null });
  assert.equal(memory.get('b'), null);
  assert.deepEqual(memory.get('a'), { offset: 3, anchorId: 'z' });
  assert.deepEqual(memory.get('c'), { offset: 4, anchorId: null });
});

test('anchor step: on screen, further down, or gone', () => {
  const all = ['n1', 'n2', 'a', 'b', 'c'];
  assert.deepEqual(anchorStep('b', ['a', 'b'], all), { kind: 'done' });
  assert.deepEqual(anchorStep('c', ['n1', 'n2'], all), { kind: 'scroll', index: 4 });
  assert.deepEqual(anchorStep('gone', ['a'], all), { kind: 'done' });
  assert.deepEqual(anchorStep(null, [], all), { kind: 'done' });
});
