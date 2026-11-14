import assert from 'node:assert/strict';
import { test } from 'node:test';

import { joinUrl, parseServerUrl } from './url.ts';

const ok = (input: string) => {
  const result = parseServerUrl(input);
  assert.ok(result.ok, `expected ${input} to parse`);
  return result.url;
};
const problem = (input: string) => {
  const result = parseServerUrl(input);
  assert.ok(!result.ok, `expected ${input} to be rejected`);
  return result.problem;
};

test('normalises scheme, trailing slash and path prefix', () => {
  assert.deepEqual(ok('https://reader.example.com/'), {
    base: 'https://reader.example.com',
    origin: 'https://reader.example.com',
    discoveryUrl: 'https://reader.example.com/.well-known/reader.json',
  });
  assert.equal(ok('  http://192.168.1.5:8000  ').base, 'http://192.168.1.5:8000');
  assert.equal(ok('https://example.com/reader//').base, 'https://example.com/reader');
  assert.equal(ok('example.com').base, 'https://example.com');
  assert.equal(ok('http://[::1]:8000').base, 'http://[::1]:8000');
});

test('rejects addresses that could send the request elsewhere', () => {
  assert.equal(problem(''), 'empty');
  assert.equal(problem('   '), 'empty');
  assert.equal(problem('https://exa mple.com'), 'whitespace');
  assert.equal(problem('ftp://example.com'), 'protocol');
  assert.equal(problem('https://user:pass@example.com'), 'credentials');
  assert.equal(problem('https://example.com/?a=1'), 'query');
  assert.equal(problem('https://example.com/?'), 'query');
  assert.equal(problem('https://example.com/#x'), 'fragment');
  assert.equal(problem('https://example.com:99999'), 'invalid');
});

test('joins routes under a path prefix', () => {
  assert.equal(joinUrl('https://example.com/reader', '/feed'), 'https://example.com/reader/feed');
  assert.equal(joinUrl('https://example.com/', 'feed'), 'https://example.com/feed');
});
