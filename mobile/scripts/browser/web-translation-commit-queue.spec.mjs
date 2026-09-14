import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const bundle = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationCommitQueue.ts')], bundle: true, format: 'iife', globalName: 'ReaderCommitQueue', write: false }).outputFiles[0].text;
async function setup(page) {
  await page.setContent('<style>article{width:330px}p{line-height:24px}.translated{display:block}</style><article></article>');
  await page.evaluate(() => {
    const article = document.querySelector('article');
    for (let index = 0; index < 100; index++) {
      const p = document.createElement('p'); p.dataset.key = String(index);
      p.textContent = 'An original paragraph for replaying a cached translation in an ordinary article. '.repeat(8);
      article.append(p);
    }
    window.writeTranslation = (index) => {
      const p = document.querySelector(`[data-key="${index}"]`);
      const translated = document.createElement('span'); translated.className = 'translated';
      translated.textContent = '真实译文提交需要插入节点并评估布局。这些文字保留原文并增加段落高度。'.repeat(8);
      p.append(translated);
      return p.getBoundingClientRect().height;
    };
  });
  await page.addScriptTag({ content: bundle });
}
test('100-result replay yields between bounded commits; synchronous baseline blocks a queued heartbeat', async ({ page }, testInfo) => {
  await setup(page);
  const synchronous = await page.evaluate(async () => {
    let count = 0; let heartbeatAt = -1;
    const heartbeat = new Promise((resolve) => setTimeout(() => { heartbeatAt = count; resolve(); }, 0));
    const start = performance.now();
    for (let index = 0; index < 100; index++) { writeTranslation(index); count++; }
    const elapsedMs = performance.now() - start;
    await heartbeat;
    return { count, heartbeatAt, elapsedMs };
  });
  expect(synchronous.heartbeatAt).toBe(100);
  await setup(page);
  const sliced = await page.evaluate(async () => {
    let count = 0; let heartbeatAt = -1;
    const queue = ReaderCommitQueue.createWebTranslationCommitQueue();
    const start = performance.now();
    for (let index = 0; index < 100; index++) queue.enqueue({ key: String(index), isValid: () => true, commit: () => { writeTranslation(index); count++; } });
    const enqueueMs = performance.now() - start;
    await new Promise((resolve) => setTimeout(() => { heartbeatAt = count; resolve(); }, 0));
    while (queue.size) await new Promise((resolve) => setTimeout(resolve, 5));
    return { count, heartbeatAt, enqueueMs, elapsedMs: performance.now() - start };
  });
  expect(sliced.count).toBe(100);
  expect(sliced.heartbeatAt).toBeGreaterThan(0);
  expect(sliced.heartbeatAt).toBeLessThanOrEqual(4);
  await testInfo.attach('commit-replay-measurement.json', { body: JSON.stringify({ synchronous, sliced }, null, 2), contentType: 'application/json' });
  console.log('commit-replay-measurement', JSON.stringify({ synchronous, sliced }));
});
test('navigation between slices cancels old DOM work and latest mode prevents translation flash', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    let epoch = 1; let mode = 'bilingual'; let oldCommits = 0; let newCommits = 0;
    const queue = ReaderCommitQueue.createWebTranslationCommitQueue({ maxItemsPerSlice: 2 });
    for (let index = 0; index < 100; index++) queue.enqueue({ key: `old-${index}`, scopeId: 'main', isValid: () => epoch === 1, commit: () => { writeTranslation(index); oldCommits++; } });
    await new Promise((resolve) => setTimeout(() => {
      epoch = 2; mode = 'original'; queue.reset();
      document.querySelectorAll('.translated').forEach((node) => node.remove());
      for (let index = 0; index < 10; index++) queue.enqueue({ key: `new-${index}`, scopeId: 'main', isValid: () => epoch === 2, commit: () => { if (mode === 'bilingual') writeTranslation(index); newCommits++; } });
      resolve();
    }, 0));
    while (queue.size) await new Promise((resolve) => setTimeout(resolve, 5));
    return { oldCommits, newCommits, visibleTranslations: document.querySelectorAll('.translated').length };
  });
  expect(result.oldCommits).toBeGreaterThan(0);
  expect(result.oldCommits).toBeLessThanOrEqual(2);
  expect(result.newCommits).toBe(10);
  expect(result.visibleTranslations).toBe(0);
});
test('source replacement is checked at execution and a throwing commit does not strand another result', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const outcomes = [];
    const queue = ReaderCommitQueue.createWebTranslationCommitQueue({ onSettled: (task, outcome) => outcomes.push([task.key, outcome]) });
    const source = document.querySelector('[data-key="0"]');
    queue.enqueue({ key: 'source', isValid: () => source.isConnected, commit: () => writeTranslation(0) });
    queue.enqueue({ key: 'bad', isValid: () => true, commit: () => { throw Error('isolated fixture'); } });
    queue.enqueue({ key: 'good', isValid: () => true, commit: () => writeTranslation(2) });
    source.replaceWith(source.cloneNode(true));
    while (queue.size) await new Promise((resolve) => setTimeout(resolve, 5));
    return { outcomes, translations: document.querySelectorAll('.translated').length };
  });
  expect(result.outcomes).toEqual([['source', 'stale'], ['bad', 'error'], ['good', 'committed']]);
  expect(result.translations).toBe(1);
});
