import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const fixtures = resolve(mobileRoot, 'scripts/fixtures/web-translation-browser');
const generated = readFileSync(resolve(mobileRoot, 'domain/translationRuntimeSources.generated.ts'), 'utf8');
const runtimeMatch = generated.match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/);
if (!runtimeMatch) throw new Error('generated web runtime source is missing');
const runtimeSource = JSON.parse(runtimeMatch[1]);
const rulesBundle = buildSync({
  entryPoints: [resolve(mobileRoot, 'domain/webTranslationRules.ts')],
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  write: false,
}).outputFiles[0].text;
const rulesModule = await import(`data:text/javascript;base64,${Buffer.from(rulesBundle).toString('base64')}`);
const rules = rulesModule.WEB_TRANSLATION_RULE_REGISTRY;

const prose = value => value.replace(/⟪READER_(?:(?:OPEN|CLOSE)_)?\d+⟫/g, '').replace(/\s+/g, ' ').trim();

const messages = (page, type) => page.evaluate((requestedType) =>
  window.__readerMessages.filter((message) => message.type === requestedType), type);

async function startRuntime(page, fixture, pageUrl) {
  if (pageUrl) {
    await page.route(pageUrl, (route) => route.fulfill({
      body: readFileSync(resolve(fixtures, fixture), 'utf8'),
      contentType: 'text/html; charset=utf-8',
    }));
    await page.goto(pageUrl);
  } else {
    await page.goto(pathToFileURL(resolve(fixtures, fixture)).href);
  }
  await page.evaluate(({ source, ruleRegistry }) => {
    window.__readerMessages = [];
    window.ReactNativeWebView = {
      postMessage(value) { window.__readerMessages.push(JSON.parse(value)); },
    };
    const bootstrap = (0, eval)(`(${source})`);
    bootstrap({ channelToken: 'browser-test-token', initialMode: 'bilingual', rules: ruleRegistry });
  }, { source: runtimeSource, ruleRegistry: rules });
  await expect.poll(async () => (await messages(page, 'reader_translation_navigation_reset')).length).toBe(1);
  const reset = (await messages(page, 'reader_translation_navigation_reset'))[0];
  await page.evaluate((epoch) => window.__readerTranslationBridge.confirmEpoch(epoch), reset.document_epoch);
  await expect.poll(async () => (await messages(page, 'reader_translation_status')).at(-1)?.state).toBe('ready');
  return reset;
}

async function latestSegments(page) {
  const plans = await messages(page, 'reader_translation_batch_plan');
  return plans.at(-1)?.batches.flatMap((batch) => batch.segments) ?? [];
}

test('discovers computed block spans without a site-specific selector', async ({ page }) => {
  await startRuntime(page, 'visual-block-span.html');
  const segments = await latestSegments(page);
  expect(segments.map((segment) => segment.text)).toEqual(expect.arrayContaining([
    'Models served through our public endpoints are original unpruned versions.',
    'This block is discovered from computed layout, not its tag name.',
    'Text with an inline phrase remains one paragraph.',
  ]));
  expect(segments.some((segment) => segment.text === 'inline phrase')).toBe(false);
  await page.waitForTimeout(600);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(1);
});

test('discovers a computed block nested below inline ancestors', async ({ page }) => {
  await startRuntime(page, 'inline-nested-block.html');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Opening inline prose.');
  expect(serialized).toContain('Nested computed-block prose must be independent.');
  expect(serialized).toContain('Closing inline prose.');
  const segments = (await messages(page, 'reader_translation_batch_plan')).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(segments.some((segment) => segment.text.includes('Opening inline') && segment.text.includes('Nested computed-block'))).toBe(false);
});

test('projects mixed-depth runs to legal sibling ranges and restores source structure', async ({ page }) => {
  const reset = await startRuntime(page, 'mixed-depth-range.html');
  const originalMarkup = await page.locator('#mixed').innerHTML();
  const segments = (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(segments.map((segment) => prose(segment.text))).toEqual(expect.arrayContaining([
    'Opening prose with inline emphasis',
    'Independent visual block prose.',
    'Closing prose after the block.',
  ]));
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment, index) => ({
      segment_id: segment.segment_id,
      source_text: segment.text,
      translated_text: `Translated range ${index + 1}. ${(segment.text.match(/⟪READER_(?:(?:OPEN|CLOSE)_)?\d+⟫/g) ?? []).join('')}`,
      translation_status: 'succeeded',
    })), epoch,
  ), { epoch: reset.document_epoch, values: segments });
  await page.evaluate(() => window.__readerTranslationBridge.setMode('translated'));
  await expect(page.locator('#mixed .reader-translation-node')).toHaveCount(segments.length);
  const visibleText = await page.locator('#mixed').evaluate((element) => element.innerText);
  expect(visibleText).not.toContain('Opening prose');
  expect(visibleText).not.toContain('Closing prose');
  expect(visibleText).toContain('Translated range 1.');
  expect(visibleText).toContain('Translated range 2.');
  await expect(page.locator('#mixed em .reader-translation-node')).toHaveCount(0);
  expect(await page.locator('#mixed .reader-translation-node').count()).toBeGreaterThanOrEqual(2);
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  await expect(page.locator('#mixed')).toHaveJSProperty('innerHTML', originalMarkup);
  await expect(page.locator('#mixed > .inline-shell > em[data-marker="kept"]')).toHaveCount(1);
});

test('invalidates translated-only mixed-depth source after a host edit inside its wrapper', async ({ page }) => {
  const reset = await startRuntime(page, 'mixed-depth-range.html');
  const segments = (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  const opening = segments.find((segment) => segment.text.includes('Opening prose'));
  const independent = segments.find((segment) => segment.text === 'Independent visual block prose.');
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment) => ({
      segment_id: segment.segment_id,
      source_text: segment.text,
      translated_text: `Old translated result for ${segment.text}`,
      translation_status: 'succeeded',
    })), epoch,
  ), { epoch: reset.document_epoch, values: segments });
  await page.evaluate(id => {
    window.preservedIndependentTranslation = [...document.querySelectorAll('[data-reader-translation-for]')]
      .find(node => node.getAttribute('data-reader-translation-for') === id);
    window.__readerTranslationBridge.setMode('translated');
  }, independent.segment_id);
  await page.locator('#mixed em[data-marker]').evaluate((emphasis) => {
    emphasis.firstChild.nodeValue = 'updated inline emphasis';
  });
  await expect.poll(async () => prose(JSON.stringify(await messages(page, 'reader_translation_batch_plan'))))
    .toContain('Opening prose with updated inline emphasis');
  const revised = (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches.flatMap((batch) => batch.segments))
    .findLast((segment) => segment.text.includes('updated inline emphasis'));
  expect(revised.segment_id).not.toBe(opening.segment_id);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  expect(await page.locator('#mixed').innerText()).not.toContain(`Old translated result for ${prose(opening.text)}`);
  await page.evaluate(({ epoch, original }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: original.segment_id, source_text: original.text,
    translated_text: `LATE OBSOLETE OPENING RESULT ${original.text}`,  translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, original: opening });
  expect(await page.locator('#mixed').innerText()).not.toContain('LATE OBSOLETE OPENING RESULT');
  await page.evaluate(({ epoch, current }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: current.segment_id, source_text: current.text,
    translated_text: `Updated opening translation. ${current.text}`,  translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, current: revised });
  await expect(page.locator('#mixed')).toContainText('Updated opening translation.');
  expect(await page.locator('#mixed').innerText()).not.toContain('LATE OBSOLETE OPENING RESULT');
  expect(await page.evaluate(id => window.preservedIndependentTranslation?.isConnected &&
    window.preservedIndependentTranslation === [...document.querySelectorAll('[data-reader-translation-for]')]
      .find(node => node.getAttribute('data-reader-translation-for') === id), independent.segment_id)).toBe(true);
  expect(await page.locator('#mixed').innerText()).toContain(`Old translated result for ${independent.text}`);
});

test('uses a legal inline translation host for a single deep inline range', async ({ page }) => {
  const reset = await startRuntime(page, 'deep-inline-anchor.html');
  const segment = (await latestSegments(page))[0];
  await page.evaluate(({ epoch, value }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: value.segment_id,
    source_text: value.text,
    translated_text: `Translated deep inline prose. ${value.text}`,
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, value: segment });
  await expect(page.locator('#deep-inline > .reader-translation-node')).toHaveCount(1);
  expect(await page.locator('#deep-inline > .reader-translation-node').evaluate((node) => node.tagName)).toBe('SPAN');
  await expect(page.locator('#deep-inline em > div')).toHaveCount(0);
  await expect(page.locator('#deep-inline > .reader-translation-node em')).toContainText('Deep inline prose');
});

test('keeps whole-block translations inside list items and table cells', async ({ page }) => {
  const reset = await startRuntime(page, 'structured-containers.html');
  const originalList = await page.locator('#structured-list').innerHTML();
  const originalTable = await page.locator('#structured-table').innerHTML();
  const segments = (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(segments.map((segment) => segment.text)).toEqual(expect.arrayContaining([
    'List item prose that requires a legal internal translation host.',
    'Table cell prose that requires a legal internal translation host.',
  ]));
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment) => ({
      segment_id: segment.segment_id,
      source_text: segment.text,
      translated_text: segment.text.startsWith('List item') ? 'Translated list item.' : 'Translated table cell.',
      translation_status: 'succeeded',
    })), epoch,
  ), { epoch: reset.document_epoch, values: segments });
  await expect(page.locator('#structured-list > .reader-translation-node')).toHaveCount(0);
  await expect(page.locator('#structured-list > li > span.reader-translation-node')).toHaveCount(1);
  await expect(page.locator('#structured-table tr > .reader-translation-node')).toHaveCount(0);
  await expect(page.locator('#structured-table td > span.reader-translation-node')).toHaveCount(1);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('translated'));
  expect(await page.locator('#structured-list').innerText()).not.toContain('List item prose that requires');
  expect(await page.locator('#structured-table').innerText()).not.toContain('Table cell prose that requires');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  await expect(page.locator('#structured-list')).toContainText('List item prose that requires');
  await expect(page.locator('#structured-table')).toContainText('Table cell prose that requires');
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  await expect(page.locator('#structured-list')).toHaveJSProperty('innerHTML', originalList);
  await expect(page.locator('#structured-table')).toHaveJSProperty('innerHTML', originalTable);
});

test('preserves an image atom in payload, controlled translation DOM, and mode round trip', async ({ page }) => {
  await page.route('https://example.test/**', (route) => route.fulfill({
    body: Buffer.from('R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=', 'base64'),
    contentType: 'image/gif',
  }));
  const reset = await startRuntime(page, 'image-rich-paragraph.html');
  const segment = (await latestSegments(page))[0];
  expect(segment.text).toContain('⟪READER_0⟫');
  await page.evaluate(({ epoch, value }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: value.segment_id,
    source_text: value.text,
    translated_text: value.text.replace('Open the diagram', '打开图示').replace('before continuing with the explanation', '然后继续阅读说明'),
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, value: segment });
  const translatedImages = page.locator('.reader-translation-node [data-reader-image-alt]');
  await expect(translatedImages).toHaveCount(2);
  await expect(page.locator('.reader-translation-node img')).toHaveCount(0);
  await expect(translatedImages.nth(0)).toHaveText('Architecture diagram');
  await expect(translatedImages.nth(1)).toHaveText('Embedded oversized diagram');
  await expect(translatedImages.nth(0)).not.toHaveAttribute('src', /.+/);
  await expect(translatedImages.nth(1)).not.toHaveAttribute('src', /.+/);
  await expect(translatedImages.nth(0)).not.toHaveAttribute('onerror', /.+/);
  await expect(translatedImages.nth(0)).not.toHaveAttribute('class', /host-image/);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('translated'));
  await expect(page.locator('#image-rich')).toBeHidden();
  await expect(translatedImages.nth(0)).toBeVisible();
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  await expect(page.locator('#image-rich > img.host-image')).toBeVisible();
  await expect(translatedImages.nth(0)).toBeHidden();
});

test('does not request translation for preserved-only images, linked images or code', async ({ page }) => {
  await startRuntime(page, 'preserved-only-content.html');
  const segments = await latestSegments(page);
  expect(segments).toHaveLength(2);
  expect(segments.map((segment) => segment.text).join(' ')).toContain('Visible explanatory prose');
  expect(segments.map((segment) => segment.text).join(' ')).toContain('Useful reference text');
  expect(segments.every((segment) => /[A-Za-z]/.test(segment.text.replace(/⟪READER_(?:(?:OPEN|CLOSE)_)?\d+⟫/g, '')))).toBe(true);
  await expect(page.locator('main img')).toHaveCount(2);
});

test('groups inline semantics and preserves stay-original variables', async ({ page }) => {
  const reset = await startRuntime(page, 'inline-rich-paragraph.html');
  const segments = await latestSegments(page);
  expect(segments).toHaveLength(1);
  expect(segments[0].text).toContain('⟪READER_0⟫');
  expect(prose(segments[0].text)).toContain('open the documentation');
  await page.evaluate(({ epoch, result }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: result.segment_id,
    source_text: result.text,
    translated_text: 'A response that dropped every protected variable.',
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, result: segments[0] });
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  const translated = segments[0].text.replace('Install', '安装').replace('then', '然后').replace('to continue', '以继续');
  await page.evaluate(({ epoch, result }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: result.segment_id,
    source_text: result.text,
    translated_text: result.translated,
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, result: { ...segments[0], translated } });
  await expect(page.locator('.reader-translation-node')).toContainText('reader-cli');
  await expect(page.locator('.reader-translation-node a[href="#docs"]')).toContainText('documentation');
  await expect(page.locator('.reader-translation-node code')).toContainText('reader-cli');
  await expect(page.locator('.reader-translation-node kbd')).toContainText('Enter');
  await page.locator('.reader-translation-node a[href="#docs"]').evaluate((link) => {
    link.addEventListener('click', (event) => { event.preventDefault(); window.__readerFixtureLinkClicked = true; }, { once: true });
  });
  await page.locator('.reader-translation-node a[href="#docs"]').click();
  expect(await page.evaluate(() => window.__readerFixtureLinkClicked)).toBe(true);
  await expect(page.locator('#rich')).toContainText('Install reader-cli');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('translated'));
  await expect(page.locator('#rich')).toBeHidden();
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  await expect(page.locator('#rich')).toBeVisible();
  await expect(page.locator('#rich')).toContainText('Install reader-cli');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  await expect(page.locator('.reader-translation-node')).toBeVisible();
  await expect(page.locator('#rich')).toBeVisible();
  const plansAfterSuccess = (await messages(page, 'reader_translation_batch_plan')).length;
  await page.evaluate(() => { window.scrollTo(0, 1); window.dispatchEvent(new Event('resize')); });
  await page.waitForTimeout(400);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(plansAfterSuccess);
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
  await page.waitForTimeout(200);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(plansAfterSuccess);
});

test('does not collapse nested block paragraphs into their parent', async ({ page }) => {
  await startRuntime(page, 'nested-blocks.html');
  const segments = await latestSegments(page);
  expect(segments.map((segment) => segment.text)).toEqual(expect.arrayContaining([
    'Nested layout heading',
    'First independent paragraph inside the section.',
    'Second independent paragraph inside the section.',
  ]));
  expect(segments.some((segment) => segment.text.includes('First independent') && segment.text.includes('Second independent'))).toBe(false);
});

test('excludes hidden/chrome content and discovers it after becoming visible', async ({ page }) => {
  await startRuntime(page, 'excluded-hidden-content.html');
  let allPlans = await messages(page, 'reader_translation_batch_plan');
  expect(JSON.stringify(allPlans)).toContain('Visible article paragraph');
  expect(JSON.stringify(allPlans)).not.toContain('Navigation must not translate');
  expect(JSON.stringify(allPlans)).not.toContain('This paragraph becomes visible dynamically');
  await page.locator('#hidden').evaluate((element) => { element.style.display = 'block'; });
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan'))).toContain('This paragraph becomes visible dynamically');
});

test('locally rebuilds host mutations and ignores runtime-owned writes', async ({ page }) => {
  const reset = await startRuntime(page, 'dynamic-content.html');
  const first = (await latestSegments(page))[0];
  const plansBeforeRender = (await messages(page, 'reader_translation_batch_plan')).length;
  await page.evaluate(({ epoch, segment }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: segment.segment_id, source_text: segment.text,
    translated_text: 'Translated dynamic paragraph.', translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, segment: first });
  await page.waitForTimeout(450);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(plansBeforeRender);
  await page.locator('#content').evaluate((content) => {
    const paragraph = document.createElement('p');
    paragraph.textContent = 'New paragraph added by the host application.';
    content.appendChild(paragraph);
  });
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan'))).toContain('New paragraph added by the host application.');
});

test('does not swallow a host class mutation in the runtime attribute checkpoint', async ({ page }) => {
  const reset = await startRuntime(page, 'dynamic-content.html');
  const first = (await latestSegments(page))[0];
  await page.evaluate(({ epoch, segment }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: segment.segment_id,
    source_text: segment.text,
    translated_text: 'Translated dynamic paragraph.',
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, segment: first });
  const before = (await messages(page, 'reader_translation_batch_plan')).length;
  await page.locator('#content p').evaluate((paragraph) => {
    // setMode writes our source-hidden class. The host's semantic exclusion
    // follows in the same MutationObserver checkpoint and must not be mistaken
    // for another owned class write. Pure padding changes no longer invalidate.
    window.__readerTranslationBridge.setMode('translated');
    paragraph.classList.add('notranslate');
  });
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await expect(page.locator('#content p')).toHaveClass(/notranslate/);
  await page.locator('#content p').evaluate((paragraph) => {
    window.__readerTranslationBridge.setMode('bilingual');
    paragraph.classList.remove('notranslate');
  });
  await expect.poll(async () => (await messages(page, 'reader_translation_batch_plan')).length).toBeGreaterThan(before);
  expect((await latestSegments(page)).some(segment => segment.text === first.text)).toBe(true);
});

test('does not reschedule a failed entity until an explicit reset', async ({ page }) => {
  const reset = await startRuntime(page, 'dynamic-content.html');
  const segment = (await latestSegments(page))[0];
  const before = (await messages(page, 'reader_translation_batch_plan')).length;
  await page.evaluate(({ epoch, item }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: item.segment_id, source_text: item.text, translation_status: 'failed',
  }], epoch), { epoch: reset.document_epoch, item: segment });
  await page.evaluate(() => window.dispatchEvent(new Event('scroll')));
  await page.waitForTimeout(300);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(before);
  await page.evaluate(() => window.__readerTranslationBridge.resetTranslations());
  await expect.poll(async () => (await messages(page, 'reader_translation_batch_plan')).length).toBe(before + 1);
});

test('rejects stale results after the host changes paragraph text', async ({ page }) => {
  const reset = await startRuntime(page, 'dynamic-content.html');
  const original = (await latestSegments(page))[0];
  await page.locator('#content p').evaluate((paragraph) => {
    paragraph.firstChild.nodeValue = 'Updated paragraph from the host application.';
  });
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan'))).toContain('Updated paragraph from the host application.');
  await page.evaluate(({ epoch, segment }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: segment.segment_id,
    source_text: segment.text,
    translated_text: 'This stale translation must not render.',
    translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, segment: original });
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
});

test('uses a new segment identity for an A to B to A source cycle', async ({ page }) => {
  const reset = await startRuntime(page, 'dynamic-content.html');
  const firstA = (await latestSegments(page))[0];
  await page.locator('#content p').evaluate((paragraph) => { paragraph.firstChild.nodeValue = 'Temporary B paragraph revision.'; });
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan'))).toContain('Temporary B paragraph revision.');
  await page.locator('#content p').evaluate((paragraph) => { paragraph.firstChild.nodeValue = 'Initial dynamic article paragraph.'; });
  await expect.poll(async () => {
    const all = (await messages(page, 'reader_translation_batch_plan')).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
    return new Set(all.filter((segment) => segment.text === firstA.text).map((segment) => segment.segment_id)).size;
  }).toBe(2);
  const allA = (await messages(page, 'reader_translation_batch_plan')).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments)).filter((segment) => segment.text === firstA.text);
  expect(allA.at(-1).segment_id).not.toBe(firstA.segment_id);
  await page.evaluate(({ epoch, segment }) => window.__readerTranslationBridge.applyTranslations([{
    segment_id: segment.segment_id, source_text: segment.text,
    translated_text: 'Earliest stale A translation.', translation_status: 'succeeded',
  }], epoch), { epoch: reset.document_epoch, segment: firstA });
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
});

test('disposes removed paragraphs and never schedules them again', async ({ page }) => {
  await startRuntime(page, 'dynamic-content.html');
  const first = (await latestSegments(page))[0];
  await page.locator('#content p').evaluate((paragraph) => paragraph.remove());
  await page.waitForTimeout(600);
  await page.evaluate(() => { window.scrollTo(0, 1); window.dispatchEvent(new Event('resize')); });
  await page.waitForTimeout(300);
  const later = (await messages(page, 'reader_translation_batch_plan')).slice(1).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(later.some((segment) => segment.segment_id === first.segment_id)).toBe(false);
  const diagnostics = await messages(page, 'reader_translation_engine_diagnostics');
  expect(diagnostics.at(-1).paragraphs_detected).toBe(0);
});

test('prunes disconnected roots from every traversal work collection', async ({ page }) => {
  await startRuntime(page, 'dynamic-content.html');
  await page.locator('#content').evaluate((content) => {
    const transient = document.createElement('section');
    transient.innerHTML = '<p>Transient paragraph removed before traversal stability.</p>';
    content.appendChild(transient);
    transient.remove();
  });
  await page.waitForTimeout(1_500);
  const diagnostics = (await messages(page, 'reader_translation_engine_diagnostics')).at(-1);
  expect(diagnostics.pending_roots).toBe(0);
  expect(diagnostics.dirty_roots).toBe(0);
  expect(diagnostics.deferred_roots).toBe(0);
  expect(diagnostics.processing_roots).toBe(0);
});

test('creates a fresh document epoch for SPA and body replacement', async ({ page }) => {
  const first = await startRuntime(page, 'spa-body-replacement.html');
  await page.evaluate(() => history.pushState({}, '', '?article=second'));
  await expect.poll(async () => (await messages(page, 'reader_translation_navigation_reset')).length).toBe(2);
  const second = (await messages(page, 'reader_translation_navigation_reset')).at(-1);
  expect(second.document_epoch).not.toBe(first.document_epoch);
  await page.evaluate(() => {
    const body = document.createElement('body');
    body.innerHTML = '<main><p>Replacement body article paragraph.</p></main>';
    document.documentElement.replaceChild(body, document.body);
  });
  await expect.poll(async () => (await messages(page, 'reader_translation_navigation_reset')).length).toBe(3);
  const third = (await messages(page, 'reader_translation_navigation_reset')).at(-1);
  expect(third.document_epoch).not.toBe(second.document_epoch);
});

test('discovers live article content while excluding site navigation', async ({ page }) => {
  await startRuntime(page, 'readability-article.html');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Readable article title');
  expect(serialized).toContain('sufficiently detailed article paragraph');
  expect(serialized).not.toContain('Site navigation and account tools');
  await expect(page.locator('#story')).toContainText('sufficiently detailed article paragraph');
});

test('ignores runtime and host mutations outside the active article root', async ({ page }) => {
  await startRuntime(page, 'explicit-content-root.html');
  await page.waitForTimeout(400);
  const before = (await messages(page, 'reader_translation_batch_plan')).length;
  await page.locator('aside').evaluate((aside) => { aside.textContent = 'Outside root mutation must never become a segment.'; });
  await page.waitForTimeout(700);
  const plans = await messages(page, 'reader_translation_batch_plan');
  expect(plans).toHaveLength(before);
  expect(JSON.stringify(plans)).not.toContain('Outside root mutation');
});

test('includes article metadata and supplementary sections, including subsequent edits', async ({ page }) => {
  await startRuntime(page, 'full-page-content.html');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  for (const text of [
    'Research findings and practical implications', 'A summary that belongs to the article header.',
    'This is a sufficiently detailed opening paragraph', 'The second paragraph adds independent evidence',
    'Important caveat:', 'Correction:', 'Supplementary comparison results outside the article',
    'A sibling supplementary note outside main still contributes useful evidence.',
  ]) expect(serialized).toContain(text);
  for (const text of ['Site navigation', 'Private navigation', 'Private form', 'Site account']) {
    expect(serialized).not.toContain(text);
  }
  await page.locator('main > section p').evaluate((element) => {
    element.textContent = 'Updated supplementary results must also be translated.';
  });
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan')))
    .toContain('Updated supplementary results must also be translated.');
});

test('maps all Readability candidates before prioritizing and drains a dense viewport without scrolling', async ({ page }) => {
  await page.goto(pathToFileURL(resolve(fixtures, 'readability-paragraph-id.html')).href);
  await page.addScriptTag({ path: resolve(mobileRoot, 'node_modules/@mozilla/readability/Readability.js') });
  const extraction = await page.evaluate(() => {
    const article = new window.Readability(document.cloneNode(true)).parse();
    const parsed = new DOMParser().parseFromString(article.content, 'text/html');
    return {
      ids: [...parsed.querySelectorAll('[id]')].map((element) => element.id),
      paragraphCount: parsed.querySelectorAll('p').length,
      textLength: article.length,
    };
  });
  // This specifically exercises the old first-id mapping failure, rather
  // than accidentally testing a Readability parse failure/body fallback.
  expect(extraction.ids.filter((id) => id !== 'readability-page-1')[0]).toBe('opening');
  expect(extraction.paragraphCount).toBe(4);
  expect(extraction.textLength).toBeGreaterThan(800);
  const reset = await startRuntime(page, 'readability-paragraph-id.html');
  const first = await latestSegments(page);
  expect(first.slice(0, 4).map((segment) => segment.text.split(':')[0])).toEqual([
    'Opening research paragraph', 'Second research paragraph', 'Third research paragraph', 'Closing research paragraph',
  ]);
  expect(first).toHaveLength(10);
  expect(first.some((segment) => segment.text.startsWith('Supporting detail'))).toBe(true);
  await expect(page.locator('[data-reader-analysis-node]')).toHaveCount(0);
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment) => ({ segment_id: segment.segment_id, source_text: segment.text,
      translated_text: 'Translated.', translation_status: 'succeeded' })), epoch,
  ), { epoch: reset.document_epoch, values: first });
  await expect.poll(async () => (await messages(page, 'reader_translation_batch_plan')).length).toBe(2);
  const second = await latestSegments(page);
  expect(second).toHaveLength(6);
  expect(second.every((segment) => !first.some((prior) => prior.segment_id === segment.segment_id))).toBe(true);
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment) => ({ segment_id: segment.segment_id, source_text: segment.text,
      translated_text: 'Translated.', translation_status: 'succeeded' })), epoch,
  ), { epoch: reset.document_epoch, values: second });
  await page.waitForTimeout(700);
  expect((await messages(page, 'reader_translation_batch_plan')).length).toBe(2);
  await expect(page.locator('.reader-translation-node')).toHaveCount(16);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('treats ordinary site roots as priority hints while retaining supplemental content', async ({ page }) => {
  await startRuntime(page, 'full-page-content.html', 'https://docs.github.com/en/example');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Supplementary comparison results outside the article');
  expect(serialized).not.toContain('Private navigation');
});

test('sends valid single segments larger than aggregate batch budgets', async ({ page }) => {
  await startRuntime(page, 'long-single-segments.html');
  const all = (await messages(page, 'reader_translation_batch_plan')).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(all.some((segment) => segment.text.startsWith('Urgent substantial') && segment.text.length > 3_000)).toBe(true);
  expect(all.some((segment) => segment.text.startsWith('Prefetch substantial') && segment.text.length > 4_000)).toBe(true);
});

test('defers distant long-page containers until they approach the viewport', async ({ page }) => {
  await startRuntime(page, 'long-page.html');
  let serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Visible paragraph at the beginning');
  expect(serialized).not.toContain('Eleventh content block');
  const firstPlans = await messages(page, 'reader_translation_batch_plan');
  for (const batch of firstPlans.flatMap((plan) => plan.batches)) {
    expect(batch.segments.length).toBeLessThanOrEqual(batch.priority === 'urgent' ? 3 : 5);
  }
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan'))).toContain('Eleventh content block');
});

test('plans an appended long-page child without rescanning the active root', async ({ page }) => {
  await startRuntime(page, 'long-page.html');
  const before = (await messages(page, 'reader_translation_engine_diagnostics')).at(-1);
  await page.locator('main').evaluate((main) => {
    const section = document.createElement('section');
    section.id = 'appended-distant';
    section.innerHTML = '<p>Dynamically appended distant paragraph.</p>';
    main.appendChild(section);
  });
  await page.waitForTimeout(1_000);
  const after = (await messages(page, 'reader_translation_engine_diagnostics')).at(-1);
  expect(after.tree_nodes - before.tree_nodes).toBeLessThan(10);
  expect(after.paragraphs_detected - before.paragraphs_detected).toBeLessThan(3);
  await page.locator('#appended-distant').scrollIntoViewIfNeeded();
  await expect.poll(async () => JSON.stringify(await messages(page, 'reader_translation_batch_plan')))
    .toContain('Dynamically appended distant paragraph.');
});

test('retains direct text owned by a root taller than ten viewports', async ({ page }) => {
  await startRuntime(page, 'long-root-direct-text.html');
  expect(JSON.stringify(await messages(page, 'reader_translation_batch_plan')))
    .toContain('Direct prose owned by the long root must remain translatable.');
});

test('retains inline prose around block children of a long root without merging across them', async ({ page }) => {
  const reset = await startRuntime(page, 'long-root-inline-content.html');
  const allSegments = async () => (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  const initial = await allSegments();
  expect(initial.some((segment) => prose(segment.text).includes('Valuable introduction with inline emphasis'))).toBe(true);
  await expect.poll(async () => (await allSegments()).some((segment) => segment.text.includes('Primary article paragraph'))).toBe(true);
  expect(initial.some((segment) => segment.text.includes('Valuable introduction') && segment.text.includes('Primary article'))).toBe(false);
  await page.locator('#ending').scrollIntoViewIfNeeded();
  await expect.poll(async () => (await allSegments()).some((segment) => segment.text.includes('Supplementary conclusion'))).toBe(true);
  const unique = [...new Map((await allSegments()).map((segment) => [segment.segment_id, segment])).values()];
  await page.evaluate(({ epoch, values }) => window.__readerTranslationBridge.applyTranslations(
    values.map((segment) => ({ segment_id: segment.segment_id, source_text: segment.text,
      translated_text: `Translated: ${segment.text}`, translation_status: 'succeeded' })), epoch,
  ), { epoch: reset.document_epoch, values: unique });
  await expect(page.locator('.reader-translation-node')).toHaveCount(3);
  await expect(page.locator('#intro > em')).toHaveText('inline emphasis');
  await expect(page.locator('#intro > a')).toHaveAttribute('href', '/evidence');
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await expect(page.locator('#ending')).toHaveText('Supplementary conclusion after the long article.');
});

test('replans queued identities after viewport replacement and upgrades prefetch to urgent', async ({ page }) => {
  await startRuntime(page, 'long-page.html');
  const initialPlans = await messages(page, 'reader_translation_batch_plan');
  const prefetch = initialPlans.flatMap((plan) => plan.batches)
    .find((batch) => batch.priority === 'prefetch' && batch.segments.length)?.segments[0];
  expect(prefetch).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(300);
  await page.evaluate(() => window.scrollTo(0, 850));
  await expect.poll(async () => (await messages(page, 'reader_translation_batch_plan'))
    .flatMap((plan) => plan.batches)
    .some((batch) => batch.priority === 'urgent' && batch.segments.some((segment) => segment.segment_id === prefetch.segment_id))).toBe(true);
});

test('all compiled selectors are valid in Chromium', async ({ page }) => {
  await page.setContent('<main><p>selector validation</p></main>');
  const selectors = rules.flatMap((rule) => [
    ...rule.scope.mainRoots,
    ...rule.segmentation.exclude,
    ...rule.segmentation.stayOriginal,
    ...rule.segmentation.forceBlock,
    ...rule.segmentation.forceInline,
    ...rule.segmentation.atomic,
    ...rule.segmentation.preformatted,
  ]);
  const invalid = await page.evaluate((values) => values.flatMap((value) => {
    try { document.querySelector(value); return []; } catch { return [value]; }
  }), selectors);
  expect(invalid).toEqual([]);
});

const profileFixtures = [
  ['wikipedia.html', 'https://en.wikipedia.org/wiki/Translation', 'wikipedia', 'Wikipedia article prose', 'Related navigation'],
  ['mdn.html', 'https://developer.mozilla.org/en-US/docs/Web/API/Node', 'mdn', 'MDN documentation prose', 'MDN sidebar'],
  ['github-docs.html', 'https://docs.github.com/en/get-started', 'github-docs', 'GitHub documentation prose', 'Article footer'],
  ['bbc.html', 'https://www.bbc.com/news/articles/test', 'bbc', 'BBC news article prose', 'Related news'],
  ['react-docs.html', 'https://react.dev/learn/components', 'react-docs', 'React documentation explains', "const example"],
  ['stackoverflow.html', 'https://stackoverflow.com/questions/1/example', 'stackoverflow', 'Stack Overflow question prose', 'Comments excluded'],
  ['hacker-news-item.html', 'https://news.ycombinator.com/item?id=1', 'hacker-news-item', 'Hacker News item title', 'reply metadata'],
  ['devto.html', 'https://dev.to/example/post', 'devto', 'Dev Community article prose', 'Footer controls'],
  ['medium.html', 'https://medium.com/example/story', 'medium', 'Medium story prose', 'Responses excluded'],
  ['ars-technica.html', 'https://arstechnica.com/example/article', 'ars-technica', 'Ars Technica article prose', 'Related posts'],
];

for (const [fixture, url, profileId, included, excluded] of profileFixtures) {
  test(`applies sparse ${profileId} rule in a real document`, async ({ page }) => {
    await startRuntime(page, fixture, url);
    const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
    expect(serialized).toContain(included);
    expect(serialized).not.toContain(excluded);
    await expect(page.locator('html')).toHaveAttribute('data-reader-translation-profile', profileId);
  });
}

test('keeps the Reddit adapter fail-closed and scoped to Reddit prose', async ({ page }) => {
  await startRuntime(page, 'reddit.html', 'https://www.reddit.com/r/example/comments/1/post');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Reddit post title for translation');
  expect(serialized).toContain('Reddit post body paragraph');
  expect(serialized).not.toContain('privateHydration');
  const segments = (await messages(page, 'reader_translation_batch_plan')).flatMap((plan) => plan.batches.flatMap((batch) => batch.segments));
  expect(segments.filter((segment) => segment.text.includes('Reddit post body paragraph'))).toHaveLength(1);
});

test('keeps the X adapter fail-closed and excludes posting metadata', async ({ page }) => {
  await startRuntime(page, 'x.html', 'https://x.com/example/status/123456789012345678');
  const serialized = JSON.stringify(await messages(page, 'reader_translation_batch_plan'));
  expect(serialized).toContain('Semantic X post text should be translated');
  expect(serialized).not.toContain('Account metadata must be excluded');
});

test('selection diagnostics explain exclusions without exporting page text and clear across epochs', async ({ page }) => {
  await startRuntime(page, 'full-page-content.html');
  const inspect = async (selector, nonce) => page.evaluate(({ selector, nonce }) => {
    const selection = window.getSelection();
    if (selector) {
      const range = document.createRange();
      range.selectNodeContents(document.querySelector(selector));
      selection.removeAllRanges();
      selection.addRange(range);
    }
    window.__readerTranslationBridge.diagnoseSelection(nonce);
    return window.__readerMessages.filter(m => m.type === 'reader_translation_selection_diagnostic').at(-1);
  }, { selector, nonce });
  expect((await inspect(null, 'initial')).reason).toBe('no_selection');
  const excluded = await inspect('nav', 'excluded');
  expect(excluded.reason).toBe('excluded');
  expect(excluded.rule_index).toBeGreaterThanOrEqual(0);
  expect(JSON.stringify(excluded)).not.toContain('Private navigation');
  const included = await inspect('#opening', 'included');
  expect(['discovered', 'queued']).toContain(included.reason);
  expect(included.segment_id).toBeTruthy();
  expect(JSON.stringify(included)).not.toContain('methodology');
  await page.evaluate(() => {
    window.getSelection().removeAllRanges();
    document.dispatchEvent(new Event('selectionchange'));
  });
  expect((await inspect(null, 'retained')).segment_id).toBe(included.segment_id);
  await page.evaluate(() => {
    history.replaceState({}, '', '#new-epoch');
    window.__readerTranslationBridge.refresh();
  });
  expect((await inspect(null, 'new-document')).reason).toBe('no_selection');
});

test('selection display diagnostics distinguish clipping, mode, hidden hosts and viewport without retrying', async ({ page }) => {
  const reset = await startRuntime(page, 'display-diagnostics.html');
  const segment = (await latestSegments(page)).find(segment => segment.text.startsWith('This meaningful'));
  expect(segment).toBeTruthy();
  const inspect = () => page.evaluate(() => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.querySelector('#source'));
    selection.removeAllRanges(); selection.addRange(range);
    const before = document.documentElement.outerHTML;
    const plans = window.__readerMessages.filter(m => m.type === 'reader_translation_batch_plan').length;
    window.__readerTranslationBridge.diagnoseSelection('display-test');
    if (before !== document.documentElement.outerHTML || plans !== window.__readerMessages.filter(m => m.type === 'reader_translation_batch_plan').length) throw new Error('diagnosis mutated DOM or scheduler');
    return window.__readerMessages.filter(m => m.type === 'reader_translation_selection_diagnostic').at(-1);
  });
  expect((await inspect()).display_status).toBe('not_inserted');
  await page.evaluate(({ segment, epoch }) => window.__readerTranslationBridge.applyTranslations([
    { segment_id: segment.segment_id, source_text: segment.text, translated_text: '这是一段中文译文，用于证明译文已经插入，但在折叠描述中被裁剪。', translation_status: 'succeeded' },
  ], epoch), { segment, epoch: reset.document_epoch });
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
  const geometry = await page.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.querySelector('.reader-translation-node'));
    return { bottom: document.querySelector('#clip').getBoundingClientRect().bottom,
      textBottom: Math.max(...[...range.getClientRects()].map(rect => rect.bottom)) };
  });
  expect(geometry.textBottom).toBeGreaterThan(geometry.bottom + 1);
  expect(await inspect()).toMatchObject({ reason: 'translated', display_status: 'clipped' });
  await page.evaluate(() => { document.querySelector('#clip').style.webkitLineClamp = 'unset'; });
  expect((await inspect()).display_status).toBe('visible');
  for (const property of ['display', 'visibility']) {
    await page.evaluate(property => { document.querySelector('#clip').style[property] = property === 'display' ? 'none' : 'hidden'; }, property);
    expect((await inspect()).display_status).toBe('host_hidden');
    await page.evaluate(property => { document.querySelector('#clip').style[property] = ''; }, property);
  }
  await page.evaluate(() => { document.querySelector('#clip').style.color = 'transparent'; });
  expect((await inspect()).display_status).toBe('unknown');
  await page.evaluate(() => { document.querySelector('#clip').style.color = ''; });
  await page.evaluate(() => { document.querySelector('#clip').style.clipPath = 'inset(0)'; });
  expect((await inspect()).display_status).toBe('unknown');
  await page.evaluate(() => { document.querySelector('#clip').style.clipPath = ''; });
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  expect((await inspect()).display_status).toBe('mode_hidden');
  await page.evaluate(() => window.__readerTranslationBridge.setMode('bilingual'));
  await page.evaluate(() => { document.querySelector('#clip').style.opacity = '0'; });
  expect((await inspect()).display_status).toBe('host_hidden');
  await page.evaluate(() => { document.querySelector('#clip').style.opacity = '1'; document.querySelector('#clip').style.height = '30px'; });
  expect((await inspect()).display_status).toBe('clipped');
  await page.evaluate(() => { document.querySelector('#clip').style.height = 'auto'; document.querySelector('#clip').style.marginTop = '3000px'; });
  expect((await inspect()).display_status).toBe('offscreen');
  expect((await inspect()).reason).toBe('translated');
  await page.evaluate(() => {
    const host = document.querySelector('#clip'); host.style.marginTop = '0';
    const text = document.querySelector('.reader-translation-node');
    const rect = text.getBoundingClientRect(); host.style.marginTop = `${window.innerHeight - rect.top - 10}px`;
  });
  expect((await inspect()).display_status).toBe('partial');
  await page.evaluate(() => {
    document.querySelector('#clip').style.marginTop = '0';
    document.querySelector('#clip').style.width = '60px';
    document.querySelector('.reader-translation-node').style.setProperty('white-space', 'nowrap', 'important');
  });
  expect((await inspect()).display_status).toBe('clipped');
  await page.evaluate(() => { document.querySelector('main').style.transform = 'scale(0.5)'; });
  expect((await inspect()).display_status).toBe('unknown');
  await page.evaluate(() => {
    document.querySelector('main').style.transform = '';
    document.querySelector('#clip').style.width = '260px';
    const text = document.querySelector('.reader-translation-node'); text.style.whiteSpace = '';
    const hidden = document.createElement('span'); hidden.style.visibility = 'hidden';
    while (text.firstChild) hidden.append(text.firstChild); text.append(hidden);
  });
  expect((await inspect()).display_status).toBe('unknown');
});

test('expired webpage translations leave the DOM and cannot be replayed', async ({ page }) => {
  await startRuntime(page, 'visual-block-span.html');
  const segment = (await latestSegments(page))[0];
  const epoch = (await messages(page, 'reader_translation_navigation_reset'))[0].document_epoch;
  const result = { ...segment, source_text: segment.text, translated_text: segment.text,
    translation_status: 'succeeded', cache_expires_at: new Date(Date.now() + 1500).toISOString() };
  await page.evaluate(({ result, epoch }) => window.__readerTranslationBridge.applyTranslations([result], epoch), { result, epoch });
  await expect(page.locator('[data-reader-translation-for]')).toHaveCount(1);
  await expect(page.locator('[data-reader-translation-for]')).toHaveCount(0);
  await page.evaluate(({ result, epoch }) => window.__readerTranslationBridge.applyTranslations([result], epoch), { result, epoch });
  await expect(page.locator('[data-reader-translation-for]')).toHaveCount(0);
  await expect.poll(async () => (await messages(page, 'reader_translation_batch_plan')).length).toBeGreaterThan(1);
});
