import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const source = JSON.parse(readFileSync(resolve('domain/translationRuntimeSources.generated.ts'), 'utf8').match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [resolve('domain/webTranslationRules.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
async function start(page, path = '/model') {
  await page.route('https://anchor.test/**', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<main><section id="providers"><p>Providers paragraph remains translated.</p></section><section id="pricing"><p>Pricing paragraph remains translated.</p></section><div id="章节"></div><a name="legacy"></a></main>' }));
  await page.goto(`https://anchor.test${path}`);
  await page.evaluate(({ source, rules }) => {
    window.messages = [];
    window.ReactNativeWebView = { postMessage: raw => {
      const message = JSON.parse(raw); window.messages.push(message);
      if (message.type === 'reader_translation_batch_plan') setTimeout(() => window.__readerTranslationBridge.applyTranslations(message.batches.flatMap(batch => batch.segments).map(segment => ({ segment_id: segment.segment_id, source_text: segment.text, translated_text: `译文 ${segment.text}`, translation_status: 'succeeded' })), message.document_epoch), 20);
    } };
    (0, eval)(`(${source})`)({ channelToken: 'anchor-token', initialMode: 'bilingual', rules });
    window.__readerTranslationBridge.confirmEpoch(window.messages[0].document_epoch);
  }, { source, rules });
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
}
test('scroll-spy anchor changes preserve the epoch and existing translation DOM', async ({ page }) => {
  await start(page);
  await page.evaluate(() => window.translationNode = document.querySelector('.reader-translation-node'));
  for (const hash of ['#pricing', '#providers', '#%E7%AB%A0%E8%8A%82', '#legacy', '']) {
    await page.evaluate(hash => { history.replaceState({}, '', `/model${hash}`); window.__readerTranslationBridge.reconnect('anchor-challenge'); }, hash);
  }
  await page.evaluate(() => { location.hash = 'pricing'; });
  await page.waitForTimeout(450);
  expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_navigation_reset').length)).toBe(1);
  expect(await page.evaluate(() => window.translationNode === document.querySelector('.reader-translation-node') && window.translationNode.isConnected)).toBe(true);
  await page.evaluate(() => window.__readerTranslationBridge.refresh());
  expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_navigation_reset').length)).toBe(1);
});
test('unknown fragments, hash routers, paths and queries still invalidate old document epochs', async ({ page }) => {
  for (const suffix of ['#unknown-route', '#/next', '#!/next', '?model=next#pricing', '/next#pricing', '#%invalid']) {
    await start(page);
    await page.evaluate(suffix => history.pushState({}, '', `/model${suffix}`), suffix);
    expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_navigation_reset').length)).toBe(2);
    expect(await page.locator('.reader-translation-node').count()).toBe(0);
  }
});
test('child frame anchor changes retain its scope while real hash routes retire it', async ({ page }) => {
  await start(page);
  await page.evaluate(() => { const frame = document.createElement('iframe'); frame.src = '/child'; document.body.appendChild(frame); });
  await expect(page.frameLocator('iframe').locator('.reader-translation-node')).toHaveCount(2);
  await page.locator('iframe').evaluate(frame => {
    frame.savedTranslation = frame.contentDocument.querySelector('.reader-translation-node');
    frame.contentWindow.history.replaceState({}, '', '#pricing');
  });
  await page.waitForTimeout(1000);
  expect(await page.locator('iframe').evaluate(frame => frame.savedTranslation === frame.contentDocument.querySelector('.reader-translation-node'))).toBe(true);
  await page.locator('iframe').evaluate(frame => frame.contentWindow.history.replaceState({}, '', '#/next'));
  await expect.poll(() => page.evaluate(() => window.messages.some(message => message.type === 'reader_translation_scope_disposed'))).toBe(true);
});

test('leaving an unknown hash route for an existing anchor still changes epoch', async ({ page }) => {
  await start(page, '/model#/route');
  await page.evaluate(() => history.replaceState({}, '', '#pricing'));
  expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_navigation_reset').length)).toBe(2);
  expect(await page.locator('.reader-translation-node').count()).toBe(0);
});
