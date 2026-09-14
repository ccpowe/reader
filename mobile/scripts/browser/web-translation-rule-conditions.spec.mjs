import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { resolve } from 'node:path';

const bundle = buildSync({ entryPoints: [resolve('domain/webTranslationRules.ts')], bundle: true,
  format: 'iife', globalName: 'ReaderRules', platform: 'browser', write: false }).outputFiles[0].text;

async function prepare(page) {
  await page.setContent('<main class="article" data-template="story"><p>Example story.</p></main>');
  await page.addScriptTag({ content: bundle });
  await page.evaluate(() => {
    const api = window.ReaderRules;
    window.testRules = [api.compileWebTranslationRule({ id: 'story', priority: 20, matches: [{
      host: 'example.test', pathPrefix: '/read', excludePathPrefixes: ['/read/admin'],
      requiredSelectors: ['main.article', '[data-template="story"]'], absentSelectors: ['.dashboard'],
    }] }), ...api.WEB_TRANSLATION_RULE_REGISTRY];
  });
}
const selected = page => page.evaluate(() => window.ReaderRules.selectWebTranslationRule('https://example.test/read/one', window.testRules, document).id);

test('same URL selects one template condition and returns to fallback when template changes', async ({ page }) => {
  await prepare(page);
  expect(await selected(page)).toBe('story');
  await page.locator('main').evaluate(node => node.classList.add('dashboard'));
  expect(await selected(page)).toBe('generic');
  await page.locator('main').evaluate(node => { node.classList.remove('dashboard'); node.removeAttribute('data-template'); });
  expect(await selected(page)).toBe('generic');
  await page.locator('main').evaluate(node => node.setAttribute('data-template', 'story'));
  expect(await selected(page)).toBe('story');
  expect(await page.evaluate(() => window.ReaderRules.selectWebTranslationRule('https://example.test/read/admin', window.testRules, document).id)).toBe('generic');
  expect(await page.evaluate(() => window.ReaderRules.selectWebTranslationRule('https://different.test/read/one', window.testRules, document).id)).toBe('generic');
});

test('conditions query the supplied iframe document and reject malformed selectors conservatively', async ({ page }) => {
  await prepare(page);
  await page.evaluate(async () => {
    const frame = document.createElement('iframe');
    frame.srcdoc = '<main class="dashboard">Frame template.</main>';
    const loaded = new Promise(resolve => frame.addEventListener('load', resolve, { once: true }));
    document.body.append(frame); await loaded;
  });
  expect(await selected(page)).toBe('story');
  expect(await page.evaluate(() => window.ReaderRules.selectWebTranslationRule('https://example.test/read/one', window.testRules, document.querySelector('iframe').contentDocument).id)).toBe('generic');
  expect(await page.evaluate(() => {
    // A corrupt serialized rule must not turn an invalid absent condition into a match.
    const corrupt = { ...window.testRules[0], matches: [{ host: 'example.test', absentSelectors: ['[broken'] }] };
    return window.ReaderRules.selectWebTranslationRule('https://example.test/read/one', [corrupt], document).id;
  })).toBe('generic');
});

test('generated translation elements do not satisfy source template conditions', async ({ page }) => {
  await prepare(page);
  expect(await page.evaluate(() => {
    const rule = window.ReaderRules.compileWebTranslationRule({ id: 'links', priority: 30, matches: [{ host: 'example.test', requiredSelectors: ['a'] }] });
    const translated = document.createElement('span'); translated.className = 'reader-translation-node';
    translated.innerHTML = '<a href="#">Translated link</a>'; document.body.append(translated);
    const registry = [rule, ...window.testRules];
    return window.ReaderRules.selectWebTranslationRule('https://example.test/read/one', registry, document).id;
  })).toBe('story');
});
