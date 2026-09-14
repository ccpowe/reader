import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const source = JSON.parse(readFileSync(`${root}/domain/translationRuntimeSources.generated.ts`, 'utf8').match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [`${root}/domain/webTranslationRules.ts`], bundle: true, write: false, platform: 'node', format: 'esm' }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules, compileWebTranslationRule } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);

async function load(page, html, extraRules = []) {
  await page.route('https://generic.example.test/**', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>body{font:16px/24px Arial;margin:16px;color:#243142}h1{font-size:30px;line-height:38px;font-weight:700}p{margin:12px 0} main{max-width:720px}button{width:45px;height:30px}</style>${html}` }));
  await page.goto('https://generic.example.test/article');
  await page.evaluate(({ source, rules }) => {
    window.messages = [];
    window.ReactNativeWebView = { postMessage(value) {
      const message = JSON.parse(value); messages.push(message);
      if (message.type === 'reader_translation_navigation_reset') queueMicrotask(() => __readerTranslationBridge.confirmEpoch(message.document_epoch));
    } };
    (0, eval)(`(${source})`)({ channelToken: 'generic-rendering', initialMode: 'bilingual', rules });
  }, { source, rules: [...extraRules, ...rules] });
}
const segments = page => page.evaluate(() => [...new Map(messages.filter(m => m.type === 'reader_translation_batch_plan').flatMap(m => m.batches.flatMap(b => b.segments)).map(s => [s.segment_id, s])).values()]);
async function apply(page, count) {
  await expect.poll(async () => (await segments(page)).length).toBe(count);
  await page.evaluate(() => {
    const segments = [...new Map(messages.filter(m => m.type === 'reader_translation_batch_plan').flatMap(m => m.batches.flatMap(b => b.segments)).map(s => [s.segment_id, s])).values()];
    __readerTranslationBridge.applyTranslations(segments.map(s => ({ segment_id: s.segment_id, source_text: s.text, translated_text: `这是一段中文译文。 ${s.text}`, translation_status: 'succeeded' })));
  });
  await expect(page.locator('.reader-translation-node')).toHaveCount(count);
}

for (const display of ['flex', 'grid']) {
  test(`${display} text items keep translations inside without new columns or host reparenting`, async ({ page }) => {
    await load(page, `<style>.card{display:${display};grid-template-columns:48px minmax(0,1fr) 45px;gap:8px;align-items:start}.card img{width:48px;height:48px}.copy{min-width:0;flex:1}</style><main><section class="card"><img alt="" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Crect width='48' height='48' fill='%23a7bbc7'/%3E%3C/svg%3E"><div class="copy">An independent story description inside a card.</div><button>Save</button></section></main>`);
    const before = await page.locator('button').boundingBox();
    await page.evaluate(() => { window.originalText = document.querySelector('.copy').firstChild; });
    await apply(page, 1);
    await expect(page.locator('.card > .reader-translation-node')).toHaveCount(0);
    await expect(page.locator('.copy > .reader-translation-node')).toHaveCount(1);
    expect((await page.locator('button').boundingBox()).x).toBeCloseTo(before.x, 1);
    expect(await page.evaluate(() => originalText.parentElement === document.querySelector('.copy'))).toBe(true);
    const id = (await segments(page))[0].segment_id;
    await page.evaluate(() => __readerTranslationBridge.setMode('translated'));
    await expect(page.locator('.copy')).toBeVisible();
    await expect(page.locator('.copy > .reader-translation-node')).toBeVisible();
    await expect(page.locator('.copy > .reader-source-layout')).toBeHidden();
    await page.evaluate(() => __readerTranslationBridge.setMode('original'));
    await expect(page.locator('.reader-translation-node')).toBeHidden();
    expect(await page.evaluate(() => originalText.parentElement === document.querySelector('.copy'))).toBe(true);
    await page.evaluate(() => __readerTranslationBridge.setMode('bilingual'));
    await expect(page.locator('.reader-translation-node')).toHaveAttribute('data-reader-translation-for', id);
    await page.evaluate(() => __readerTranslationBridge.cleanup());
    expect(await page.locator('.card').evaluate(n => n.children.length)).toBe(3);
    await expect(page.locator('.reader-source-layout')).toHaveCount(0);
  });
}

test('article typography, list and cell spacing follow content roles', async ({ page }) => {
  await load(page, '<main><h1>Heading with a clear hierarchy</h1><p>Ordinary body paragraph with a <strong>bold phrase</strong>.</p><ul><li>One list item.</li></ul><table><tbody><tr><td>One table cell.</td></tr></tbody></table></main>');
  await apply(page, 4);
  const title = page.locator('h1 + .reader-translation-node');
  await expect(title).toHaveCSS('font-size', '30px');
  await expect(title).toHaveCSS('font-weight', '700');
  await expect(title).toHaveCSS('margin-top', '4.5px');
  await expect(page.locator('p + .reader-translation-node strong')).toHaveText('bold phrase');
  await expect(page.locator('li > .reader-translation-node')).toHaveCSS('margin-bottom', '0px');
  await expect(page.locator('td > .reader-translation-node')).toHaveCSS('margin-bottom', '0px');
  await expect(page.locator('tr')).toHaveCount(1);
});

test('a responsive card moves existing translation without new work', async ({ page }) => {
  await load(page, '<style>@media(min-width:600px){.card{display:grid;grid-template-columns:1fr 45px;gap:10px}}</style><main><section class="card"><p>Responsive body copy remains the same.</p><button>Save</button></section></main>');
  await apply(page, 1);
  await page.evaluate(() => { window.savedTranslation = document.querySelector('.reader-translation-node'); });
  await expect(page.locator('p + .reader-translation-node')).toHaveCount(1);
  await page.setViewportSize({ width: 800, height: 844 });
  await expect(page.locator('p > .reader-translation-node')).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('p + .reader-translation-node')).toHaveCount(1);
  expect(await page.evaluate(() => savedTranslation === document.querySelector('.reader-translation-node'))).toBe(true);
  expect((await segments(page)).length).toBe(1);
});

test('short inline atomic text stays inline and follows RTL gap and display modes', async ({ page }) => {
  const rule = compileWebTranslationRule({ id: 'inline-fixture', priority: 50, matches: [{ host: 'generic.example.test' }], segmentation: { atomic: { add: ['.label'] } } });
  await load(page, '<main><span class="label" style="direction:rtl">Short label</span></main>', [rule]);
  await apply(page, 1);
  await expect(page.locator('.reader-translation-node')).toHaveCSS('display', 'inline');
  await expect(page.locator('.reader-translation-node')).toHaveCSS('margin-right', '8px');
  await page.evaluate(() => __readerTranslationBridge.setMode('translated'));
  await expect(page.locator('.reader-translation-node')).toHaveCSS('margin-right', '0px');
  await page.evaluate(() => __readerTranslationBridge.setMode('bilingual'));
  await expect(page.locator('.reader-translation-node')).toHaveCSS('display', 'inline');
});

test('a passive clamp on the text itself is expanded and restored without moving out', async ({ page }) => {
  await load(page, '<style>.clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}</style><main><p class="clamp">This description has enough natural language to overflow two lines when its Chinese translation appears below it.</p></main>');
  await apply(page, 1);
  await expect(page.locator('.clamp > .reader-translation-node')).toHaveCount(1);
  await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', 'none');
  await page.setViewportSize({ width: 500, height: 844 });
  await expect(page.locator('.clamp > .reader-translation-node')).toHaveCount(1);
  await page.evaluate(() => __readerTranslationBridge.setMode('original'));
  await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', '2');
  await page.evaluate(() => __readerTranslationBridge.setMode('bilingual'));
  await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', 'none');
});

test('fixed and interactive clamps keep their original restrictions', async ({ page }) => {
  await load(page, '<style>.clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}</style><main><section><p class="clamp fixed" style="height:48px">A fixed height description with enough words to receive a translated paragraph.</p></section><section><p class="clamp interactive">Another long description beside its own expansion control.</p><button aria-expanded="false">More</button></section></main>');
  await apply(page, 2);
  await expect(page.locator('.fixed')).toHaveCSS('height', '48px');
  await expect(page.locator('.fixed')).toHaveCSS('-webkit-line-clamp', '2');
  await expect(page.locator('.interactive')).toHaveCSS('-webkit-line-clamp', '2');
});

test('slotted text respects the flattened shadow grid', async ({ page }) => {
  await load(page, '<main><story-card style="display:block"><p slot="copy">A story inside a slotted grid item.</p></story-card></main><script>document.querySelector("story-card").attachShadow({mode:"open"}).innerHTML = `<div style="display:grid;grid-template-columns:minmax(0,1fr) 45px;gap:8px"><slot name="copy" style="display:contents"></slot><button>Save</button></div>`;</script>');
  const before = await page.locator('button').boundingBox();
  await apply(page, 1);
  await expect(page.locator('p > .reader-translation-node')).toHaveCount(1);
  await expect(page.locator('story-card > .reader-translation-node')).toHaveCount(0);
  expect((await page.locator('button').boundingBox()).x).toBeCloseTo(before.x, 1);
});

test('an ancestor becoming constrained releases an existing natural flow lease', async ({ page }) => {
  await load(page, '<style>.clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}</style><main><section><p class="clamp">This natural flow paragraph has enough words to fill several lines after a translation is inserted into its original text area.</p></section></main>');
  await apply(page, 1);
  await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', 'none');
  await page.locator('section').evaluate(n => { n.style.height = '48px'; n.style.overflow = 'hidden'; });
  await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', '2');
  expect((await segments(page)).length).toBe(1);
});

test('style refresh retains rich nodes but repairs host-cleared translated content', async ({ page }) => {
  await load(page, '<main><p>Read <a href="/details">the full explanation</a> for more information.</p></main>');
  await apply(page, 1);
  await page.evaluate(() => { window.translatedLink = document.querySelector('.reader-translation-node a'); });
  await page.locator('p').evaluate(n => { n.style.fontSize = '18px'; });
  await expect(page.locator('.reader-translation-node')).toHaveCSS('font-size', '18px');
  expect(await page.evaluate(() => translatedLink === document.querySelector('.reader-translation-node a'))).toBe(true);
  await page.locator('.reader-translation-node').evaluate(n => { n.textContent = ''; });
  await page.locator('p').evaluate(n => { n.style.fontSize = '19px'; });
  await expect(page.locator('.reader-translation-node a')).toHaveText('the full explanation');
  expect((await segments(page)).length).toBe(1);
});

test('older WebViews retain explicit clamp rules while generic discovery stays conservative', async ({ page }) => {
  const rule = compileWebTranslationRule({ id: 'legacy-clamp', priority: 50, matches: [{ host: 'generic.example.test' }], rendering: { unclamp: { add: ['.clamp'] } } });
  for (const explicit of [false, true]) {
    await load(page, '<style>.clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}</style><main><p class="clamp">This longer natural paragraph fills enough lines to demonstrate compatibility with a browser that has no CSS Typed OM support.</p></main><script>Object.defineProperty(Element.prototype,"computedStyleMap",{value:undefined,configurable:true})</script>', explicit ? [rule] : []);
    await apply(page, 1);
    await expect(page.locator('.clamp')).toHaveCSS('-webkit-line-clamp', explicit ? 'none' : '2');
  }
});

test('linked headings retain source link appearance through theme changes', async ({ page }) => {
  await load(page, '<style>a{color:blue;text-decoration:underline}h2 a{color:rgb(20,30,40);text-decoration:none}main.night h2 a{color:rgb(180,190,200)}</style><main><h2><a href="#details">A linked section heading</a></h2><p id="details">Ordinary explanatory text.</p></main>');
  await apply(page, 2);
  const link = page.locator('h2 + .reader-translation-node a');
  await expect(link).toHaveCSS('color', 'rgb(20, 30, 40)');
  await expect(link).toHaveCSS('text-decoration-line', 'none');
  await page.locator('main').evaluate(n => { n.className = 'night'; });
  await expect(link).toHaveCSS('color', 'rgb(180, 190, 200)');
  await expect(link).toHaveAttribute('href', '#details');
  expect((await segments(page)).length).toBe(2);
});

test('inline headings use the actual text range typography rather than the outer article', async ({ page }) => {
  await load(page, '<style>h1{display:inline;font:48px/60px Arial}article{font:16px/24px Arial}article.wide h1{font-size:52px}</style><main><article><h1>Inline heading with actions<button>Save</button></h1><p>A separate explanatory paragraph follows the heading.</p></article></main>');
  await apply(page, 2);
  const heading = page.locator('h1 > .reader-translation-node');
  await expect(heading).toHaveCSS('font-size', '48px');
  await expect(heading).toHaveCSS('line-height', '60px');
  await page.locator('article').evaluate(n => { n.className = 'wide'; });
  await expect(heading).toHaveCSS('font-size', '52px');
  expect((await segments(page)).length).toBe(2);
});
