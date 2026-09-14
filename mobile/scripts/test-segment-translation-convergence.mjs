import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-translation-convergence-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'domain/translationConvergence.ts',
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
    SEGMENT_CONVERGENCE_TIMEOUT_MS,
    segmentConvergenceTimeoutMessage,
    titleConvergenceTimeoutMessage,
    nextTranslationConvergencePollAt,
    translationConvergenceDecision,
  } = require(join(outputDirectory, 'domain/translationConvergence.js'));
  const { i18n } = require(join(outputDirectory, 'i18n/index.js'));
  await i18n.changeLanguage('zh-CN');

  const startedAt = 10_000;
  for (let elapsed = 0; elapsed < SEGMENT_CONVERGENCE_TIMEOUT_MS; elapsed += 750) {
    assert.equal(
      translationConvergenceDecision('pending', startedAt, startedAt + elapsed),
      'retry',
      'pending work may converge inside the bounded window',
    );
  }
  assert.equal(
    translationConvergenceDecision(
      'running',
      startedAt,
      startedAt + SEGMENT_CONVERGENCE_TIMEOUT_MS,
    ),
    'timed_out',
    'permanently pending work must stop at the deadline',
  );
  assert.equal(
    nextTranslationConvergencePollAt(
      startedAt,
      startedAt + SEGMENT_CONVERGENCE_TIMEOUT_MS - 1_000,
      30_000,
    ),
    startedAt + SEGMENT_CONVERGENCE_TIMEOUT_MS,
    'retry_after must not schedule polling beyond the convergence deadline',
  );

  assert.equal(
    translationConvergenceDecision('pending', startedAt, startedAt + 2_250),
    'retry',
  );
  assert.equal(
    translationConvergenceDecision('succeeded', startedAt, startedAt + 3_000),
    'settled',
    'normal worker convergence must settle without timing out',
  );

  const reenqueueStartedAt = startedAt + SEGMENT_CONVERGENCE_TIMEOUT_MS + 5_000;
  assert.equal(
    translationConvergenceDecision('pending', reenqueueStartedAt, reenqueueStartedAt),
    'retry',
    'a fresh enqueue gets a fresh convergence window',
  );
  assert.equal(
    translationConvergenceDecision('succeeded', reenqueueStartedAt, reenqueueStartedAt + 1_500),
    'settled',
    'a re-enqueued segment can recover normally',
  );
  assert.equal(segmentConvergenceTimeoutMessage(),
    '翻译服务长时间未完成该段内容，已停止自动等待。请点击“重试翻译”再次尝试。');
  assert.equal(titleConvergenceTimeoutMessage(),
    '翻译标题超时，已停止自动等待。请点击“重试翻译”再次尝试。');
  await i18n.changeLanguage('en');
  assert.equal(segmentConvergenceTimeoutMessage(),
    'Translation of this passage timed out and automatic waiting has stopped. Tap “Retry translation” to try again.');
  assert.equal(titleConvergenceTimeoutMessage(),
    'Title translation timed out and automatic waiting has stopped. Tap “Retry translation” to try again.');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
