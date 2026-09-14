import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-http-response-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/http.ts',
      'domain/translationRetry.ts',
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

  const { HttpResponseError, httpResponseError, readJsonResponse } = require(
    join(outputDirectory, 'lib/http.js'),
  );
  const { translationTransportRetryDecision } = require(
    join(outputDirectory, 'domain/translationRetry.js'),
  );
  const { i18n } = require(join(outputDirectory, 'i18n/index.js'));
  const { localizedMessage } = require(join(outputDirectory, 'i18n/message.js'));
  await i18n.changeLanguage('zh-CN');
  const gatewayError = await readJsonResponse(
    new Response('Bad Gateway', { status: 502 }),
    '翻译服务',
  ).catch((error) => error);
  assert.ok(gatewayError instanceof HttpResponseError);
  assert.equal(gatewayError.status, 502);
  assert.equal(gatewayError.retryable, true);
  assert.match(gatewayError.message, /翻译服务返回了非 JSON 响应（HTTP 502）/);
  assert.deepEqual(
    translationTransportRetryDecision(gatewayError, 0),
    { delayMs: 1_500, failureCount: 1, retry: true },
  );
  assert.deepEqual(
    translationTransportRetryDecision(gatewayError, 4),
    { delayMs: null, failureCount: 5, retry: false },
  );
  const validationError = httpResponseError(
    new Response('', { status: 422 }),
    '翻译服务',
    [{
      loc: ['body', 'segments', 0, 'context_before'],
      msg: 'Extra inputs are not permitted',
      type: 'extra_forbidden',
    }],
  );
  assert.ok(validationError instanceof HttpResponseError);
  assert.equal(validationError.status, 422);
  assert.equal(validationError.retryable, false);
  assert.match(validationError.message, /HTTP 422/);
  assert.match(validationError.message, /context_before/);
  assert.doesNotMatch(validationError.message, /undefined is not a function/);
  assert.deepEqual(
    translationTransportRetryDecision(
      new HttpResponseError('稍后重试', {
        retryAfterMs: 10_000,
        retryable: true,
        status: 429,
      }),
      1,
    ),
    { delayMs: 10_000, failureCount: 2, retry: true },
  );
  await assert.deepEqual(
    await readJsonResponse(new Response('{"status":"ok"}', { status: 200 })),
    { status: 'ok' },
  );
  const heldResponseError = await readJsonResponse(new Response('Bad Gateway', { status: 502 }))
    .catch((error) => error);
  const heldProviderError = httpResponseError(new Response('', { status: 422 }),
    localizedMessage('errors:translationService'), 'Provider message 原文 token=secret');
  assert.equal(heldResponseError.message, '后端返回了非 JSON 响应（HTTP 502）。');
  await i18n.changeLanguage('en');
  assert.equal(heldResponseError.message, 'Backend returned a non-JSON response (HTTP 502).');
  assert.equal(heldProviderError.message, 'Translation service request failed (HTTP 422): Provider message 原文 token=[redacted]');
  const englishError = await readJsonResponse(new Response('Bad Gateway', { status: 502 }))
    .catch((error) => error);
  assert.equal(englishError.message, 'Backend returned a non-JSON response (HTTP 502).');
  assert.equal(httpResponseError(new Response('', { status: 422 }), 'Translation service',
    '原始提供商消息').message, 'Translation service request failed (HTTP 422): 原始提供商消息');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('HTTP response parsing is resilient to non-JSON tunnel failures');
