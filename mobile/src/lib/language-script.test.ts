import assert from 'node:assert/strict';
import { test } from 'node:test';

import { alreadyInLanguage } from './language-script.ts';

test('Chinese text is not translated into Chinese', () => {
  assert.equal(alreadyInLanguage('让老音箱焕发新生：Home Assistant Connect AUX-2 首发体验', 'zh-CN'), true);
  assert.equal(alreadyInLanguage('少数派', 'zh-TW'), true);
  assert.equal(alreadyInLanguage('繁體中文的標題', 'zh-Hant'), true);
  // link placeholders of a page segment are not words
  assert.equal(alreadyInLanguage('⟪READER_OPEN_0⟫ 南桥北桥不是桥 ⟪READER_OPEN_1⟫ ⟪READER_CLOSE_1⟫ ⟪READER_CLOSE_0⟫', 'zh-CN'), true);
  assert.equal(alreadyInLanguage('⟪READER_OPEN_0⟫Open Home Foundation⟪READER_CLOSE_0⟫ runs it', 'zh-CN'), false);
});

test('mostly Latin text is translated even with a few Chinese characters', () => {
  assert.equal(alreadyInLanguage('Show HN: Ramasite, a Self-Contained CMS', 'zh-CN'), false);
  assert.equal(alreadyInLanguage('DeepSeek 4.1 flash is here and it is fast (快)', 'zh-CN'), false);
});

test('Japanese is told apart from Chinese by kana', () => {
  assert.equal(alreadyInLanguage('日本語の記事を読む', 'zh-CN'), false);
  assert.equal(alreadyInLanguage('日本語の記事を読む', 'ja'), true);
  assert.equal(alreadyInLanguage('中文标题', 'ja'), false);
});

test('Korean', () => {
  assert.equal(alreadyInLanguage('한국어 기사 제목', 'ko'), true);
  assert.equal(alreadyInLanguage('한국어 기사 제목', 'zh-CN'), false);
  // Han characters in a Korean headline do not make it Chinese
  assert.equal(alreadyInLanguage('美 연준 금리 동결', 'zh-CN'), false);
  assert.equal(alreadyInLanguage('美 연준 금리 동결', 'ko'), true);
});

test('Latin-script targets and empty input always translate', () => {
  assert.equal(alreadyInLanguage('An English title', 'en'), false);
  assert.equal(alreadyInLanguage('Un titre français', 'fr'), false);
  assert.equal(alreadyInLanguage('2026 · 100%', 'zh-CN'), false);
  assert.equal(alreadyInLanguage('中文', null), false);
});
