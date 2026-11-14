import assert from 'node:assert/strict';
import { test } from 'node:test';

import { discover } from './discovery.ts';

function answer(status: number, body: unknown, url = '') {
  return (async () => {
    const response = new Response(JSON.stringify(body), { status });
    if (url) Object.defineProperty(response, 'url', { value: url });
    return response;
  }) as unknown as typeof fetch;
}

const good = { protocol_version: 3, server_id: 'home', api_base_url: 'https://api.r.example/' };

test('a Reader server answers with its id and API base', async () => {
  const seen: string[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    seen.push(url, (init.headers as Record<string, string>)['X-Reader-Server-Token']);
    return new Response(JSON.stringify(good), { status: 200 });
  }) as unknown as typeof fetch;
  assert.deepEqual(await discover('r.example/reader/', 'tok', { fetch: fetchImpl }), {
    ok: true,
    server: { base: 'https://r.example/reader', serverId: 'home', apiBaseUrl: 'https://api.r.example' },
  });
  assert.deepEqual(seen, ['https://r.example/reader/.well-known/reader.json', 'tok']);
});

test('a bad address never makes a request', async () => {
  const result = await discover('ftp://x', 't', { fetch: (() => assert.fail('no request')) as unknown as typeof fetch });
  assert.deepEqual(result, { ok: false, problem: { kind: 'address', problem: 'protocol' } });
});

test('tells apart the ways it can fail', async () => {
  const problem = async (f: typeof fetch) => {
    const result = await discover('https://r.example', 't', { fetch: f, timeoutMs: 20 });
    assert.ok(!result.ok);
    return result.problem;
  };
  assert.deepEqual(await problem(answer(401, { error: { code: 'invalid_server_token', message: '' } })), { kind: 'wrong-token' });
  assert.deepEqual(await problem(answer(503, { error: { code: 'server_not_configured', message: '' } })), { kind: 'not-configured' });
  assert.deepEqual(await problem(answer(429, { error: { code: 'too_many_attempts', message: '', retry_after: 9 } })), {
    kind: 'rate-limited',
    retryAfter: 9,
  });
  assert.deepEqual(await problem(answer(200, { hello: 'world' })), { kind: 'not-reader' });
  assert.deepEqual(await problem(answer(200, { ...good, protocol_version: 2 })), { kind: 'old-protocol', version: 2 });
  assert.deepEqual(await problem(answer(200, good, 'https://elsewhere.example/.well-known/reader.json')), {
    kind: 'redirected',
    to: 'https://elsewhere.example',
  });
  assert.deepEqual(await problem(answer(200, { ...good, api_base_url: 'http://r.example' })), { kind: 'insecure-api', to: 'http://r.example' });
  assert.deepEqual(await problem((async () => { throw new TypeError('refused'); }) as unknown as typeof fetch), { kind: 'unreachable' });
  const hang = ((_: string, init: RequestInit) =>
    new Promise((_, reject) => init.signal!.addEventListener('abort', () => reject(new Error('x'))))) as unknown as typeof fetch;
  assert.deepEqual(await problem(hang), { kind: 'timeout' });
});
