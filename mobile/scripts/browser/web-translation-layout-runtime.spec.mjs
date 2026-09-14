import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const rulesSource = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationRules.ts')], bundle: true, write: false, platform: 'node', format: 'esm' }).outputFiles[0].text;
const { compileWebTranslationRule, WEB_TRANSLATION_RULE_REGISTRY } = await import(`data:text/javascript;base64,${Buffer.from(rulesSource).toString('base64')}`);
const rule = compileWebTranslationRule({ id: 'layout-fixture', priority: 50, matches: [{ host: 'layout.example.test' }], rendering: { unclamp: { add: ['.clipped-description'] } } });
const html = `<!doctype html><html><head><style>body{font:16px/22px Arial;margin:20px}.clipped-description{width:290px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}p{margin:0}</style></head><body><div class="clipped-description"><p>This natural flow description is long enough to extend beyond the first two lines. Its translation must become readable when an explicit safe layout rule enables unclamping.</p></div></body></html>`;

async function load(page, enabled) {
  await page.route('https://layout.example.test/article', route => route.fulfill({ body: html, contentType: 'text/html' }));
  await page.goto('https://layout.example.test/article');
  const generated = readFileSync(resolve(root, 'domain/translationRuntimeSources.generated.ts'), 'utf8');
  const source = JSON.parse(generated.match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
  await page.evaluate(({ source, rules }) => {
    window.__messages = [];
    window.ReactNativeWebView = { postMessage: value => window.__messages.push(JSON.parse(value)) };
    (0, eval)(`(${source})`)({ channelToken: 'layout-runtime-test', initialMode: 'bilingual', rules });
    const reset = window.__messages.find(message => message.type === 'reader_translation_navigation_reset');
    window.__readerTranslationBridge.confirmEpoch(reset.document_epoch);
  }, { source, rules: enabled ? [rule, ...WEB_TRANSLATION_RULE_REGISTRY] : WEB_TRANSLATION_RULE_REGISTRY });
  await expect.poll(() => page.evaluate(() => window.__messages.filter(message => message.type === 'reader_translation_batch_plan').flatMap(plan => plan.batches.flatMap(batch => batch.segments)).length)).toBeGreaterThan(0);
  const id = await page.evaluate(() => {
    const segment = window.__messages.filter(message => message.type === 'reader_translation_batch_plan').flatMap(plan => plan.batches.flatMap(batch => batch.segments)).find(segment => segment.text.startsWith('This natural flow description'));
    window.__readerTranslationBridge.applyTranslations([{ segment_id: segment.segment_id, source_text: segment.text, translated_text: '这段自然流说明超过两行。显式允许的布局规则应让译文可读，同时保留原文以及原文模式下的原始折叠样式。', translation_status: 'succeeded' }]);
    return segment.segment_id;
  });
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  return id;
}

async function geometry(page) {
  return page.evaluate(() => {
    const container = document.querySelector('.clipped-description');
    const range = document.createRange();
    range.selectNodeContents(document.querySelector('.reader-translation-node'));
    return { clamp: getComputedStyle(container).webkitLineClamp, bottom: container.getBoundingClientRect().bottom, textBottom: Math.max(...Array.from(range.getClientRects(), rect => rect.bottom)), inline: container.getAttribute('style') };
  });
}

test('runtime expands a safe passive clamp without a site-specific rule', async ({ page }) => {
  await load(page, false);
  const result = await geometry(page);
  expect(result.clamp).toBe('none');
  expect(result.textBottom).toBeLessThanOrEqual(result.bottom + 1);
});

test('runtime applies declarative unclamp and restores it across modes and cleanup', async ({ page }) => {
  const segmentId = await load(page, true);
  await expect.poll(async () => { const result = await geometry(page); return result.textBottom <= result.bottom + 1; }).toBe(true);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  expect((await geometry(page)).clamp).toBe('2');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  await expect.poll(async () => { const result = await geometry(page); return result.textBottom <= result.bottom + 1; }).toBe(true);
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  expect(await page.locator('.reader-translation-node').getAttribute('data-reader-translation-for')).toBe(segmentId);
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  expect(await page.locator('.clipped-description').evaluate(element => getComputedStyle(element).webkitLineClamp)).toBe('2');
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
});
