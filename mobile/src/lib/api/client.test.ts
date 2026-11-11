import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildQuery, createApiClient, type TokenSource } from './client.ts';
import { ApiError, isRetryable } from './errors.ts';

type Call = { url: string; init: RequestInit };

function fakeFetch(responses: (Response | Error | ((init: RequestInit) => Promise<Response>))[]) {
  const calls: Call[] = [];
  const impl = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (!next) throw new Error('no more responses');
    if (next instanceof Error) throw next;
    if (typeof next === 'function') return next(init);
    return next;
  }) as unknown as typeof fetch;
  return { impl, calls };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const header = (call: Call, name: string) => (call.init.headers as Record<string, string>)[name];

function tokens(initial: string | null, refreshTo: string | null) {
  let current = initial;
  const refreshed: string[] = [];
  const owner = {};
  const source: TokenSource = {
    accessToken: () => (current ? { token: current, owner } : null),
    refreshAccessToken: async (rejected) => {
      refreshed.push(rejected.token);
      current = refreshTo;
      return refreshTo ? { token: refreshTo, owner } : null;
    },
  };
  return { source, refreshed };
}

test('sends the server token, the access token and a JSON body', async () => {
  const { impl, calls } = fakeFetch([json(200, { ok: true })]);
  const api = createApiClient({ apiBaseUrl: 'https://r.example/api', serverToken: 'srv', tokens: tokens('a1', null).source, fetch: impl });
  const result = await api.request<{ ok: boolean }>('/saved/x', { method: 'PUT', body: { a: 1 }, query: { q: 'ai', limit: 20, none: undefined } });
  assert.deepEqual(result, { ok: true });
  assert.equal(calls[0].url, 'https://r.example/api/saved/x?q=ai&limit=20');
  assert.equal(header(calls[0], 'X-Reader-Server-Token'), 'srv');
  assert.equal(header(calls[0], 'Authorization'), 'Bearer a1');
  assert.equal(header(calls[0], 'Content-Type'), 'application/json');
  assert.equal(calls[0].init.body, '{"a":1}');
  assert.equal(calls[0].init.redirect, 'error');
});

test('unauthenticated requests carry no bearer token', async () => {
  const { impl, calls } = fakeFetch([json(200, {})]);
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 'srv', tokens: tokens('a1', null).source, fetch: impl });
  await api.request('/auth/login', { method: 'POST', body: {}, authenticated: false });
  assert.equal(header(calls[0], 'Authorization'), undefined);
});

test('turns the error envelope into an http problem', async () => {
  const { impl } = fakeFetch([json(429, { error: { code: 'too_many_attempts', message: 'slow down', retry_after: 30 } })]);
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: impl });
  await assert.rejects(api.request('/feed', { authenticated: false }), (error: ApiError) => {
    assert.deepEqual(error.problem, { kind: 'http', status: 429, code: 'too_many_attempts', message: 'slow down', retryAfter: 30 });
    return true;
  });
});

test('a body that is not the envelope is an invalid response', async () => {
  const { impl } = fakeFetch([new Response('<html>bad gateway</html>', { status: 502 })]);
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: impl });
  await assert.rejects(api.request('/feed'), (error: ApiError) => error.problem.kind === 'invalid-response');
});

test('network failure and timeout are told apart, and both are retryable', async () => {
  const network = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([new TypeError('failed')]).impl });
  await assert.rejects(network.request('/feed'), (error: ApiError) => error.problem.kind === 'network' && isRetryable(error));

  const hang = (init: RequestInit) =>
    new Promise<Response>((_, reject) => init.signal!.addEventListener('abort', () => reject(new Error('aborted'))));
  const slow = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([hang]).impl, timeoutMs: 10 });
  await assert.rejects(slow.request('/feed'), (error: ApiError) => error.problem.kind === 'timeout' && isRetryable(error));
});

test("the caller's cancellation is 'aborted', not a failure", async () => {
  const hang = (init: RequestInit) =>
    new Promise<Response>((_, reject) => init.signal!.addEventListener('abort', () => reject(new Error('aborted'))));
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([hang]).impl });
  const controller = new AbortController();
  const pending = api.request('/feed', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error: ApiError) => error.problem.kind === 'aborted' && !isRetryable(error));
});

test('a body that stalls after the headers is still cut off by the deadline and by cancellation', async () => {
  const stalling = (init: RequestInit) =>
    Promise.resolve(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('{"items": ['));
            init.signal!.addEventListener('abort', () => controller.error(new Error('aborted')));
          },
        }),
        { status: 200 },
      ),
    );
  const slow = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([stalling]).impl, timeoutMs: 20 });
  await assert.rejects(slow.request('/feed'), (error: ApiError) => error.problem.kind === 'timeout');

  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([stalling]).impl });
  const controller = new AbortController();
  const pending = api.request('/feed', { signal: controller.signal });
  await new Promise((resolve) => setTimeout(resolve, 5));
  controller.abort();
  await assert.rejects(pending, (error: ApiError) => error.problem.kind === 'aborted');
});

test('a proxy error page during a restart is retryable', async () => {
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([new Response('<html>502</html>', { status: 502 })]).impl });
  await assert.rejects(api.request('/feed'), (error: ApiError) => error.problem.kind === 'invalid-response' && isRetryable(error));
});

test('an expired access token is refreshed once and the request repeated', async () => {
  const { impl, calls } = fakeFetch([json(401, { error: { code: 'not_authenticated', message: '' } }), json(200, { items: [] })]);
  const t = tokens('old', 'new');
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', tokens: t.source, fetch: impl });
  assert.deepEqual(await api.request('/feed'), { items: [] });
  assert.deepEqual(t.refreshed, ['old']);
  assert.equal(header(calls[1], 'Authorization'), 'Bearer new');
});

test('when the session is over the 401 is passed on, without a second request', async () => {
  const { impl, calls } = fakeFetch([json(401, { error: { code: 'not_authenticated', message: '' } })]);
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', tokens: tokens('old', null).source, fetch: impl });
  await assert.rejects(api.request('/feed'), (error: ApiError) => error.problem.kind === 'http' && error.problem.code === 'not_authenticated');
  assert.equal(calls.length, 1);
});

test('a wrong server token is not answered with a token refresh', async () => {
  const { impl } = fakeFetch([json(401, { error: { code: 'invalid_server_token', message: '' } })]);
  const t = tokens('a', 'b');
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 'bad', tokens: t.source, fetch: impl });
  await assert.rejects(api.request('/feed'), (error: ApiError) => error.problem.kind === 'http' && error.problem.code === 'invalid_server_token');
  assert.deepEqual(t.refreshed, []);
});

test('204 resolves to undefined', async () => {
  const api = createApiClient({ apiBaseUrl: 'https://r.example', serverToken: 's', fetch: fakeFetch([new Response(null, { status: 204 })]).impl });
  assert.equal(await api.request('/saved/x', { method: 'DELETE' }), undefined);
});

test('query arrays repeat the key', () => {
  assert.equal(buildQuery({ a: [1, 2], b: true }), '?a=1&a=2&b=true');
  assert.equal(buildQuery({}), '');
});
