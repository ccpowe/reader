import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-avatar-source-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/api.ts',
      'hooks/useAvatarImageSource.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--outDir',
      outputDirectory,
      '--lib',
      'dom,es2022',
      '--types',
      'node',
      '--skipLibCheck',
    ],
    { cwd: mobileRoot, stdio: 'inherit' },
  );

  const api = require(join(outputDirectory, 'lib/api.js'));
  const { avatarImageSource } = api;
  const { commitRuntimeSnapshot } = require(join(outputDirectory, 'lib/connection/snapshot.js'));
  const uri = 'http://10.182.85.212:8000/reader/v1/sources/source-id/avatar';
  const runtime = { generation: 1, identity: { server_id: 'avatar-test', api_base_url: 'http://10.182.85.212:8000/reader' }, api_base_url: 'http://10.182.85.212:8000/reader', serverToken: 'avatar-entry-token' };
  commitRuntimeSnapshot(runtime, 1);
  for (const accessToken of ['first-session-token', 'refreshed-session-token']) {
    assert.deepEqual(avatarImageSource(uri, accessToken), [{
      headers: { Authorization: `Bearer ${accessToken}`, 'X-Reader-Server-Token': 'avatar-entry-token' }, uri,
    }], 'only the current Reader API source receives current account and entry credentials');
  }
  for (const external of [
    'https://cdn.example/avatar.png',
    'http://10.182.85.212:8000/other/v1/avatar',
    'http://10.182.85.212:8000/reader/v1/../public/avatar',
    'http://10.182.85.212:8001/reader/v1/avatar',
    'data:image/png;base64,aGVsbG8=',
  ]) assert.deepEqual(avatarImageSource(external, 'private-token'), [{ uri: external }], 'external image URLs never receive credentials');
  // The production display hook fetches private bytes through Reader's
  // redirect-aware transport and hands Image a credential-free data URI.
  const originalLoad = Module._load;
  const originalFetch = globalThis.fetch;
  const originalBtoa = globalThis.btoa;
  let queryOptions;
  let displayedImage;
  Module._load = function(request, parent, isMain) {
    if (request === '@tanstack/react-query') return { useQuery: options => { queryOptions = options; return { data: displayedImage }; } };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const { useAvatarImageSource } = require(join(outputDirectory, 'hooks/useAvatarImageSource.js'));
    assert.equal(useAvatarImageSource(uri, 'first-session-token'), undefined, 'private URL is not passed directly to the native Image loader');
    assert.equal(queryOptions.enabled, true);
    assert.doesNotMatch(JSON.stringify(queryOptions.queryKey), /avatar-entry-token|first-session-token/, 'cache keys must never contain secrets');
    const imageBytes = new Uint8Array([137, 80, 78, 71]);
    globalThis.fetch = async (url, options) => {
      assert.equal(url, uri);
      assert.equal(options.redirect, 'error');
      assert.equal(new Headers(options.headers).get('X-Reader-Server-Token'), 'avatar-entry-token');
      assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer first-session-token');
      return new Response(imageBytes, { headers: { 'content-type': 'image/png' } });
    };
    displayedImage = await queryOptions.queryFn({ signal: new AbortController().signal });
    assert.deepEqual(useAvatarImageSource(uri, 'first-session-token'), [{ uri: 'data:image/png;base64,iVBORw==' }]);
    assert.deepEqual(useAvatarImageSource('https://cdn.example/avatar.png', 'private-token'), [{ uri: 'https://cdn.example/avatar.png' }]);
    assert.equal(queryOptions.enabled, false, 'public image URLs do not enter the authenticated byte loader');
    useAvatarImageSource(uri, 'first-session-token');
    globalThis.btoa = undefined; // Hermes does not guarantee the browser encoder.
    for (const size of [0, 1, 2, 3, 8190, 8191, 8192]) {
      const bytes = Uint8Array.from({ length: size }, (_, index) => index % 256);
      globalThis.fetch = async () => new Response(bytes, { headers: { 'content-type': 'image/png' } });
      assert.equal(await queryOptions.queryFn({ signal: new AbortController().signal }), `data:image/png;base64,${Buffer.from(bytes).toString('base64')}`, 'native-safe base64 encoding preserves tail bytes and chunk boundaries');
    }
    // A complete 1x1 32-bit ICO containing a DIB and transparency mask.
    const iconBytes = Buffer.alloc(70);
    iconBytes.writeUInt16LE(1, 2);
    iconBytes.writeUInt16LE(1, 4);
    iconBytes[6] = iconBytes[7] = 1;
    iconBytes.writeUInt16LE(1, 10);
    iconBytes.writeUInt16LE(32, 12);
    iconBytes.writeUInt32LE(48, 14);
    iconBytes.writeUInt32LE(22, 18);
    iconBytes.writeUInt32LE(40, 22);
    iconBytes.writeInt32LE(1, 26);
    iconBytes.writeInt32LE(2, 30);
    iconBytes.writeUInt16LE(1, 34);
    iconBytes.writeUInt16LE(32, 36);
    iconBytes.fill(255, 62, 66);
    for (const [header, mime] of [
      ['image/x-icon', 'image/x-icon'],
      ['image/vnd.microsoft.icon', 'image/vnd.microsoft.icon'],
      ['IMAGE/X-ICON; charset=binary', 'image/x-icon'],
    ]) {
      globalThis.fetch = async () => new Response(iconBytes, { headers: { 'content-type': header } });
      displayedImage = await queryOptions.queryFn({ signal: new AbortController().signal });
      assert.deepEqual(useAvatarImageSource(uri, 'first-session-token'), [{ uri: `data:${mime};base64,${iconBytes.toString('base64')}` }], 'ICO bytes reach the image loader without credentials');
    }
    globalThis.fetch = async () => new Response('<svg></svg>', { headers: { 'content-type': 'image/svg+xml' } });
    await assert.rejects(queryOptions.queryFn({ signal: new AbortController().signal }), error => error.message === 'Invalid avatar' || error.kind === 'network');
  } finally {
    Module._load = originalLoad;
    globalThis.fetch = originalFetch;
    globalThis.btoa = originalBtoa;
  }
  commitRuntimeSnapshot(null, 2);
  assert.deepEqual(avatarImageSource(uri, 'stale-token'), [{ uri }], 'a disconnected runtime cannot authenticate image requests');

} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Avatar source authentication is confined to the active Reader API origin and prefix.');
