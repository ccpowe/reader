import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const source = JSON.parse(readFileSync(resolve('domain/translationRuntimeSources.generated.ts'), 'utf8').match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [resolve('domain/webTranslationRules.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
async function start(page, measured = true, extra = '') {
  await page.setContent('<style>#card span.source-inline { display:var(--inline-display,inline) } #card.blocked span.source-inline { display:block }</style><div id="card"><p id="source">Opening meaningful prose <span class="source-inline">middle meaningful prose</span> ending meaningful prose.</p>' + extra + '</div>');
  await page.evaluate(({ source, rules, measured }) => {
    window.messages = []; window.writes = 0;
    const card = document.querySelector('#card');
    if (measured) new ResizeObserver(() => { card.style.setProperty('--measured-height', `${card.offsetHeight}px`); window.writes++; }).observe(card);
    window.ReactNativeWebView = { postMessage(raw) {
      const message = JSON.parse(raw); window.messages.push(message);
      if (message.type === 'reader_translation_batch_plan') setTimeout(() => window.__readerTranslationBridge.applyTranslations(message.batches.flatMap(batch => batch.segments).map(segment => ({ segment_id: segment.segment_id, source_text: segment.text, translated_text: `稳定译文 ${segment.text}`, translation_status: 'succeeded' })), message.document_epoch), 30);
    } };
    (0, eval)(`(${source})`)({ channelToken: 'feedback-token', initialMode: 'bilingual', rules });
    window.__readerTranslationBridge.confirmEpoch(window.messages[0].document_epoch);
  }, { source, rules, measured });
  await expect(page.locator('#source + .reader-translation-node')).toHaveCount(1);
}

test('host ResizeObserver measurement writes retain successful translation DOM without feedback', async ({ page }) => {
  await start(page);
  await page.evaluate(() => window.firstTranslation = document.querySelector('.reader-translation-node'));
  await page.waitForTimeout(2200);
  expect(await page.evaluate(() => window.firstTranslation === document.querySelector('.reader-translation-node') && window.firstTranslation.isConnected)).toBe(true);
  expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_batch_plan').length)).toBe(1);
  expect(await page.evaluate(() => window.writes)).toBeLessThan(5);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('translated'));
  await page.waitForTimeout(1300);
  expect(await page.evaluate(() => window.firstTranslation === document.querySelector('.reader-translation-node'))).toBe(true);
});

test('real descendant display changes via class or CSS variable still split paragraphs', async ({ page }) => {
  await start(page, false);
  await page.locator('#card').evaluate(card => card.style.setProperty('--inline-display', 'block'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(3);
  await page.locator('#card').evaluate(card => card.style.removeProperty('--inline-display'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await page.locator('#card').evaluate(card => card.classList.add('blocked'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(3);
});

test('exclusion, hidden recovery, source edits and removed translation remain recoverable', async ({ page }) => {
  await start(page, false);
  await page.locator('#card').evaluate(card => card.classList.add('notranslate'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await page.locator('#card').evaluate(card => card.classList.remove('notranslate'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await page.locator('#card').evaluate(card => card.setAttribute('translate', 'no'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await page.locator('#card').evaluate(card => card.removeAttribute('translate'));
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await page.locator('#card').evaluate(card => card.style.display = 'none');
  await page.waitForTimeout(500);
  await page.locator('#card').evaluate(card => card.style.display = 'block');
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await page.locator('#source').evaluate(source => source.firstChild.nodeValue = 'Changed opening prose ');
  await expect(page.locator('.reader-translation-node')).toContainText('Changed opening prose');
  await page.locator('.reader-translation-node').evaluate(node => node.remove());
  await page.evaluate(() => window.__readerTranslationBridge.refresh());
  await expect(page.locator('#source + .reader-translation-node')).toHaveCount(1);
});

test('measurement writes with excluded navigation and SVG preserve existing translations', async ({ page }) => {
  await start(page, true, '<nav><a href="#">Navigation control</a></nav><svg width="20" height="20"><path d="M0 0L10 10"/></svg>');
  await page.evaluate(() => window.firstTranslation = document.querySelector('.reader-translation-node'));
  await page.waitForTimeout(1800);
  expect(await page.evaluate(() => window.firstTranslation.isConnected && window.firstTranslation === document.querySelector('.reader-translation-node'))).toBe(true);
  expect(await page.evaluate(() => window.messages.filter(message => message.type === 'reader_translation_batch_plan').length)).toBe(1);
});

for (const mutation of ['comment', 'decoration', 'counter']) {
  test(`unrelated ${mutation} mutation preserves translated sibling paragraphs`, async ({ page }) => {
    const extra = '<!-- disposable marker --><div id="decoration" aria-hidden="true"><svg width="1" height="1"></svg></div><span id="counter">1</span>' + Array.from({ length: 5 }, (_, i) => `<p>Stable sibling paragraph number ${i} stays translated.</p>`).join('');
    await start(page, false, extra);
    await expect.poll(() => page.locator('.reader-translation-node').count()).toBeGreaterThan(2);
    await page.evaluate(mutation => {
      window.savedTranslations = [...document.querySelectorAll('.reader-translation-node')];
      const card = document.querySelector('#card');
      if (mutation === 'comment') [...card.childNodes].find(node => node.nodeType === Node.COMMENT_NODE).remove();
      else if (mutation === 'decoration') document.querySelector('#decoration').remove();
      else document.querySelector('#counter').firstChild.nodeValue = '2';
    }, mutation);
    await page.waitForTimeout(1100);
    expect(await page.evaluate(() => window.savedTranslations.every(node => node.isConnected))).toBe(true);
  });
}

test('same-text node replacement rebuilds only its source container', async ({ page }) => {
  await start(page, false, '<p id="neighbor">Unchanged neighboring paragraph remains translated.</p>');
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  await page.evaluate(() => {
    window.oldTranslation = document.querySelector('#source + .reader-translation-node');
    window.neighborTranslation = document.querySelector('#neighbor + .reader-translation-node');
    const source = document.querySelector('#source');
    source.replaceChildren(...[...source.childNodes].map(node => node.cloneNode(true)));
  });
  await expect.poll(() => page.evaluate(() => Boolean(document.querySelector('#source + .reader-translation-node')))).toBe(true);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => !window.oldTranslation.isConnected && window.neighborTranslation === document.querySelector('#neighbor + .reader-translation-node'))).toBe(true);
});

test('connected but relocated translations are restored to their source anchor', async ({ page }) => {
  await start(page, false, '<p id="neighbor">Unchanged neighboring paragraph remains translated.</p>');
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  await page.evaluate(() => {
    window.oldTranslation = document.querySelector('#source + .reader-translation-node');
    window.neighborTranslation = document.querySelector('#neighbor + .reader-translation-node');
    document.body.appendChild(window.oldTranslation);
    window.__readerTranslationBridge.refresh();
  });
  await expect(page.locator('#source + .reader-translation-node')).toHaveCount(1);
  expect(await page.evaluate(() => !window.oldTranslation.isConnected && window.neighborTranslation === document.querySelector('#neighbor + .reader-translation-node'))).toBe(true);
});

test('translated-mode partial runs survive unrelated work and still update changed source', async ({ page }) => {
  await start(page, false, '<div id="runs">Opening partial meaningful prose.<p>Independent inner meaningful prose.</p>Closing partial meaningful prose.</div><span id="counter">1</span>');
  await expect(page.locator('.reader-translation-node')).toHaveCount(4);
  await page.evaluate(() => {
    window.__readerTranslationBridge.setMode('translated');
    window.savedTranslations = [...document.querySelectorAll('.reader-translation-node')];
    document.querySelector('#counter').firstChild.nodeValue = '2';
  });
  await page.waitForTimeout(1000);
  expect(await page.evaluate(() => window.savedTranslations.every(node => node.isConnected))).toBe(true);
  await page.evaluate(() => {
    window.__readerTranslationBridge.setMode('bilingual');
    document.querySelector('#runs').firstChild.nodeValue = 'Updated partial meaningful prose.';
  });
  await expect(page.locator('#runs .reader-translation-node').first()).toContainText('Updated partial meaningful prose.');
});

test('structural work survives collapse with deferred host measurement attributes', async ({ page }) => {
  await start(page, true);
  const oldId = await page.locator('.reader-translation-node').getAttribute('data-reader-translation-for');
  await page.locator('#source').evaluate(source => source.firstChild.replaceWith(document.createTextNode(source.firstChild.nodeValue)));
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  await expect(page.locator('.reader-translation-node')).not.toHaveAttribute('data-reader-translation-for', oldId);
  await page.waitForTimeout(800);
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
});
