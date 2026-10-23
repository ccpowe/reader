import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import Module from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-product-hunt-date-'));
const require = createRequire(import.meta.url);
const NativeDate = Date;
const originalLoad = Module._load;
const originalTimezone = process.env.TZ;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(process.execPath, [
    join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
    '--ignoreConfig', 'hooks/useRanking.ts', 'domain/productHuntDate.ts',
    '--target', 'es2022', '--module', 'commonjs', '--outDir', outputDirectory,
    '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const { productHuntToday, shiftProductHuntDate, isProductHuntDate, productHuntMonthGrid, shiftProductHuntMonth } = require(join(outputDirectory, 'domain/productHuntDate.js'));
  const at = (instant) => productHuntToday(new NativeDate(instant));
  assert.equal(at('2026-01-01T07:59:59Z'), '2025-12-31');
  assert.equal(at('2026-01-01T08:00:00Z'), '2026-01-01');
  assert.equal(at('2026-03-08T07:59:59Z'), '2026-03-07');
  assert.equal(at('2026-03-08T08:00:00Z'), '2026-03-08');
  assert.equal(at('2026-03-09T06:59:59Z'), '2026-03-08');
  assert.equal(at('2026-03-09T07:00:00Z'), '2026-03-09');
  assert.equal(at('2026-11-01T06:59:59Z'), '2026-10-31');
  assert.equal(at('2026-11-01T07:00:00Z'), '2026-11-01');
  assert.equal(at('2026-11-02T07:59:59Z'), '2026-11-01');
  assert.equal(at('2026-11-02T08:00:00Z'), '2026-11-02');
  for (const timezone of ['Pacific/Kiritimati', 'UTC', 'Asia/Kolkata']) {
    process.env.TZ = timezone;
    assert.equal(at('2026-03-09T06:59:59Z'), '2026-03-08', timezone);
    assert.equal(shiftProductHuntDate('2026-03-08', 1), '2026-03-09', timezone);
    assert.deepEqual(productHuntMonthGrid('2024-02'), { leadingDays: 4, daysInMonth: 29 }, timezone);
    assert.equal(shiftProductHuntMonth('2026-01', -1), '2025-12', timezone);
  }
  if (originalTimezone === undefined) delete process.env.TZ;
  else process.env.TZ = originalTimezone;

  assert.equal(shiftProductHuntDate('2024-02-28', 1), '2024-02-29');
  assert.equal(shiftProductHuntDate('2024-02-29', 1), '2024-03-01');
  assert.equal(shiftProductHuntDate('2025-03-01', -1), '2025-02-28');
  assert.equal(shiftProductHuntDate('2025-12-31', 1), '2026-01-01');
  assert.equal(shiftProductHuntDate('2026-01-01', -1), '2025-12-31');
  assert.equal(shiftProductHuntDate('2026-03-08', -1), '2026-03-07');
  assert.equal(shiftProductHuntDate('2026-11-01', 1), '2026-11-02');
  assert.equal(isProductHuntDate('2024-02-29', '2024-03-01'), true);
  assert.equal(isProductHuntDate('2024-03-02', '2024-03-01'), false);
  for (const invalid of ['2025-02-29', '2026-04-31', '0000-01-01', '2026-2-01', '2026-02-1', '2026-02-01T00:00:00Z', '2026/02/01']) {
    assert.equal(isProductHuntDate(invalid, '2026-12-31'), false, invalid);
    assert.throws(() => shiftProductHuntDate(invalid, 1), RangeError, invalid);
  }
  assert.equal(isProductHuntDate('2026-01-01', 'invalid'), false);
  assert.deepEqual(productHuntMonthGrid('2025-02'), { leadingDays: 6, daysInMonth: 28 });
  assert.deepEqual(productHuntMonthGrid('2000-02'), { leadingDays: 2, daysInMonth: 29 });
  assert.deepEqual(productHuntMonthGrid('1900-02'), { leadingDays: 4, daysInMonth: 28 });
  assert.deepEqual(productHuntMonthGrid('0001-01'), { leadingDays: 1, daysInMonth: 31 });
  assert.equal(shiftProductHuntMonth('2024-02', 1), '2024-03');
  assert.equal(shiftProductHuntMonth('2026-12', 1), '2027-01');
  assert.equal(shiftProductHuntMonth('0001-01', 1), '0001-02');
  for (const invalid of ['0000-01', '2026-00', '2026-13', '2026-1', '2026-01-01']) {
    assert.throws(() => productHuntMonthGrid(invalid), RangeError, invalid);
  }
  assert.throws(() => shiftProductHuntMonth('0001-01', -1), RangeError);
  assert.throws(() => shiftProductHuntMonth('9999-12', 1), RangeError);
  assert.throws(() => shiftProductHuntMonth('2026-03', 0.5), RangeError);

  const { QueryClient } = require('@tanstack/react-query');
  const requests = [];
  Module._load = function load(request, parent, isMain) {
    if (request === '../lib/api') return {
      getRanking: async (_session, kind, options) => {
        requests.push({ kind, date: options?.date });
        return { kind, title: `Products on ${options?.date}`, subtitle: '', fetched_at: '2026-03-09T00:00:00Z', items: [] };
      },
    };
    if (request === './useSegmentTranslationQueue') return { useSegmentTranslationQueue: () => ({}) };
    if (request === './useRankingTitleRetry') return { useRankingTitleRetry: () => ({}) };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => null };
    if (request === '../lib/connection/runtime') return { getActiveRuntime: () => null };
    if (request === '../lib/connection') return { captureRuntimeContext: () => null, isRuntimeContextCurrent: () => false };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { rankingQueryOptions, prefetchRanking } = require(join(outputDirectory, 'hooks/useRanking.js'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const session = { user: { id: 'reader' }, access_token: 'test-token' };
  const query = (options) => rankingQueryOptions(session, 'product_hunt', options, 'server', null, 'en', false, null);
  const day1 = query({ date: '2026-03-07' });
  const day2 = query({ date: '2026-03-08' });
  const result1 = await client.fetchQuery(day1);
  const result2 = await client.fetchQuery(day2);
  assert.equal(result1.title, 'Products on 2026-03-07');
  assert.equal(result2.title, 'Products on 2026-03-08');
  assert.equal(client.getQueryData(day1.queryKey), result1);
  assert.equal(client.getQueryData(day2.queryKey), result2);
  await prefetchRanking(client, session, 'product_hunt', { date: '2026-03-07' }, 'server', null, 'en', false, null);
  assert.equal(requests.length, 2, 'prefetch reuses the selected date cache');

  let clock = '2026-03-08T07:59:59Z';
  globalThis.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [clock])); }
  };
  const beforeMidnight = query(undefined);
  clock = '2026-03-08T08:00:01Z';
  const afterMidnight = query(undefined);
  const laterClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  assert.equal((await laterClient.fetchQuery(beforeMidnight)).title, 'Products on 2026-03-07');
  assert.equal((await laterClient.fetchQuery(afterMidnight)).title, 'Products on 2026-03-08');
  laterClient.clear();
  client.clear();
} finally {
  if (originalTimezone === undefined) delete process.env.TZ;
  else process.env.TZ = originalTimezone;
  globalThis.Date = NativeDate;
  Module._load = originalLoad;
  rmSync(outputDirectory, { recursive: true, force: true });
}
