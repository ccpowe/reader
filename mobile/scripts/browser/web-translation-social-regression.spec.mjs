import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../..', import.meta.url));
const generated = readFileSync(`${root}/domain/translationRuntimeSources.generated.ts`, 'utf8');
const source = JSON.parse(generated.match(/export const WEB_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundle = buildSync({ entryPoints: [`${root}/domain/webTranslationRules.ts`], bundle: true, write: false, platform: 'node', format: 'esm' }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
async function load(page, host, html, initialState) {
  await page.route(`https://${host}/**`, route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><style>body{font:16px/24px Arial;margin:16px}h1{font-size:32px;line-height:40px;font-weight:700}p{margin:12px 0}</style>${html}` }));
  await page.goto(`https://${host}/example`);
  await page.evaluate(({ source, rules }) => {
    window.messages = [];
    window.ReactNativeWebView = { postMessage(value) {
      const message = JSON.parse(value); window.messages.push(message);
      if (message.type === 'reader_translation_navigation_reset') queueMicrotask(() => window.__readerTranslationBridge.confirmEpoch(message.document_epoch));
    } };
    (0, eval)(`(${source})`)({ channelToken: 'social-test', initialMode: 'bilingual', rules });
  }, { source, rules });
  await expect.poll(() => page.evaluate(
    states => messages.some(m => m.type === 'reader_translation_status' && states.includes(m.state)),
    initialState ? [initialState] : ['ready', 'waiting_for_content'],
  )).toBe(true);
}
const texts = page => page.evaluate(() => [...new Set(messages.filter(m => m.type === 'reader_translation_batch_plan').flatMap(m => m.batches.flatMap(b => b.segments.map(s => s.text))))]);
for (const host of ['x.com', 'www.reddit.com']) {
  for (const mount of ['element', 'role', 'data-testid']) {
    if (mount === 'data-testid' && host !== 'x.com') continue;
    test(`${host} recovers a content root mounted after handshake via ${mount}`, async ({ page }) => {
      await load(page, host, '<div id="shell"><p>Outside the permitted content root.</p></div>', 'profile_mismatch');
      expect(await texts(page)).toEqual([]);
      const epoch = await page.evaluate(() => messages.find(m => m.type === 'reader_translation_navigation_reset').document_epoch);
      await page.evaluate(mount => {
        const shell = document.querySelector('#shell');
        if (mount === 'element') shell.insertAdjacentHTML('afterend', '<main><p data-testid="tweetText">Delayed social post content.</p></main>');
        else {
          shell.innerHTML = '<p data-testid="tweetText">Delayed social post content.</p>';
        }
      }, mount);
      if (mount !== 'element') {
        // Let the child mutation settle before the root-identifying attribute.
        await page.waitForTimeout(50);
        await page.locator('#shell').evaluate((shell, mount) => shell.setAttribute(mount, mount === 'role' ? 'main' : 'primaryColumn'), mount);
      }
      await expect.poll(() => texts(page)).toEqual(['Delayed social post content.']);
      await expect.poll(() => page.evaluate(() => messages.filter(m => m.type === 'reader_translation_status').at(-1)?.state)).toBe('ready');
      await apply(page);
      await expect(page.locator('.reader-translation-node')).toHaveCount(1);
      expect(await page.evaluate(() => messages.filter(m => m.type === 'reader_translation_navigation_reset').at(-1).document_epoch)).toBe(epoch);
    });
  }
}
async function apply(page) {
  await page.evaluate(() => {
    const plan = messages.filter(m => m.type === 'reader_translation_batch_plan').at(-1);
    window.__readerTranslationBridge.applyTranslations(plan.batches.flatMap(b => b.segments).map(s => ({ segment_id: s.segment_id, source_text: s.text, translated_text: `译文 ${s.text}`, translation_status: 'succeeded' })), plan.document_epoch);
  });
}
test('Reddit preserves paragraph/list boundaries and title typography', async ({ page }) => {
  await load(page, 'www.reddit.com', '<main><h1 slot="title">An independent post heading</h1><div slot="text-body"><p>First independent paragraph.</p><p>Second independent paragraph.</p><ul><li>First list entry.</li><li>Second list entry.</li></ul></div></main>');
  await expect.poll(() => texts(page)).toHaveLength(5);
  expect(await texts(page)).toEqual(expect.arrayContaining(['First independent paragraph.', 'Second independent paragraph.', 'First list entry.', 'Second list entry.']));
  await apply(page);
  await expect(page.locator('.reader-translation-node')).toHaveCount(5);
  await expect(page.locator('h1 + .reader-translation-node')).toHaveCSS('font-size', '32px');
  await expect(page.locator('h1 + .reader-translation-node')).toHaveCSS('font-weight', '700');
  await expect(page.locator('h1 + .reader-translation-node')).toHaveCSS('opacity', '1');
  await expect(page.locator('li > .reader-translation-node')).toHaveCount(2);
  await expect(page.locator('[slot="text-body"] > p + .reader-translation-node')).toHaveCount(2);
});
test('X discovers alternate post markup, biographies, cards and long articles without metadata', async ({ page }) => {
  await load(page, 'x.com', '<main><article data-testid="tweet"><div data-testid="User-Name">Private account metadata</div><div dir="auto">Alternate post body.</div><button>Reply control</button></article><div data-testid="UserDescription">Author biography.</div><div data-testid="card.layoutLarge.detail"><p>Linked story description.</p></div><div data-testid="twitterArticleReadView"><p>Long article first paragraph.</p><p>Long article second paragraph.</p></div></main>');
  await expect.poll(() => texts(page)).toEqual(expect.arrayContaining(['Alternate post body.', 'Author biography.', 'Linked story description.', 'Long article first paragraph.', 'Long article second paragraph.']));
  expect((await texts(page)).join(' ')).not.toMatch(/Private account|Reply control/);
});
test('Reddit and X rediscover text edits nested within an approved content root', async ({ page }) => {
  for (const host of ['www.reddit.com', 'x.com']) {
    await load(page, host, '<main><div slot="text-body" data-testid="tweetText"><span id="changing">Original nested prose.</span></div></main>');
    await expect.poll(() => texts(page)).toContain('Original nested prose.');
    await page.locator('#changing').evaluate(n => { n.textContent = 'Updated nested prose.'; });
    await expect.poll(() => texts(page)).toContain('Updated nested prose.');
  }
});
test('GitHub main site preserves prose while excluding code, diff and author metadata', async ({ page }) => {
  await load(page, 'github.com', '<main><h1><bdi>Issue heading to translate</bdi></h1><article class="markdown-body"><p>Documentation with <code>inline_code()</code>.</p><div class="blob-code">const neverTranslate = true;</div><table class="diff-table"><tr><td>Never translate diff</td></tr></table><div class="timeline-comment-header">Account metadata</div><p>Review explanation.</p></article></main>');
  await expect.poll(() => texts(page)).toContain('Review explanation.');
  expect((await texts(page)).join(' ')).not.toMatch(/neverTranslate|Never translate diff|Account metadata/);
  await expect(page.locator('html')).toHaveAttribute('data-reader-translation-profile', 'github');
});
test('Reddit opts into reversible unclamp for a passive prose container', async ({ page }) => {
  await load(page, 'www.reddit.com', '<main><div slot="text-body" class="line-clamp-2" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden"><p>This is a long paragraph with enough text to overflow the natural two line container after its translated content is inserted beneath the source paragraph.</p></div></main>');
  await expect.poll(() => texts(page)).toHaveLength(1);
  await apply(page);
  await expect.poll(() => page.locator('[slot="text-body"]').evaluate(n => getComputedStyle(n).webkitLineClamp)).toBe('none');
  await page.evaluate(() => __readerTranslationBridge.setMode('original'));
  await expect(page.locator('[slot="text-body"]')).toHaveCSS('-webkit-line-clamp', '2');
});

test('source typography follows responsive changes without new translation work', async ({ page }) => {
  await load(page, 'www.reddit.com', '<style>@media(min-width:600px){h1{font-size:44px;line-height:52px}}</style><main><h1 slot="title">Responsive heading for translation</h1></main>');
  await expect.poll(() => texts(page)).toHaveLength(1);
  await apply(page);
  const translated = page.locator('h1 + .reader-translation-node');
  await expect(translated).toHaveCSS('font-size', '32px');
  await page.setViewportSize({ width: 800, height: 844 });
  await expect(translated).toHaveCSS('font-size', '44px');
  await expect(translated).toHaveCSS('line-height', '52px');
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
});

test('title translation follows host theme and typography changes', async ({ page }) => {
  await load(page, 'www.reddit.com', '<style>main.changed h1{color:rgb(100, 120, 140);font-weight:500}</style><main><h1 slot="title">Heading with dynamic host styles</h1></main>');
  await expect.poll(() => texts(page)).toHaveLength(1);
  await apply(page);
  const translated = page.locator('h1 + .reader-translation-node');
  await expect(translated).toHaveCSS('font-weight', '700');
  await page.locator('main').evaluate(n => { n.className = 'changed'; });
  await expect(translated).toHaveCSS('font-weight', '500');
  await expect(translated).toHaveCSS('color', 'rgb(100, 120, 140)');
});

test('GitHub issue references keep original text and safe navigation', async ({ page }) => {
  await load(page, 'github.com', '<main><p>See <a class="issue-link" href="/example/repo/issues/42">#42: Keep this reference</a> for the discussion.</p></main>');
  await expect.poll(() => texts(page)).toHaveLength(1);
  const payload = (await texts(page))[0];
  expect(payload).toContain('⟪READER_0⟫');
  expect(payload).not.toContain('Keep this reference');
  await apply(page);
  const link = page.locator('.reader-translation-node a');
  await expect(link).toHaveText('#42: Keep this reference');
  await expect(link).toHaveAttribute('href', '/example/repo/issues/42');
});

test('X pre-wrap text with blank lines gets interleaved paragraph translations', async ({ page }) => {
  await load(page, 'x.com', '<main><div data-testid="tweetText" style="white-space:pre-wrap"><span id="prose">First long paragraph.\n\nSecond long paragraph.\n\nThird long paragraph with <a href="https://example.test/paper">the paper</a>.</span></div></main>');
  const original = await page.locator('[data-testid="tweetText"]').innerHTML();
  await expect.poll(() => texts(page)).toHaveLength(3);
  expect(await texts(page)).toEqual(expect.arrayContaining(['First long paragraph.', 'Second long paragraph.']));
  await apply(page);
  await expect(page.locator('[data-testid="tweetText"] .reader-translation-node')).toHaveCount(3);
  const runs = await page.locator('#prose').evaluate(n => [...n.childNodes].map(c => ({ type: c.nodeType, text: c.textContent.trim() })));
  expect(runs.slice(0,4).map(r=>r.text)).toEqual(['First long paragraph.', '译文 First long paragraph.', 'Second long paragraph.', '译文 Second long paragraph.']);
  await page.evaluate(() => __readerTranslationBridge.setMode('original'));
  await expect(page.locator('.reader-translation-node:visible')).toHaveCount(0);
  await page.evaluate(() => __readerTranslationBridge.cleanup());
  expect(await page.locator('[data-testid="tweetText"]').innerHTML()).toBe(original);
  expect(await page.locator('#prose').evaluate(n=>n.childNodes.length)).toBe(3);
});

test('X host replacing its original text node does not duplicate split paragraphs', async ({ page }) => {
  await load(page, 'x.com', '<main><div data-testid="tweetText" style="white-space:pre-wrap"><span id="prose">Original first.\n\nOriginal second.</span></div></main>');
  await expect.poll(() => texts(page)).toHaveLength(2);
  await apply(page);
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  await page.locator('#prose').evaluate(n => { n.firstChild.data = 'Updated first.\n\nUpdated second.'; });
  await expect.poll(() => texts(page)).toContain('Updated second.');
  await page.evaluate(() => __readerTranslationBridge.cleanup());
  await expect(page.locator('#prose')).toHaveText('Updated first.\n\nUpdated second.');
  expect(await page.locator('#prose').evaluate(n=>n.childNodes.length)).toBe(1);
});
