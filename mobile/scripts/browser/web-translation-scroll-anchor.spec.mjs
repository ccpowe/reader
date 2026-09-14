import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const bundle = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationScrollAnchor.ts')], bundle: true, format: 'iife', globalName: 'ReaderAnchor', write: false }).outputFiles[0].text;
async function setup(page, { native = false, wrapper = '' } = {}) {
  await page.setViewportSize({ width: 800, height: 600 });
  await page.setContent(`<style>html {overflow-anchor:${native ? 'auto' : 'none'};scroll-behavior:auto}body{margin:0}main{width:600px;margin:auto;${wrapper}}p{font:20px/24px sans-serif;margin:0 0 24px}</style><div id="spacer" style="height:800px"></div><main>${'<p>Readable source paragraph with sufficient words to cover several lines. Translation must preserve the text currently being read in this ordinary article.</p>'.repeat(40)}</main><div style="height:1000px"></div>`);
  await page.addScriptTag({ content: bundle });
  await page.evaluate(() => {
    window.anchor = ReaderAnchor.createWebTranslationScrollAnchor(window, (element) => !!element.closest('main p'));
    window.grow = () => { document.querySelector('#spacer').style.height = '920px'; };
    window.scrollTo(0, 850);
  });
  await page.evaluate(() => new Promise(requestAnimationFrame));
}
test('restores the visible text after an above-viewport renderer write with native anchoring disabled', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(() => {
    const y = scrollY; anchor.beforeSlice(); grow(); anchor.afterSlice(); return scrollY - y;
  });
  expect(result).toBe(120);
});
for (const input of ['wheel', 'key', 'pointer', 'touch', 'selection']) {
  test(`does not move the viewport during ${input} interaction`, async ({ page }) => {
    await setup(page);
    const result = await page.evaluate(async (input) => {
      if (input === 'wheel') dispatchEvent(new WheelEvent('wheel'));
      if (input === 'key') dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown' }));
      if (input === 'pointer') {
        dispatchEvent(new PointerEvent('pointerdown', { pointerId: 1 }));
        await new Promise((resolve) => setTimeout(resolve, 550));
      }
      if (input === 'touch') {
        dispatchEvent(new TouchEvent('touchstart'));
        await new Promise((resolve) => setTimeout(resolve, 550));
      }
      if (input === 'selection') {
        const range = document.createRange(); range.selectNodeContents(document.querySelector('p'));
        getSelection().addRange(range);
      }
      const y = scrollY; anchor.beforeSlice(); grow(); anchor.afterSlice(); return scrollY - y;
    }, input);
    expect(result).toBe(0);
  });
}
for (const wrapper of ['height:400px;overflow:auto', 'position:sticky;top:0', 'transform:translateY(0)']) {
  test(`leaves constrained text alone: ${wrapper}`, async ({ page }) => {
    await setup(page, { wrapper });
    expect(await page.evaluate(() => {
      const y = scrollY; anchor.beforeSlice(); grow(); anchor.afterSlice(); return scrollY - y;
    })).toBe(0);
  });
}
test('native anchoring or host scrolling is never corrected twice', async ({ page }) => {
  await setup(page, { native: true });
  const result = await page.evaluate(async () => {
    const y = scrollY; anchor.beforeSlice(); grow();
    // A layout read allows the browser to perform its own anchor adjustment.
    document.querySelector('main').getBoundingClientRect();
    anchor.afterSlice();
    await new Promise(requestAnimationFrame);
    return scrollY - y;
  });
  expect(result).toBe(120);
  await setup(page);
  expect(await page.evaluate(() => {
    const y = scrollY; anchor.beforeSlice(); grow(); scrollBy(0, 50); anchor.afterSlice(); return scrollY - y;
  })).toBe(50);
});
test('reset, hash navigation and changed source invalidate a captured range', async ({ page }) => {
  for (const change of ['reset', 'hash', 'text']) {
    await setup(page);
    expect(await page.evaluate((change) => {
      const y = scrollY; anchor.beforeSlice(); grow();
      if (change === 'reset') anchor.reset();
      if (change === 'hash') history.replaceState(null, '', '#different-location');
      if (change === 'text') for (const p of document.querySelectorAll('p')) p.firstChild.textContent += ' updated';
      anchor.afterSlice(); return scrollY - y;
    }, change)).toBe(0);
  }
});
test('top of page stays at top and input beginning within a slice invalidates capture', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => {
    scrollTo(0, 0); anchor.beforeSlice(); grow(); anchor.afterSlice(); return scrollY;
  })).toBe(0);
  await setup(page);
  expect(await page.evaluate(() => {
    const y = scrollY; anchor.beforeSlice(); grow(); dispatchEvent(new WheelEvent('wheel')); anchor.afterSlice(); return scrollY - y;
  })).toBe(0);
});
test('dispose removes its event listeners and makes queued hooks inert', async ({ page }) => {
  await setup(page);
  expect(await page.evaluate(() => {
    anchor.dispose();
    const add = window.addEventListener; const remove = window.removeEventListener;
    const registrations = []; const removals = [];
    window.addEventListener = function (...args) { registrations.push(args); return add.apply(this, args); };
    window.removeEventListener = function (...args) { removals.push(args); return remove.apply(this, args); };
    const owned = ReaderAnchor.createWebTranslationScrollAnchor(window, () => true);
    owned.beforeSlice(); owned.dispose(); owned.dispose();
    window.addEventListener = add; window.removeEventListener = remove;
    const y = scrollY; grow(); owned.afterSlice(); owned.beforeSlice(); owned.afterSlice();
    return { count: registrations.length, removed: registrations.every(([type, callback]) => removals.some(([t, c]) => t === type && c === callback)), extra: removals.length - registrations.length, shift: scrollY - y };
  })).toEqual({ count: 11, removed: true, extra: 0, shift: 0 });
});
