import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-connection-url-'));
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
  const { normalizeReaderBaseUrl, parseReaderBaseUrl, resolveReaderUrl } = require(
    join(outputDirectory, 'lib/connection/url.js'),
  );
  assert.equal(normalizeReaderBaseUrl('http://localhost:8000/reader/'), 'http://localhost:8000/reader');
  assert.equal(normalizeReaderBaseUrl('https://example.com'), 'https://example.com');
  assert.equal(normalizeReaderBaseUrl('http://192.168.1.20:8000'), 'http://192.168.1.20:8000');
  assert.equal(normalizeReaderBaseUrl('http://[::1]:8000/reader/'), 'http://[::1]:8000/reader');
  assert.equal(
    resolveReaderUrl('https://example.com/reader', '/v1/me'),
    'https://example.com/reader/v1/me',
  );
  assert.equal(
    parseReaderBaseUrl('https://Example.com:443/prefix/').discovery_url,
    'https://example.com/prefix/.well-known/reader.json',
  );

  for (const input of [
    'ftp://example.com',
    'file:///tmp/reader',
    'https://user:password@example.com',
    'https://example.com?token=secret',
    'https://example.com/#fragment',
  ]) {
    assert.throws(() => parseReaderBaseUrl(input), ConnectionError, input);
  }
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Reader connection URL validation passed.');
