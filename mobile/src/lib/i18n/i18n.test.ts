import assert from 'node:assert/strict';
import { test } from 'node:test';

import { en } from './en.ts';
import { language, setLanguage, systemLanguage, t } from './index.ts';
import { zh } from './zh.ts';

/** Every text of a table, functions called with sample values. */
function texts(table: unknown, path = ''): [string, string][] {
  if (typeof table === 'string') return [[path, table]];
  // A Date works for every parameter: it prints as text, counts as a number and has date methods.
  if (typeof table === 'function') {
    const sample = Object.assign(new Date(2026, 10, 12), { toString: () => '7' });
    return [[path, String((table as (...args: unknown[]) => unknown)(...Array.from({ length: table.length }, () => sample)))]];
  }
  if (table && typeof table === 'object') return Object.entries(table).flatMap(([k, v]) => texts(v, path ? `${path}.${k}` : k));
  return [];
}

test('the English table has no Chinese left', () => {
  const chinese = texts(en).filter(([, text]) => /[一-鿿]/.test(text));
  assert.deepEqual(chinese, []);
});

test('both tables have the same keys (code-only maps too)', () => {
  const keys = (table: unknown) =>
    texts(table)
      .map(([path]) => path)
      .sort();
  assert.deepEqual(keys(en), keys(zh));
});

test('switching language and the system default', () => {
  assert.equal(language(), 'zh');
  assert.equal(t().common.retry, '重试');
  setLanguage('en');
  assert.equal(t().common.retry, 'Retry');
  assert.equal(t().time.minutes(1, false), '1 min ago');
  assert.equal(t().subs.count(2), '2 sources');
  setLanguage('zh');
  assert.equal(systemLanguage(['zh-Hans-CN', 'en-US']), 'zh');
  assert.equal(systemLanguage(['en-GB', 'zh-CN']), 'en');
  assert.equal(systemLanguage([]), 'en');
  assert.equal(systemLanguage(['zh-Hant-TW']), 'zh');
  assert.equal(systemLanguage(['yue-Hant-HK']), 'zh');
  assert.equal(systemLanguage(['yue']), 'zh');
  assert.equal(systemLanguage(['en-CN']), 'en');
  setLanguage('en');
  assert.equal(t().boards.comments('1'), '1 comment');
  assert.equal(t().reader.translation.failed(1), "1 paragraph wasn't translated.");
  assert.equal(t().reader.translation.captionsFailed(2), "2 caption lines weren't translated.");
  setLanguage('zh');
});
