import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const output = mkdtempSync(join(tmpdir(), 'reader-web-rules-'));
const require = createRequire(import.meta.url);

try {
  execFileSync('pnpm', ['exec', 'tsc', 'domain/webTranslationRules.ts', '--ignoreConfig', '--target', 'es2022', '--module', 'commonjs', '--outDir', output], { stdio: 'pipe' });
  const rules = require(join(output, 'webTranslationRules.js'));
  const selected = (url) => rules.selectWebTranslationRule(url).id;

  assert.equal(selected('https://docs.github.com/en/get-started'), 'github-docs');
  assert.equal(selected('https://en.wikipedia.org/wiki/Translation'), 'wikipedia');
  assert.equal(selected('https://unknown.example/article'), 'generic');
  const youtube = rules.selectWebTranslationRule('https://m.youtube.com/watch?v=test');
  assert.equal(youtube.id, 'youtube');
  assert.ok(youtube.segmentation.exclude.includes('.html5-video-player'));
  assert.ok(youtube.segmentation.exclude.includes('[data-reader-caption-control]'));
  assert.ok(youtube.segmentation.forceBlock.includes('.yt-core-attributed-string'));
  assert.equal(youtube.scope.fallback, 'document', 'YouTube comments stay in document scope instead of Readability extraction');
  assert.equal(rules.selectWebTranslationRule('https://docs.github.com/en/get-started').scope.rootMode, 'priority');
  assert.equal(rules.selectWebTranslationRule('https://reader.local/').scope.rootMode, 'exclusive');
  assert.equal(rules.selectWebTranslationRule('https://www.reddit.com/r/typescript/').scope.rootMode, 'exclusive');
  assert.equal(rules.isSpecialRuleAllowed('https://www.reddit.com/r/typescript/', 'reddit'), true);
  assert.equal(rules.isSpecialRuleAllowed('https://news.ycombinator.com/', 'reddit'), false);

  const compiled = rules.compileWebTranslationRule({
    id: 'test-rule', priority: 1, matches: [{ host: 'example.test' }],
    segmentation: { exclude: { add: ['.advert'] }, stayOriginal: { add: ['var'] } },
  });
  assert.ok(compiled.segmentation.exclude.includes('nav'));
  assert.ok(compiled.segmentation.exclude.includes('.advert'));
  assert.ok(compiled.segmentation.stayOriginal.includes('var'));
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad', priority: 1, matches: [{ host: 'example.test' }],
    segmentation: { exclude: { replace: ['main'], add: ['aside'] } },
  }), /replace cannot be combined/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad;', priority: 1, matches: [{ host: 'example.test' }],
  }), /invalid rule id/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-mode', priority: 1, matches: [{ host: 'example.test' }], scope: { rootMode: 'anything' },
  }), /invalid root mode/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'unsafe-social', priority: 1, matches: [{ host: 'reddit.com' }], adapter: 'reddit', scope: { rootMode: 'priority' },
  }), /social adapter requires exclusive/);

  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-layout', priority: 1, matches: [], rendering: { unclamp: { add: 'p' } },
  }), /string arrays/);
  const rendering = rules.compileWebTranslationRule({
    id: 'layout-test', priority: 1, matches: [{ host: 'example.test' }],
    rendering: { unclamp: { add: ['.natural-description'] } },
  });
  assert.deepEqual(rendering.rendering.unclamp, ['.natural-description']);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-layout', priority: 1, matches: [], rendering: { css: 'height:auto' },
  }), /unknown field/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-layout', priority: 1, matches: [], rendering: { unclamp: { replace: ['p'], add: ['div'] } },
  }), /replace cannot be combined/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-layout', priority: 1, matches: [], rendering: { unclamp: { add: ['p{height:auto}'] } },
  }), /invalid selector/);
  assert.throws(() => rules.compileWebTranslationRule({
    id: 'bad-layout', priority: 1, matches: [], rendering: [],
  }), /invalid rendering/);
  for (const rule of rules.WEB_TRANSLATION_RULE_REGISTRY) {
    if (!['reddit', 'x', 'github'].includes(rule.id)) assert.deepEqual(rule.rendering.unclamp, [], 'other sites remain opt-in');
    assert(Object.isFrozen(rule.rendering.unclamp));
    assert.ok(rule.segmentation.exclude.includes('.reader-translation-node'));
    assert.ok(rule.segmentation.stayOriginal.includes('code'));
  }
  const conditional = rules.compileWebTranslationRule({ id: 'conditional', priority: 20, matches: [{
    host: 'example.test', pathPrefix: '/read', excludePathPrefixes: ['/read/admin'],
    requiredSelectors: ['main.article', '[data-template="story"]'], absentSelectors: ['.dashboard'],
  }] });
  const registry = [conditional, compiled, ...rules.WEB_TRANSLATION_RULE_REGISTRY];
  const dom = { querySelector: selector => ['main.article', '[data-template="story"]'].includes(selector.split(':not(')[0]) ? {} : null };
  assert.equal(rules.selectWebTranslationRule('https://example.test/read/1', registry, dom).id, 'conditional');
  assert.equal(rules.selectWebTranslationRule('https://example.test/read/admin/1', registry, dom).id, 'test-rule');
  assert.equal(rules.selectWebTranslationRule('https://example.test/read/1', registry).id, 'test-rule');
  assert.equal(rules.selectWebTranslationRule('https://unrelated.test/read/1', registry, dom).id, 'generic');
  assert.equal(rules.selectWebTranslationRule('https://example.test/read/1', registry, { querySelector() { throw Error('invalid selector'); } }).id, 'test-rule');
  const absentOnly = rules.compileWebTranslationRule({ id: 'absent-only', priority: 100, matches: [{ host: 'example.test', absentSelectors: ['.dashboard'] }] });
  assert.equal(rules.selectWebTranslationRule('https://example.test/', [absentOnly, compiled]).id, 'test-rule');
  for (const fields of [{ requiredSelectors: 'main' }, { absentSelectors: [1] }, { requiredSelectors: ['main:has(p)'] },
    { requiredSelectors: ['main,aside'] }, { requiredSelectors: ['span a'] }, { requiredSelectors: ['main > p'] }, { requiredSelectors: ['.reader-translation-node'] }, { requiredSelectors: Array(9).fill('main') }, { excludePathPrefixes: [42] },
    { excludePathPrefixes: ['/path?query'] }, { pathPrefix: 42 }, { unexpected: true }]) {
    assert.throws(() => rules.compileWebTranslationRule({ id: 'bad-condition', priority: 1, matches: [{ host: 'example.test', ...fields }] }));
  }
  let count = 0;
  const manyRules = Array.from({ length: 80 }, (_, index) => rules.compileWebTranslationRule({
    id: `condition-${index}`, priority: 100, matches: [{ host: 'example.test', requiredSelectors: [`.missing-${index}`] }],
  }));
  assert.equal(rules.selectWebTranslationRule('https://example.test/', [...manyRules, compiled], { querySelector() { count++; return null; } }).id, 'test-rule');
  assert.equal(count, 64);
  count = 0;
  rules.selectWebTranslationRule('https://example.test/read/1', [conditional, conditional, compiled], { querySelector() { count++; return null; } });
  assert.equal(count, 1, 'shared selectors are queried once per decision');
  console.log('web translation rule tests passed');
} finally {
  rmSync(output, { force: true, recursive: true });
}
