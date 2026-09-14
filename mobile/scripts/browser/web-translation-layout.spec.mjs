import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const bundle = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationLayout.ts')], bundle: true, format: 'iife', globalName: 'ReaderLayout', write: false }).outputFiles[0].text;
async function setup(page, { wrapper = '', inline = '', controls = '' } = {}) {
  await page.setContent(`<style>.clamp {width:230px;line-height:24px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}</style><main style="${wrapper}"><div id="container" class="clamp" style="${inline}"><span id="source">This is the original paragraph with enough source words to wrap across several visible lines of text.</span><span id="translation">这是完整的中文译文，需要解除行数限制才能显示所有内容。它必须确实可读，不能只是插入节点。</span></div>${controls}</main>`);
  await page.addScriptTag({ content: bundle });
  await page.evaluate(() => {
    window.layoutWrites = 0;
    window.manager = ReaderLayout.createWebTranslationLayoutManager({ ownedWrite: (_element, write) => { window.layoutWrites++; write(); } });
    window.acquire = (id = 'first', selectors = ['.clamp']) => window.manager.acquire(id, document.querySelector('#source'), document.querySelector('#translation'), { selectors }).status;
  });
}
test('layout policy defaults to no write and ordinary article is unchanged', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => acquire('first', []))).toBe('disabled');
  await page.evaluate(() => document.querySelector('#container').className = 'article');
  expect(await page.evaluate(() => acquire('first', ['.article']))).toBe('unchanged');
  expect(await page.evaluate(() => window.layoutWrites)).toBe(0);
});
test('explicit clamp lease makes complete translation visible and restores important priority', async ({ page }) => {
  await setup(page, { inline: '-webkit-line-clamp:2!important;color:navy' });
  const before = await page.locator('#container').evaluate((node) => node.clientHeight);
  expect(await page.evaluate(() => acquire())).toBe('applied');
  const after = await page.locator('#container').evaluate((node) => ({ height: node.clientHeight, content: node.scrollHeight, bottom: node.getBoundingClientRect().bottom, textBottom: document.querySelector('#translation').getBoundingClientRect().bottom }));
  expect(after.height).toBeGreaterThan(before);
  expect(after.content).toBeLessThanOrEqual(after.height + 1);
  expect(after.textBottom).toBeLessThanOrEqual(after.bottom + 1);
  await page.evaluate(() => manager.releaseAll());
  expect(await page.locator('#container').evaluate((node) => [node.style.getPropertyValue('-webkit-line-clamp'), node.style.getPropertyPriority('-webkit-line-clamp'), node.style.color])).toEqual(['2', 'important', 'navy']);
});
test('shared and repeated leases release only after last entity, including detached hosts', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => acquire())).toBe('applied');
  expect(await page.evaluate(() => acquire())).toBe('shared');
  expect(await page.evaluate(() => acquire('second'))).toBe('shared');
  await page.evaluate(() => manager.release('first'));
  expect(await page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe('none');
  expect(await page.evaluate(() => { const host = document.querySelector('#container'); host.remove(); manager.release('second'); manager.release('second'); return host.style.webkitLineClamp; })).toBe('');
});
test('host same-property updates and change-then-revert writes survive restoration', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => acquire())).toBe('applied');
  expect(await page.evaluate(() => { const host = document.querySelector('#container'); host.style.setProperty('-webkit-line-clamp', '4', 'important'); manager.releaseAll(); return host.style.webkitLineClamp; })).toBe('4');
  await setup(page);
  expect(await page.evaluate(() => acquire())).toBe('applied');
  expect(await page.evaluate(() => { const host = document.querySelector('#container'); host.style.setProperty('-webkit-line-clamp', '5'); host.style.setProperty('-webkit-line-clamp', 'none', 'important'); manager.releaseAll(); return host.style.webkitLineClamp; })).toBe('none');
});
test('unrelated host styles survive while clamp is restored, class control releases ownership', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { acquire(); document.querySelector('#container').style.color = 'red'; document.querySelector('#container').setAttribute('style', document.querySelector('#container').getAttribute('style')); manager.releaseAll(); });
  expect(await page.locator('#container').evaluate((node) => [node.style.webkitLineClamp, node.style.color])).toEqual(['', 'red']);
  await page.evaluate(() => { acquire(); document.querySelector('#container').classList.add('expanded'); });
  await expect.poll(() => page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe('');
});
for (const wrapper of ['display:grid', 'display:flex', 'position:fixed', 'position:absolute', 'overflow:auto']) {
  test(`unsafe layout boundary never writes: ${wrapper}`, async ({ page }) => {
    await setup(page, { wrapper });
    expect(await page.evaluate(() => acquire())).toBe('unsafe');
    expect(await page.evaluate(() => window.layoutWrites)).toBe(0);
  });
}
test('Show more control keeps user in charge and fixed height is not broadened', async ({ page }) => {
  await setup(page, { controls: '<button aria-expanded="false">Show more</button>' });
  expect(await page.evaluate(() => acquire())).toBe('interactive');
  expect(await page.evaluate(() => window.layoutWrites)).toBe(0);
  await setup(page, { inline: 'height:48px' });
  expect(await page.evaluate(() => acquire())).toBe('unsafe');
  expect(await page.locator('#container').evaluate((node) => [node.style.webkitLineClamp, node.style.height])).toEqual(['', '48px']);
});
test('remaining ancestor clipping or fixed height rolls back and traversal has hard budgets', async ({ page }) => {
  await setup(page, { wrapper: 'height:48px;overflow:hidden' });
  expect(await page.evaluate(() => acquire())).toBe('unsafe');
  expect(await page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe('');
  await setup(page, { wrapper: 'height:48px;overflow:visible' });
  expect(await page.evaluate(() => acquire())).toBe('unsafe');
  expect(await page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe('');
  await setup(page);
  expect(await page.evaluate(() => { document.querySelector('main').insertAdjacentHTML('beforeend', '<i></i>'.repeat(130)); return acquire(); })).toBe('budget_exceeded');
  expect(await page.evaluate(() => window.layoutWrites)).toBe(0);
  await setup(page);
  expect(await page.evaluate(() => acquire('first', ['[']))).toBe('invalid_selector');
});
test('shadow text checks layout of its host before changing a clamp', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => {
    const host = document.createElement('section');
    host.style.display = 'grid';
    document.body.append(host);
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<div class="clamp" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden;width:80px"><span id="a">Long enough original paragraph.</span><span id="b">译文必须检查宿主布局。</span></div>';
    return manager.acquire('shadow', shadow.querySelector('#a'), shadow.querySelector('#b'), { selectors: ['.clamp'] }).status;
  })).toBe('unsafe');
  expect(await page.evaluate(() => window.layoutWrites)).toBe(0);
});

async function acquireNatural(page, id = 'natural') {
  return page.evaluate((key) => manager.acquire(key, document.querySelector('#source'), document.querySelector('#translation'), { selectors: [], naturalFlow: true }).status, id);
}
test('generic passive clamp becomes readable without site selectors and releases to original layout', async ({ page }) => {
  await setup(page);
  const before = await page.locator('#container').evaluate((node) => node.clientHeight);
  expect(await acquireNatural(page)).toBe('applied');
  expect(await acquireNatural(page, 'second')).toBe('shared');
  const expanded = await page.locator('#container').evaluate((node) => ({ height: node.clientHeight, scroll: node.scrollHeight }));
  expect(expanded.height).toBeGreaterThan(before);
  expect(expanded.scroll).toBeLessThanOrEqual(expanded.height + 1);
  await page.evaluate(() => manager.release('natural'));
  expect(await page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe('none');
  await page.evaluate(() => manager.release('second'));
  expect(await page.locator('#container').evaluate((node) => node.clientHeight)).toBe(before);
});
for (const variant of [
  { inline: 'height:500px' },
  { inline: 'max-height:500px' },
  { wrapper: 'height:500px' },
  { wrapper: 'max-height:500px' },
  { wrapper: 'display:flex' },
  { wrapper: 'display:grid' },
  { wrapper: 'overflow:auto' },
  { wrapper: 'transform:translateY(0)' },
  { wrapper: 'contain:layout' },
  { controls: '<button>Show more</button>' },
]) {
  test(`generic discovery refuses constrained or interactive host ${JSON.stringify(variant)}`, async ({ page }) => {
    await setup(page, variant);
    expect(['unsafe', 'interactive']).toContain(await acquireNatural(page));
    expect(await page.evaluate(() => layoutWrites)).toBe(0);
  });
}
test('generic discovery requires actual clipping and translation inside the clamped container', async ({ page }) => {
  await setup(page, { inline: '-webkit-line-clamp:100' });
  expect(await acquireNatural(page)).toBe('unmatched');
  await setup(page);
  await page.evaluate(() => document.querySelector('main').append(document.querySelector('#translation')));
  expect(await acquireNatural(page)).toBe('unmatched');
  expect(await page.evaluate(() => layoutWrites)).toBe(0);
});
for (const change of ['class', 'style', 'clamp']) {
  test(`generic lease yields to host ${change} change and does not reapply`, async ({ page }) => {
    await setup(page);
    expect(await acquireNatural(page)).toBe('applied');
    await page.evaluate((change) => {
      const node = document.querySelector('#container');
      if (change === 'class') node.classList.add('host-update');
      else if (change === 'style') node.style.color = 'red';
      else node.style.setProperty('-webkit-line-clamp', '3', 'important');
    }, change);
    await expect.poll(() => page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe(change === 'clamp' ? '3' : '');
    expect(await acquireNatural(page)).not.toBe('applied');
    await page.evaluate(() => manager.releaseAll());
    expect(await page.locator('#container').evaluate((node) => node.style.webkitLineClamp)).toBe(change === 'clamp' ? '3' : '');
  });
}
test('without Typed OM generic discovery fails closed while explicit site policies still work', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => Object.defineProperty(Element.prototype, 'computedStyleMap', { value: undefined, configurable: true }));
  expect(await acquireNatural(page)).toBe('unsafe');
  expect(await page.evaluate(() => layoutWrites)).toBe(0);
  expect(await page.evaluate(() => acquire())).toBe('applied');
});

test('a slotted clamp cannot bypass a shadow grid safety boundary', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => {
    const host = document.createElement('section'); document.body.append(host);
    host.attachShadow({ mode: 'open' }).innerHTML = '<div style="display:grid"><slot style="display:contents"></slot></div>';
    host.append(document.querySelector('#container'));
    return manager.acquire('slotted', document.querySelector('#source'), document.querySelector('#translation'), { selectors: [], naturalFlow: true }).status;
  })).toBe('unsafe');
  expect(await page.evaluate(() => layoutWrites)).toBe(0);
});

test('an unrelated distant card button does not block generic paragraph expansion', async ({ page }) => {
  await setup(page);
  await page.locator('main').evaluate(n => n.insertAdjacentHTML('afterbegin', '<section><button>Save another story</button></section><h2>Article section</h2>'));
  expect(await acquireNatural(page)).toBe('applied');
  await page.evaluate(() => manager.releaseAll());
  await expect(page.locator('#container')).toHaveCSS('-webkit-line-clamp', '2');
});
