import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-server-discovery-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/connection/errors.ts',
      'lib/connection/types.ts',
      'lib/connection/url.ts',
      'lib/connection/discovery.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--outDir',
      outputDirectory,
      '--lib',
      'dom,es2022',
      '--skipLibCheck',
    ],
    { cwd: mobileRoot, stdio: 'inherit' },
  );

  const { ConnectionError } = require(join(outputDirectory, 'lib/connection/errors.js'));
  const { discoverReaderServer, redirectConfirmationFor } = require(
    join(outputDirectory, 'lib/connection/discovery.js'),
  );
  const payload = {
    api_base_url: 'https://reader.example.com/prefix',
    protocol_version: 2,
    server_id: 'server-a',
  };
  const requests = [];
  const discover = (url, options = {}) => discoverReaderServer(url, { serverToken: 'private-entry-token', ...options });
  const result = await discover('https://reader.example.com/prefix', {
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' }, status: 200 });
    },
  });
  assert.equal(result.base_url, 'https://reader.example.com/prefix');
  assert.equal(result.discovery_url, 'https://reader.example.com/prefix/.well-known/reader.json');
  assert.equal(result.redirected, false);
  assert.equal(result.requires_confirmation, false);
  assert.equal(redirectConfirmationFor(result), null);
  assert.equal(new Headers(requests[0].options.headers).get('X-Reader-Server-Token'), 'private-entry-token');
  assert.equal(requests[0].options.redirect, 'error', 'credentials must never follow an untrusted redirect');
  assert.equal(requests[0].url.includes('private-entry-token'), false, 'entry tokens never enter URLs');
  let missingTokenFetches = 0;
  await assert.rejects(discoverReaderServer('https://unknown.example.com', {
    fetchImpl: async () => { missingTokenFetches += 1; return new Response('{}'); },
  }), error => error instanceof ConnectionError && error.code === 'not_configured');
  assert.equal(missingTokenFetches, 0, 'a missing token is rejected before network I/O');

  for (const change of [
    { api_base_url: 'https://external.example.net/reader' },
    { api_base_url: 'http://reader.example.com/prefix' },
    { api_base_url: 'https://reader.example.com:444/prefix' },
    { protocol_version: 1 },
    { protocol_version: 3 },
    { supabase_url: 'https://legacy.example.net' },
    { server_id: '' },
  ]) {
    await assert.rejects(discover('https://reader.example.com/prefix', {
      fetchImpl: async () => new Response(JSON.stringify({ ...payload, ...change })),
    }), error => error instanceof ConnectionError && error.code === 'discovery_invalid_response');
  }
  await assert.rejects(discover('https://reader.example.com', {
    fetchImpl: async () => ({ json: async () => payload, ok: true, status: 200, url: 'https://cdn.example.net/.well-known/reader.json' }),
  }), error => error instanceof ConnectionError && error.code === 'discovery_invalid_response', 'even an incorrectly redirecting transport cannot activate a cross-origin runtime');
  await assert.rejects(discover('https://reader.example.com', {
    fetchImpl: async () => { throw new TypeError('Redirect disallowed'); },
  }), error => error instanceof ConnectionError && error.code === 'discovery_network');
  for (const [status, code] of [[401, 'server_token_invalid'], [503, 'discovery_configuration'], [404, 'discovery_http']]) {
    await assert.rejects(discover('https://reader.example.com', {
      fetchImpl: async () => new Response(JSON.stringify({ detail: { code: 'reader_discovery_configuration_incomplete' } }), { status }),
    }), error => error instanceof ConnectionError && error.code === code);
  }
  await assert.rejects(discover('https://reader.example.com', {
    timeoutMs: 1,
    fetchImpl: async (_url, options) => new Promise((_, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }),
  }), error => error instanceof ConnectionError && error.code === 'discovery_timeout');
  const upstream = new AbortController();
  await assert.rejects(discover('https://reader.example.com', {
    signal: upstream.signal,
    fetchImpl: async () => { upstream.abort(); return new Response(JSON.stringify(payload)); },
  }), error => error instanceof ConnectionError && error.code === 'discovery_cancelled');

} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Reader server discovery validation passed.');
