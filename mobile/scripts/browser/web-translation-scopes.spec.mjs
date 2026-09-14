import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const generated = readFileSync(resolve('domain/translationRuntimeSources.generated.ts'), 'utf8');
const source = JSON.parse(generated.match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [resolve('domain/webTranslationRules.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
const all = page => page.evaluate(() => [...new Map(window.messages.filter(m => m.type === 'reader_translation_batch_plan').flatMap(m => m.batches.flatMap(b => b.segments)).map(s => [s.segment_id, s])).values()]);
async function start(page, html, setup, frameHtml) {
  await page.route('https://scope.test/**', route => route.fulfill({ contentType: 'text/html', body: frameHtml && new URL(route.request().url()).pathname !== '/main' ? frameHtml : html }));
  await page.goto('https://scope.test/main');
  if (setup) await page.evaluate(setup);
  await page.evaluate(({ source, rules }) => {
    window.messages = [];
    window.ReactNativeWebView = { postMessage: m => window.messages.push(JSON.parse(m)) };
    (0, eval)(`(${source})`)({ channelToken: 'scope-test', initialMode: 'bilingual', rules });
    window.epoch = window.messages.find(m => m.type === 'reader_translation_navigation_reset').document_epoch;
    window.__readerTranslationBridge.confirmEpoch(window.epoch);
  }, { source, rules });
}
async function translate(page) {
  const segments = await all(page);
  await page.evaluate(segments => window.__readerTranslationBridge.applyTranslations(segments.map(s => ({ segment_id: s.segment_id, source_text: s.text, translated_text: `译文 ${s.text}`, translation_status: 'succeeded' })), window.epoch), segments);
}

test('one channel discovers main, srcdoc frame, shadow direct text and named slots once', async ({ page }) => {
  await start(page, '<p>Main document meaningful paragraph.</p><iframe srcdoc="<p>Frame document meaningful paragraph.</p>"></iframe><div id="host"><p slot="body">Assigned light paragraph content.</p>Unassigned light content must remain undiscovered.</div><div id="direct"></div>', () => {
    document.querySelector('#host').attachShadow({ mode: 'open' }).innerHTML = '<p>Shadow document meaningful paragraph.</p><slot name="body"><p>Fallback must not appear.</p></slot>';
    document.querySelector('#direct').attachShadow({ mode: 'open' }).textContent = 'Direct shadow meaningful paragraph.';
  });
  await expect.poll(async () => (await all(page)).map(s => s.text)).toEqual(expect.arrayContaining(['Main document meaningful paragraph.', 'Frame document meaningful paragraph.', 'Shadow document meaningful paragraph.', 'Assigned light paragraph content.', 'Direct shadow meaningful paragraph.']));
  expect((await all(page)).filter(s => s.text.includes('Assigned light')).length).toBe(1);
  expect((await all(page)).some(s => /Fallback|Unassigned light/.test(s.text))).toBe(false);
  expect(await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_navigation_reset').length)).toBe(1);
  await translate(page);
  await expect(page.locator('#host .reader-translation-node[slot="body"]')).toBeVisible();
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  expect(await page.locator('#direct .reader-translation-node').isVisible()).toBe(false);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  await expect(page.locator('#direct .reader-translation-node')).toBeVisible();
});

test('frame document navigation rejects old results, preserves main and uses frame base URI', async ({ page }) => {
  await start(page, '<p>Main paragraph remains translated.</p><iframe srcdoc="<base href=&quot;https://scope.test/docs/&quot;><p>Frame paragraph with <a href=&quot;reference&quot;>relative reference</a> included.</p>"></iframe>');
  await expect.poll(async () => (await all(page)).length).toBe(2);
  const old = await all(page);
  await translate(page);
  expect(await page.frameLocator('iframe').locator('.reader-translation-node a').getAttribute('href')).toBe('reference');
  expect(await page.frameLocator('iframe').locator('.reader-translation-node a').evaluate(a => a.href)).toBe('https://scope.test/docs/reference');
  await page.locator('iframe').evaluate(frame => frame.srcdoc = '<p>Replacement frame document content.</p>');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Replacement frame document content.')).toBe(true);
  await page.evaluate(old => window.__readerTranslationBridge.applyTranslations(old.map(s => ({ segment_id: s.segment_id, source_text: s.text, translated_text: 'LATE OLD RESULT', translation_status: 'succeeded' })), window.epoch), old.filter(s => s.text.startsWith('Frame')));
  expect(await page.frameLocator('iframe').locator('.reader-translation-node').count()).toBe(0);
  await expect(page.locator('body > .reader-translation-node')).toHaveText('译文 Main paragraph remains translated.');
  expect(await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_navigation_reset').length)).toBe(1);
});

test('late shadow registration, hidden recovery, mode mutation and removed host cleanup', async ({ page }) => {
  await start(page, '<p>Main readable content.</p><div id="late"></div><div id="hidden" style="display:none"></div><button><span id="button"></span></button>', () => {
    document.querySelector('#hidden').attachShadow({ mode: 'open' }).innerHTML = '<p>Initially hidden shadow content.</p>';
    document.querySelector('#button').attachShadow({ mode: 'open' }).innerHTML = '<p>Excluded interactive shadow content.</p>';
  });
  await page.evaluate(() => document.querySelector('#late').attachShadow({ mode: 'open' }).innerHTML = '<p>Late attached shadow content.</p>');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Late attached shadow content.')).toBe(true);
  expect((await all(page)).some(s => s.text.includes('hidden shadow') || s.text.includes('interactive shadow'))).toBe(false);
  await page.locator('#hidden').evaluate(el => el.style.display = 'block');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Initially hidden shadow content.')).toBe(true);
  await translate(page);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  await page.evaluate(() => document.querySelector('#late').shadowRoot.querySelector('p').textContent = 'Changed shadow paragraph during original mode.');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Changed shadow paragraph during original mode.')).toBe(true);
  await page.locator('#late').evaluate(el => el.remove());
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_segment_event' && m.reason === 'source_removed').length)).toBeGreaterThan(0);
});

test('inner frame scroll discovers content clipped outside its viewport and sandbox skips safely', async ({ page }) => {
  await start(page, '<p>Main readable paragraph.</p><iframe style="height:120px" srcdoc="<p>Frame initial visible paragraph.</p><div style=&quot;height:900px&quot;></div><p>Frame distant paragraph after scrolling.</p>"></iframe><iframe sandbox srcdoc="<p>Sandbox inaccessible content.</p>"></iframe>');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Frame initial visible paragraph.')).toBe(true);
  expect((await all(page)).some(s => s.text.includes('distant') || s.text.includes('Sandbox'))).toBe(false);
  await page.locator('iframe').first().evaluate(frame => frame.contentWindow.scrollTo(0, 10000));
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Frame distant paragraph after scrolling.')).toBe(true);
});

test('nested frame and body or SPA replacement keep the top epoch and reject synchronous old results', async ({ page }) => {
  await start(page, '<p>Main persistent paragraph content.</p><iframe id="outer" src="/outer"></iframe>', null, '<p>Old outer frame paragraph.</p><iframe srcdoc="<p>Nested frame paragraph content.</p>"></iframe>');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Nested frame paragraph content.')).toBe(true);
  const old = (await all(page)).find(s => s.text === 'Old outer frame paragraph.');
  await page.evaluate(old => {
    const frame = document.querySelector('#outer');
    frame.contentDocument.body.innerHTML = '<p>Replacement body paragraph content.</p>';
    frame.contentWindow.history.pushState({}, '', 'https://scope.test/next');
    window.__readerTranslationBridge.applyTranslations([{ segment_id: old.segment_id, source_text: old.text, translated_text: 'OLD RESPONSE', translation_status: 'succeeded' }], window.epoch);
  }, old);
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Replacement body paragraph content.')).toBe(true);
  expect(await page.frameLocator('#outer').locator('.reader-translation-node').count()).toBe(0);
  expect(await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_navigation_reset').length)).toBe(1);
});

test('frame ancestor clipping and transform conservatively prevent invisible urgent work', async ({ page }) => {
  await start(page, '<p>Main readable paragraph content.</p><div style="height:15px;overflow:hidden"><iframe srcdoc="<div style=&quot;height:70px&quot;></div><p>Clipped frame invisible paragraph.</p>"></iframe></div><div style="transform:scale(.8)"><iframe srcdoc="<p>Transformed frame uncertain paragraph.</p>"></iframe></div>');
  await expect.poll(async () => (await all(page)).length).toBe(1);
  await page.waitForTimeout(1100);
  expect((await all(page)).some(s => /Clipped frame|Transformed frame/.test(s.text))).toBe(false);
  expect(await page.evaluate(() => window.messages.some(m => m.type === 'reader_translation_engine_diagnostics' && m.render_failures.scope_geometry_unknown))).toBe(true);
});

test('scope count is globally bounded and cleanup releases root styles and listeners', async ({ page }) => {
  await start(page, '<p>Main budget test paragraph.</p>' + '<div class="host"></div>'.repeat(40), () => {
    document.querySelectorAll('.host').forEach((host, i) => host.attachShadow({ mode: 'open' }).innerHTML = `<p>Shadow budget paragraph number ${i}.</p>`);
  });
  await expect.poll(() => page.locator('#reader-translation-style').count()).toBe(32);
  await page.waitForTimeout(900);
  expect(await page.locator('#reader-translation-style').count()).toBe(32);
  expect(await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_navigation_reset').length)).toBe(1);
  expect(await page.evaluate(() => window.messages.some(m => m.type === 'reader_translation_engine_diagnostics' && m.render_failures.scope_limit))).toBe(true);
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  expect(await page.locator('#reader-translation-style').count()).toBe(0);
});

test('direct shadow text mutation remains discoverable and slot reassignment does not duplicate prose', async ({ page }) => {
  await start(page, '<p>Main readable paragraph.</p><div id="host"><p slot="one">Assigned unique prose paragraph.</p></div><div id="direct"></div>', () => {
    document.querySelector('#host').attachShadow({ mode: 'open' }).innerHTML = '<slot name="one"></slot><slot name="two"></slot>';
    document.querySelector('#direct').attachShadow({ mode: 'open' }).textContent = 'Direct initial shadow prose.';
  });
  await expect.poll(async () => (await all(page)).length).toBe(3);
  await translate(page);
  await page.evaluate(() => {
    document.querySelector('#direct').shadowRoot.firstChild.nodeValue = 'Direct changed shadow prose.';
    document.querySelector('#host p').slot = 'two';
  });
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Direct changed shadow prose.')).toBe(true);
  await expect(page.locator('#host > .reader-translation-node')).toHaveAttribute('slot', 'two');
  const assigned = (await all(page)).filter(s => s.text === 'Assigned unique prose paragraph.');
  expect(new Set(assigned.map(s => s.segment_id)).size).toBe(1);
});

test('hidden direct shadow prose restores through every mode and cleanup preserves host classes', async ({ page }) => {
  await start(page, '<p>Main readable paragraph.</p><div id="direct" class="site-owned" style="display:none"></div>', () => document.querySelector('#direct').attachShadow({ mode: 'open' }).textContent = 'Hidden direct shadow prose.');
  await page.locator('#direct').evaluate(el => el.style.display = 'block');
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Hidden direct shadow prose.')).toBe(true);
  await translate(page);
  for (const mode of ['translated', 'original', 'bilingual']) {
    await page.evaluate(mode => window.__readerTranslationBridge.setMode(mode), mode);
    expect(await page.locator('#direct .reader-translation-node').isVisible()).toBe(mode !== 'original');
  }
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  await expect(page.locator('#direct')).toHaveAttribute('class', 'site-owned');
  expect(await page.locator('#direct').evaluate(el => el.shadowRoot.textContent)).toBe('Hidden direct shadow prose.');
});

test('frame body identity replacement and cross-origin documents are isolated', async ({ page }) => {
  await page.route('https://other.test/**', route => route.fulfill({ contentType: 'text/html', body: '<p>Cross origin unavailable paragraph.</p>' }));
  await start(page, '<p>Main readable paragraph.</p><iframe id="frame" srcdoc="<p>Frame before body replacement.</p>"></iframe><iframe src="https://other.test/page"></iframe>');
  await expect.poll(async () => (await all(page)).length).toBe(2);
  const old = (await all(page)).find(s => s.text === 'Frame before body replacement.');
  await page.evaluate(old => {
    const doc = document.querySelector('#frame').contentDocument;
    const next = doc.createElement('body'); next.innerHTML = '<p>Frame after body replacement.</p>';
    doc.body.replaceWith(next);
    window.__readerTranslationBridge.applyTranslations([{ segment_id: old.segment_id, source_text: old.text, translated_text: 'LATE OLD BODY', translation_status: 'succeeded' }], window.epoch);
  }, old);
  await expect.poll(async () => (await all(page)).some(s => s.text === 'Frame after body replacement.')).toBe(true);
  expect(await page.frameLocator('#frame').locator('.reader-translation-node').count()).toBe(0);
  expect((await all(page)).some(s => s.text.includes('Cross origin'))).toBe(false);
});
