import assert from 'node:assert/strict';
import { build } from 'esbuild';

const { outputFiles } = await build({
  entryPoints: [new URL('../domain/translationDiagnostics.ts', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'esm',
});
const { createTranslationDiagnostics } = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);
let time = 1_000;
const buffer = createTranslationDiagnostics(() => time);
buffer.record('render_rejected', {
  segmentId: 'web-epoch:main:11:r1', reason: 'placeholder_mismatch',
  source_text: 'PRIVATE_SOURCE', token: 'PRIVATE_AUTH',
  requestId: 'https://private.example/?token=PRIVATE_URL',
  missingTokens: ['⟪READER_OPEN_0⟫', 'PRIVATE_TEXT'],
  durationMs: Infinity, count: -1,
  taskId: 'sk-proj-PRIVATE_KEY', engineId: 'sb_secret_PRIVATE_KEY',
  runtimeVersion: 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.signature',
});
let exported = buffer.exportJson();
assert(!exported.includes('PRIVATE'));
assert(!exported.includes('eyJ'));
assert.deepEqual(JSON.parse(exported).events[0].fields, {
  segmentId: 'web-epoch:main:11:r1', reason: 'placeholder_mismatch', missingTokens: ['⟪READER_OPEN_0⟫'],
});
assert.equal(buffer.record('success', { level: 'debug' }), false);
buffer.setDetailed(true);
assert.equal(buffer.record('success', { level: 'debug' }), true);
assert.equal(buffer.record('success', { level: 'debug' }), false);
assert.equal(buffer.record('invalid event'), false);
for (let index = 0; index < 100; index++) buffer.record('failure', { segmentId: `segment-${index}` });
assert.equal(JSON.parse(buffer.exportJson()).events.length, 40);
assert(JSON.parse(buffer.exportJson()).droppedEvents > 0);
assert.equal(buffer.record('state_snapshot', { failedCount: 7 }), true);
assert.equal(JSON.parse(buffer.exportJson()).events.at(-1).fields.failedCount, 7);
time += 1_000;
assert.equal(buffer.record('recovered', { segmentId: 'target-segment' }), true);
for (let index = 0; index < 1_000; index++) {
  time += 1_001;
  buffer.record('render_rejected', {
    segmentId: `segment-${index}`, batchId: 'x'.repeat(200), documentEpoch: 'y'.repeat(200),
  });
}
exported = buffer.exportJson();
assert(Buffer.byteLength(exported) < 128_000);
assert(JSON.parse(exported).events.length <= 400);
assert.equal(JSON.parse(exported).events.at(-1).fields.segmentId, 'segment-999');
buffer.clear();
assert.equal(JSON.parse(buffer.exportJson()).events.length, 0);
assert.equal(JSON.parse(buffer.exportJson()).droppedEvents, 0);
buffer.record('selection_diagnostic', { displayStatus: 'clipped' });
assert.equal(JSON.parse(buffer.exportJson()).events.at(-1).fields.displayStatus, 'clipped');
time += 1_001;
buffer.record('selection_diagnostic', { displayStatus: 'PRIVATE_STYLE' });
assert.equal(JSON.parse(buffer.exportJson()).events.at(-1).fields.displayStatus, undefined);
console.log('Translation diagnostic buffering, content filtering, rate limits and export bounds passed.');

// Release builds must not collect developer diagnostics; Metro dev retains them.
for (const enabled of [false, true]) {
  const result = await build({
    entryPoints: [new URL('../domain/translationDiagnostics.ts', import.meta.url).pathname],
    bundle: true, write: false, platform: 'node', format: 'esm',
    define: { __DEV__: String(enabled) },
  });
  const runtime = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
  assert.equal(runtime.TRANSLATION_DIAGNOSTICS_ENABLED, enabled);
  runtime.setTranslationDiagnosticsDetailed(true);
  assert.equal(runtime.recordTranslationDiagnostic('release_boundary', { level: 'debug' }), enabled);
  assert.equal(JSON.parse(runtime.exportTranslationDiagnostics()).events.length, enabled ? 1 : 0);
}
console.log('Development/release diagnostic collection boundary passed.');
