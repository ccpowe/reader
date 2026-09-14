import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-realtime-errors-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'domain/realtimeTranslationErrors.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--outDir',
      outputDirectory,
      '--skipLibCheck',
    ],
    { cwd: mobileRoot, stdio: 'inherit' },
  );

  const {
    providerTranslationErrorMessage,
    realtimeTranslationErrorMessage,
  } = require(join(outputDirectory, 'domain/realtimeTranslationErrors.js'));
  const { i18n } = require(join(outputDirectory, 'i18n/index.js'));
  const { localizedMessage } = require(join(outputDirectory, 'i18n/message.js'));
  const { TranslationRequestError } = require(join(outputDirectory, 'domain/translationRequestErrors.js'));
  await i18n.changeLanguage('zh-CN');
  const heldTransportError = new TranslationRequestError(localizedMessage('errors:backendNetwork'), 'network');
  const providerError = new TranslationRequestError('Provider message 原文', 'http', 400);
  assert.equal(heldTransportError.message,
    '无法连接服务器。请检查网络和服务器地址，并确认服务可用。');

  const busy = Object.assign(new Error('internal pool details'), { status: 503 });
  assert.equal(realtimeTranslationErrorMessage(busy), '翻译服务暂时繁忙，请稍后重试。');
  const throttled = Object.assign(new Error('quota'), { status: 429 });
  assert.equal(realtimeTranslationErrorMessage(throttled), '翻译请求较多，请稍后继续。');
  assert.equal(
    realtimeTranslationErrorMessage(new TypeError('network failed')),
    '网络连接异常，翻译暂未完成。',
  );
  assert.equal(
    realtimeTranslationErrorMessage(new Error('HTTP 500')),
    '翻译服务请求失败，请稍后重试。',
  );
  assert.equal(providerTranslationErrorMessage(), 'AI 翻译失败，请稍后重试。');
  await i18n.changeLanguage('en');
  assert.equal(heldTransportError.message,
    'Could not connect to the server. Check your network and server address, and make sure the service is available.');
  assert.equal(providerError.message, 'Provider message 原文', 'provider text remains verbatim across interface language changes');
  assert.equal(providerTranslationErrorMessage(), 'AI translation failed. Please try again later.');
  assert.equal(realtimeTranslationErrorMessage(busy), 'The translation service is busy. Please try again later.');
  await i18n.changeLanguage('zh-CN');
  assert.equal(providerTranslationErrorMessage(), 'AI 翻译失败，请稍后重试。');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
