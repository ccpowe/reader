import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import Module, { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-translation-runtime-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync('pnpm', [
    'exec', 'tsc', '--ignoreConfig',
    'domain/webTranslation.ts', 'domain/youtubeTranslation.ts',
    '--target', 'es2022', '--module', 'commonjs', '--outDir', outputDirectory,
    '--lib', 'dom,es2022', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const webTranslation = require(join(outputDirectory, 'domain/webTranslation.js'));
  const youtubeTranslation = require(join(outputDirectory, 'domain/youtubeTranslation.js'));
  const originalFunctionToString = Function.prototype.toString;
  Function.prototype.toString = function hermesFunctionToString() {
    if (this.name === 'webTranslationBootstrap' || this.name === 'youTubeTranslationBootstrap') {
      return `function ${this.name}() { [bytecode] }`;
    }
    return originalFunctionToString.call(this);
  };

  try {
    const webScript = webTranslation.createWebTranslationScript({
      channelToken: 'serialization-test', initialMode: 'bilingual',
    });
    const youtubeScript = youtubeTranslation.createYouTubeTranslationScript({
      channelToken: 'serialization-test', initialMode: 'original',
    });
    for (const script of [webScript, youtubeScript]) {
      assert.ok(script.length > 1_000, 'injected runtime must contain its implementation');
      assert.ok(!script.includes('[bytecode]'), 'runtime must not depend on Function#toString');
      assert.doesNotThrow(() => new Function(script), 'injected runtime must be valid JavaScript');
    }
    assert.match(webScript, /ReaderWebTranslationRuntime/, 'web runtime must be an isolated browser IIFE bundle');
    assert.match(webScript, /createTreeWalker/, 'web runtime bundle must contain deterministic TreeWalker discovery');
    assert.match(webScript, /reader_translation_batch_plan/, 'web runtime must preserve the native batch protocol');
    assert.match(webScript, /removeEventListener\("scroll", handleScroll\)/, 'web runtime must remove its named scroll listener');
    assert.match(youtubeScript, /reader-bilingual-caption-host/, 'YouTube runtime must mount its overlay');
    assert.match(youtubeScript, /reader-bilingual-caption-active/, 'YouTube runtime must scope caption hiding');
    assert.match(youtubeScript, /restoreNativeCaptions/, 'YouTube runtime must restore native captions');
    assert.match(youtubeScript, /pagehide/, 'YouTube runtime must clean up discarded documents');
  } finally {
    Function.prototype.toString = originalFunctionToString;
  }
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('translation runtime serialization is Hermes-safe');
