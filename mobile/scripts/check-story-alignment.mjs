#!/usr/bin/env node
/**
 * Alignment check for the design stories, on a real Android device.
 *
 * Opens each Storybook story in Expo Go, reads the on-screen layout with `uiautomator dump`
 * (real drawn bounds of every text, icon and image), and reports two kinds of slip that are
 * easy to miss by eye:
 *   1. same row, different center: two small things side by side (time + badge, icon + label,
 *      message text + its button) whose vertical centers differ by more than 1dp;
 *   2. near-miss edges: a left edge (or right edge) a few dp off the content edge — 24 on a page,
 *      16 in a sheet — so it almost, but not quite, lines up with everything else.
 * It checks the first screen and one scroll down. It cannot judge spacing or taste.
 *
 * Needs: Metro running Storybook (`pnpm run storybook`), Expo Go on the device, adb.
 * Usage:  node scripts/check-story-alignment.mjs [--device SERIAL] [--only substring] [--no-scroll]
 * Exit code: number of stories with findings (0 = clean).
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const args = process.argv.slice(2);
const option = (name) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
};
const device = option('--device') ?? process.env.ANDROID_SERIAL;
const only = option('--only');
const scroll = !args.includes('--no-scroll');

const EDGES = [16, 24]; // content edges, dp: sheet, page
const ROW_TOLERANCE = 1; // dp
const EDGE_NEAR = 6; // dp: farther than this is a deliberate different indent
// Known false alarms: text and icons inside a drawn box sit at the box's own padding, which the
// dump cannot tell from a slip. Mark such boxes testID="align-inset" to skip their insides.
const SMALL = 40; // dp: taller things are blocks or multi-line text, not row items (stacked label + line: 48)

function adb(...command) {
  return execFileSync('adb', [...(device ? ['-s', device] : []), ...command], { encoding: 'utf8' });
}

/** Story ids from the story files: <title>--<export name in kebab case>. */
function storyIds() {
  const files = ['src/design/pages.stories.tsx'];
  const ids = [];
  for (const file of files) {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    const title = source.match(/title:\s*'([^']+)'/)[1];
    for (const match of source.matchAll(/^export const (\w+)/gm)) {
      const kebab = match[1].replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();
      ids.push(`${title}--${kebab}`);
    }
  }
  return ids;
}

/** uiautomator XML → tree of { cls, text, desc, box: [x1, y1, x2, y2] in dp, children }. */
function parse(xml, density) {
  const root = { children: [] };
  const stack = [root];
  for (const match of xml.matchAll(/<node ([^>]*?)(\/?)>|<\/node>/g)) {
    if (match[0] === '</node>') {
      stack.pop();
      continue;
    }
    const attrs = Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map((m) => [m[1], m[2]]));
    const b = attrs.bounds.match(/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/).slice(1).map((v) => Number(v) / density);
    const node = {
      cls: attrs.class.split('.').pop(),
      text: attrs.text,
      desc: attrs['content-desc'],
      id: attrs['resource-id'],
      clickable: attrs.clickable === 'true',
      box: b,
      children: [],
    };
    stack.at(-1).children.push(node);
    if (match[2] !== '/') stack.push(node);
  }
  return root;
}

function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {
    const hit = find(child, predicate);
    if (hit) return hit;
  }
  return null;
}

function flatten(node, out = []) {
  out.push(node);
  for (const child of node.children) flatten(child, out);
  return out;
}

const inside = (inner, outer) => inner[0] >= outer[0] - 0.5 && inner[1] >= outer[1] - 0.5 && inner[2] <= outer[2] + 0.5 && inner[3] <= outer[3] + 0.5;

/**
 * Things that are drawn: text, icons (SvgView), images. The dump does not keep every parent /
 * child link (Android flattens some), so regions are matched by bounds:
 *   - only marks inside the open sheet (testID="sheet") when there is one;
 *   - none inside the tab bar (testID="tab-bar") or testID="align-ignore" (deliberate layouts);
 *   - an icon inside a small non-clickable box is a tile (source mark): the tile lines up, not the glyph;
 *   - an icon inside a small clickable box is an icon button: the button's box lines up with the
 *     content edge and the glyph sits inside it (user decision 2026-11-11: bookmark, ›, ⋮ stay so).
 */
function marks(root) {
  const nodes = flatten(root).filter((node) => node.box);
  // A sheet covers the page: only what comes after it in drawing order and lies inside it counts.
  const sheetIndex = nodes.findIndex((node) => node.id === 'sheet');
  const sheet = nodes[sheetIndex];
  const skipped = nodes.filter((node) => ['tab-bar', 'align-ignore', 'align-inset'].includes(node.id)).map((node) => node.box);
  const out = [];
  for (const [index, node] of nodes.entries()) {
    const [x1, y1, x2, y2] = node.box;
    if (sheet && index <= sheetIndex) continue;
    if (x2 - x1 < 4 || y2 - y1 < 6) continue; // clipped or off-screen nodes report tiny / inverted boxes
    if (sheet && !inside(node.box, sheet.box)) continue;
    if (skipped.some((box) => inside(node.box, box))) continue;
    if (node.cls === 'SvgView') {
      const tiles = nodes.filter((other) => {
        const [ox1, oy1, ox2, oy2] = other.box;
        const w = ox2 - ox1;
        const h = oy2 - oy1;
        const box = (!other.clickable && (other.cls === 'ViewGroup' || other.cls === 'View')) || other.cls === 'CheckBox';
        return other !== node && box && w <= 56 && h <= 56 && (w > x2 - x1 + 2 || h > y2 - y1 + 2) && inside(node.box, other.box);
      });
      const tile = tiles.sort((a, b) => (a.box[2] - a.box[0]) - (b.box[2] - b.box[0]))[0];
      const button = nodes
        .filter((other) => other.clickable && other.cls !== 'CheckBox' && other.box[2] - other.box[0] <= 48 && other.box[3] - other.box[1] <= 48 && inside(node.box, other.box))
        .sort((a, b) => (a.box[2] - a.box[0]) - (b.box[2] - b.box[0]))[0];
      if (tile) out.push({ kind: 'tile', label: 'tile', box: tile.box });
      else if (button) out.push({ kind: 'button', label: 'button', box: button.box });
      else out.push({ kind: 'icon', label: 'icon', box: node.box });
    } else if (node.cls === 'TextView' || node.cls === 'ImageView') {
      out.push({ kind: node.cls === 'TextView' ? 'text' : 'image', label: node.text || node.cls, box: node.box });
    }
  }
  // a tile holding several glyph parts is one mark
  return out.filter((m, i) => (m.kind !== 'tile' && m.kind !== 'button') || out.findIndex((o) => o.kind === m.kind && o.box.join() === m.box.join()) === i);
}

/** Texts stacked at the same left edge with no gap (label + the line under it) count as one block. */
function mergeStacks(items) {
  const texts = items.filter((m) => m.kind === 'text').sort((a, b) => a.box[1] - b.box[1]);
  const merged = [];
  for (const text of texts) {
    // same left edge, or indented by up to 20 (a status line led by a small icon)
    const block = merged.find((m) => text.box[0] - m.box[0] >= -1 && text.box[0] - m.box[0] <= 20 && text.box[1] - m.box[3] >= -1 && text.box[1] - m.box[3] <= 4);
    if (block) {
      block.box = [block.box[0], block.box[1], Math.max(block.box[2], text.box[2]), text.box[3]];
      block.stacked = true;
    } else {
      merged.push({ ...text, box: [...text.box] });
    }
  }
  return [...items.filter((m) => m.kind !== 'text'), ...merged];
}

const fmt = (value) => Math.round(value * 10) / 10;
const name = (mark) =>
  mark.kind === 'text'
    ? `「${mark.label.slice(0, 16)}」${mark.stacked ? '（连同下面一行）' : ''}`
    : `${{ icon: '图标', tile: '图标方块', button: '图标按钮', image: '图片' }[mark.kind]}（x=${fmt(mark.box[0])}, y=${fmt(mark.box[1])}）`;

function check(raw, width) {
  const items = mergeStacks(raw);
  const findings = [];
  // 1. same row, different center
  const small = items.filter((m) => m.box[3] - m.box[1] <= (m.stacked ? SMALL + 8 : SMALL));
  for (let i = 0; i < small.length; i++) {
    for (let j = i + 1; j < small.length; j++) {
      const [a, b] = [small[i].box, small[j].box];
      const apart = a[2] <= b[0] || b[2] <= a[0];
      const overlap = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
      const shorter = Math.min(a[3] - a[1], b[3] - b[1]);
      if (!apart || overlap < shorter * 0.5) continue;
      const diff = (a[1] + a[3]) / 2 - (b[1] + b[3]) / 2;
      if (Math.abs(diff) > ROW_TOLERANCE) {
        findings.push(`同一行没对齐：${name(small[i])} 和 ${name(small[j])} 中线差 ${fmt(Math.abs(diff))}dp（y ≈ ${fmt(a[1])}）`);
      }
    }
  }
  // 2. near-miss edges
  for (const m of items) {
    const [x1, , x2] = m.box;
    if (Math.abs((x1 + x2) / 2 - width / 2) < 2) continue; // centered on purpose
    // A TextView is as wide as its box, not its glyphs, so only its left edge means anything.
    const sides = m.kind === 'text' ? [['左', x1]] : [['左', x1], ['右', width - x2]];
    for (const [edge, value] of sides) {
      const nearest = EDGES.reduce((best, e) => (Math.abs(value - e) < Math.abs(value - best) ? e : best));
      const off = Math.abs(value - nearest);
      if (off > ROW_TOLERANCE && off <= EDGE_NEAR) {
        findings.push(`${edge}边差一点：${name(m)} 离屏幕${edge}边 ${fmt(value)}dp，内容边是 ${nearest}`);
      }
    }
  }
  return findings;
}

/** The story's layout once it has settled: two dumps in a row with the same set of boxes. */
async function dump(id, density) {
  let previous = null;
  for (let attempt = 0; attempt < 12; attempt++) {
    try {
      adb('shell', 'uiautomator', 'dump', '/sdcard/reader-align.xml');
      const tree = parse(adb('shell', 'cat', '/sdcard/reader-align.xml'), density);
      const root = find(tree, (node) => node.desc === id);
      if (root) {
        const signature = flatten(root).map((node) => node.box.join()).join('|');
        if (signature === previous) return root;
        previous = signature;
      }
    } catch {
      // the screen was still animating; try again
    }
    await sleep(800);
  }
  return null;
}

const density = Number(adb('shell', 'wm', 'density').match(/(\d+)\s*$/)[1]) / 160;
adb('reverse', 'tcp:8081', 'tcp:8081');
let failing = 0;
for (const id of storyIds().filter((story) => !only || story.includes(only))) {
  adb('shell', 'am', 'start', '-a', 'android.intent.action.VIEW', '-d', `exp://127.0.0.1:8081/--/?STORYBOOK_STORY_ID=${encodeURI(id)}`, 'host.exp.exponent');
  await sleep(2500);
  const root = await dump(id, density);
  if (!root) {
    console.log(`? ${id}：没找到页面（没加载出来）`);
    failing++;
    continue;
  }
  const width = root.box[2] - root.box[0];
  const findings = new Set(check(marks(root), width));
  // A sheet's list scrolls inside the sheet and clips its rows at the edges; check it unscrolled.
  if (scroll && !find(root, (node) => node.id === 'sheet')) {
    const [x1, y1, x2, y2] = root.box.map((v) => Math.round(v * density));
    const x = Math.round((x1 + x2) / 2);
    adb('shell', 'input', 'swipe', String(x), String(Math.round(y1 + (y2 - y1) * 0.8)), String(x), String(Math.round(y1 + (y2 - y1) * 0.3)), '400');
    await sleep(1200);
    const after = await dump(id, density);
    if (after) for (const finding of check(marks(after), width)) findings.add(finding);
  }
  if (findings.size === 0) {
    console.log(`✓ ${id}`);
  } else {
    failing++;
    console.log(`✗ ${id}`);
    for (const finding of findings) console.log(`    ${finding}`);
  }
}
console.log(failing === 0 ? '\n全部对齐。' : `\n${failing} 个页面有问题。`);
process.exit(failing);
