import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const generated = readFileSync(resolve(root, 'domain/translationRuntimeSources.generated.ts'), 'utf8');
const runtime = name => JSON.parse(generated.match(new RegExp(`export const ${name} = (".*");\\n`))[1]);
const bundle = buildSync({ entryPoints: [resolve(root, 'domain/webTranslationRules.ts')], bundle: true, format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { WEB_TRANSLATION_RULE_REGISTRY: rules } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);

test('YouTube timeline, player captions and webpage comments translate independently', async ({ page }) => {
  await page.route('https://www.youtube.com/watch?v=testvideo01', route => route.fulfill({ contentType: 'text/html', body: `
    <ytd-watch-flexy style="display:block">
      <div id="player" class="html5-video-player" style="position:relative;width:360px;height:200px">
        <video></video><div class="ytp-right-controls"><button class="ytp-subtitles-button" aria-pressed="false" onclick="this.setAttribute('aria-pressed',this.getAttribute('aria-pressed')==='true'?'false':'true')">CC</button></div>
      </div>
      <h1>A documentary about the ocean</h1>
      <div id="comments"><yt-attributed-string style="display:block">This thoughtful comment explains the final scene.</yt-attributed-string></div>
    </ytd-watch-flexy>` }));
  await page.route('**/api/timedtext*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ events: Array.from({ length: 40 }, (_, index) => ({ tStartMs: index * 4000, dDurationMs: 3800, segs: [{ utf8: index === 0 ? 'A caption about the ocean.' : `Sentence number ${index} about marine life.` }] })) }) }));
  await page.goto('https://www.youtube.com/watch?v=testvideo01');
  await page.evaluate(({ web, captions, rules }) => {
    window.messages = [];
    window.ReactNativeWebView = { postMessage: value => window.messages.push(JSON.parse(value)) };
    (0, eval)(`(${web})`)({ channelToken: 'page-token', initialMode: 'original', rules });
    (0, eval)(`(${captions})`)({ channelToken: 'caption-token', initialMode: 'original', initialTimelineMode: 'bilingual', bridgeName: '__readerCaptionBridge' });
    fetch('/api/timedtext?v=testvideo01&lang=en');
  }, { web: runtime('WEB_TRANSLATION_BOOTSTRAP_SOURCE'), captions: runtime('YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE'), rules });
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_batch_plan' && m.channel_token === 'caption-token').length)).toBeGreaterThan(0);
  await page.evaluate(() => {
    const plan = window.messages.find(m => m.type === 'reader_translation_batch_plan' && m.channel_token === 'caption-token');
    const segment = plan.batches[0].segments[0];
    window.__readerCaptionBridge.applyTranslations([{ segment_id: segment.segment_id, translated_text: '关于海洋的字幕。', translation_status: 'succeeded' }], plan.media_epoch);
  });
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1)?.captions[0]?.translated_text)).toBe('关于海洋的字幕。');
  await expect(page.locator('.reader-bilingual-caption-host')).toHaveCount(0);
  await expect(page.locator('[data-reader-caption-control]')).toHaveAttribute('aria-pressed', 'false');

  await page.evaluate(() => { window.__readerCaptionBridge.setMode('bilingual'); window.__readerCaptionBridge.setTimelineMode('original'); });
  await expect(page.locator('.reader-bilingual-caption-target')).toHaveText('关于海洋的字幕。');
  await page.evaluate(() => {
    const reset = window.messages.find(m => m.type === 'reader_translation_navigation_reset' && m.channel_token === 'page-token');
    window.__readerTranslationBridge.confirmEpoch(reset.document_epoch);
    window.__readerTranslationBridge.setMode('bilingual');
  });
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_batch_plan' && m.channel_token === 'page-token').flatMap(m => m.batches.flatMap(b => b.segments)).some(s => s.text.includes('thoughtful comment')))).toBe(true);
  const texts = await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_batch_plan' && m.channel_token === 'page-token').flatMap(m => m.batches.flatMap(b => b.segments.map(s => s.text))));
  expect(texts.some(text => text.includes('caption about') || text.includes('关于海洋'))).toBe(false);
  await page.evaluate(() => window.__readerTranslationBridge.setMode('original'));
  await expect(page.locator('.reader-bilingual-caption-target')).toHaveText('关于海洋的字幕。');
  await expect(page.locator('[data-reader-caption-control]')).toHaveAttribute('aria-pressed', 'true');

  // Expanded native transcript can show the beginning while playback is two minutes ahead.
  await page.evaluate(() => {
    document.querySelector('video').currentTime = 120;
    window.__readerCaptionBridge.setMode('original');
    window.__readerCaptionBridge.setTimelineMode('bilingual');
    const timeline = window.messages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1);
    window.messages = [];
    window.__readerCaptionBridge.setTimelineVisibleSegments(timeline.captions.slice(0, 2).map(c => c.segment_id), timeline.media_epoch);
  });
  const visiblePlan = await page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_batch_plan').at(-1));
  const visibleSegments = visiblePlan.batches.flatMap(batch => batch.segments);
  expect(visibleSegments.map(segment => segment.text)).toEqual(expect.arrayContaining(['A caption about the ocean.', 'Sentence number 1 about marine life.']));
  expect(visibleSegments).toHaveLength(2);
  expect(visibleSegments.some(segment => segment.text.includes('number 30'))).toBe(false);
  await page.evaluate(plan => {
    window.__readerCaptionBridge.applyTranslations(plan.batches.flatMap(batch => batch.segments.map(segment => ({
      segment_id: segment.segment_id, translated_text: '可见字幕轴译文', translation_status: 'succeeded',
      cache_expires_at: new Date(Date.now() + 1500).toISOString(),
    }))), plan.media_epoch);
  }, visiblePlan);
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1)?.captions[1]?.translated_text)).toBe('可见字幕轴译文');
  await expect(page.locator('[data-reader-caption-control]')).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => page.evaluate(() => window.messages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1)?.captions[1]?.translated_text)).toBeNull();

});
