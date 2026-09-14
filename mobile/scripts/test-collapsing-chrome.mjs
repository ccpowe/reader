import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const directory = mkdtempSync(join(tmpdir(), 'reader-chrome-motion-'));
const originalLoad = Module._load;
const timings = [];
let tree;

try {
  buildSync({
    entryPoints: [join(mobileRoot, 'hooks/useCollapsingChrome.ts')],
    outfile: join(directory, 'chrome.cjs'),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    external: ['react', 'react-native-reanimated'],
  });
  Module._load = function load(request, parent, isMain) {
    if (request === 'react') return React;
    if (request === 'react-native-reanimated') return {
      Easing: { out: (value) => value, cubic: (value) => value },
      cancelAnimation: () => {},
      useSharedValue: (value) => React.useRef({ value }).current,
      useAnimatedScrollHandler: (handlers) => handlers,
      useAnimatedStyle: (factory) => factory(),
      withTiming: (value, options) => { timings.push({ value, options }); return value; },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { useCollapsingChrome, useChromeStyle } = require(join(directory, 'chrome.cjs'));
  let result;
  let props;
  let progress;
  function Harness(input) {
    result = useCollapsingChrome(input.active, progress, input.pageKey, {
      height: 100,
      locked: input.locked,
    });
    return null;
  }
  async function mount(overrides = {}) {
    if (tree) await act(async () => { tree.unmount(); });
    progress = { value: 0 };
    props = { active: true, locked: false, pageKey: 'a', ...overrides };
    await act(async () => { tree = create(React.createElement(Harness, props)); });
    timings.length = 0;
  }
  async function update(overrides) {
    props = { ...props, ...overrides };
    await act(async () => { tree.update(React.createElement(Harness, props)); });
    timings.length = 0;
  }
  const event = (y, contentHeight = 2000) => ({
    contentOffset: { x: 0, y },
    contentSize: { width: 390, height: contentHeight },
    layoutMeasurement: { width: 390, height: 600 },
    velocity: { x: 0, y: 0 },
  });
  const begin = (y, contentHeight) => result.onScroll.onBeginDrag(event(y, contentHeight));
  const scroll = (y, contentHeight) => result.onScroll.onScroll(event(y, contentHeight));
  const near = (expected, message) => assert.ok(Math.abs(progress.value - expected) < 0.00001,
    `${message}: expected ${expected}, received ${progress.value}`);

  await mount();
  begin(200);
  scroll(225);
  near(0.25, 'chrome follows partial drag distance from the real starting offset');
  scroll(245);
  near(0.45, 'continued drag advances continuously');
  scroll(235);
  near(0.35, 'reversing direction immediately reveals chrome');
  assert.equal(timings.length, 0, 'drag events must not start independent timing animations');
  scroll(600);
  near(1, 'a long downward drag clamps at fully hidden');
  scroll(200);
  near(0, 'a long reverse drag clamps at fully shown');
  scroll(-20);
  near(0, 'top overscroll cannot hide chrome');

  await mount();
  begin(100, 650);
  scroll(140, 650);
  near(0, 'a short list must remain readable without collapsing chrome');
  begin(0, 600);
  scroll(20, 600);
  near(0, 'non-scrollable content keeps navigation visible');

  await mount({ active: false });
  begin(200);
  scroll(270);
  near(0, 'offscreen list events cannot drive shared chrome');
  await update({ active: true });
  scroll(700);
  near(0, 'reactivation does not treat a retained offset as new drag distance');
  begin(700);
  scroll(720);
  near(0.2, 'reactivated screen follows the next real drag');
  await update({ locked: true });
  near(0, 'focusing search reveals navigation');
  begin(720);
  scroll(780);
  near(0, 'search focus keeps navigation visible while content moves');
  await update({ locked: false });
  begin(780);
  scroll(800);
  near(0.2, 'scroll behavior resumes after search unlocks');

  await mount();
  begin(300);
  scroll(350);
  near(0.5, 'establish a partially hidden state');
  await update({ pageKey: 'b' });
  near(0, 'switching pages reveals shared navigation');
  scroll(900);
  near(0, 'restored page scroll offset does not trigger a collapse');
  begin(900);
  scroll(930);
  near(0.3, 'new page movement starts from its actual offset');
  result.revealChrome();
  near(0, 'explicit reveal opens navigation');
  scroll(930);
  near(0, 'explicit reveal resets the offset baseline');
  begin(930);
  scroll(950);
  near(0.2, 'drag after explicit reveal continues from the retained position');

  await mount();
  begin(200);
  scroll(225);
  result.onScroll.onEndDrag(event(225));
  assert.ok(progress.value >= 0 && progress.value <= 1, 'drag settlement stays within bounds');
  assert.equal(typeof result.onScroll.onMomentumBegin, 'function');
  assert.equal(typeof result.onScroll.onMomentumEnd, 'function');
  result.onScroll.onMomentumBegin(event(225));
  scroll(280);
  result.onScroll.onMomentumEnd(event(280));
  assert.ok(progress.value >= 0 && progress.value <= 1, 'momentum settlement stays within bounds');

  const style = useChromeStyle({ value: 0.5 }, 100);
  assert.deepEqual(style.transform, [{ translateY: -50 }], 'chrome translates by its measured height');
  assert.equal(style.opacity, undefined, 'chrome movement must not also fade content');
  console.log('collapsing chrome follows drag distance, guards inactive/search/short lists, and restores scroll baselines');
} finally {
  if (tree) await act(async () => { tree.unmount(); });
  Module._load = originalLoad;
  rmSync(directory, { recursive: true, force: true });
}
