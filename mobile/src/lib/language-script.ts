/**
 * Whether a text is already in the language it would be translated into, judged by its writing
 * system, so it is not sent to the model (a Chinese page read in Chinese, a Chinese title). Only
 * for targets with their own script — Chinese, Japanese, Korean; a Latin-script target cannot tell
 * English from French by letters alone, so those texts are always translated.
 * Mirrored on the server for queued titles: backend/src/reader/translation/script.py.
 * Pure: runs under `node --test`.
 */

const HAN = /\p{Script=Han}/gu;
const KANA = /[\p{Script=Hiragana}\p{Script=Katakana}]/gu;
const HANGUL = /\p{Script=Hangul}/gu;
/** A run of Latin (or other alphabetic) letters: one word. */
const WORD = /[\p{Script=Latin}\p{Script=Cyrillic}\p{Script=Greek}]+/gu;

const PLACEHOLDER = /\u27eaREADER_(?:(?:OPEN|CLOSE)_)?\d+\u27eb/g;

const count = (text: string, pattern: RegExp) => text.match(pattern)?.length ?? 0;

/**
 * A CJK character weighs about as much as a word, so the target's characters are compared with the
 * number of words in other alphabets: "Home Assistant 首发体验" is Chinese, "DeepSeek is fast (快)" is not.
 */
export function alreadyInLanguage(text: string, locale: string | null | undefined): boolean {
  if (!locale) return false;
  const language = locale.toLowerCase().split('-')[0];
  // Page segments carry link / markup placeholders (⟪READER_OPEN_0⟫): not words of the text.
  const prose = text.replace(PLACEHOLDER, ' ');
  const words = count(prose, WORD);
  const kana = count(prose, KANA);
  const hangul = count(prose, HANGUL);
  switch (language) {
    case 'zh':
    case 'yue':
      // Kana means Japanese and Hangul Korean, even when most characters are Han ("美 연준 금리").
      return kana === 0 && count(prose, HAN) > words + hangul;
    case 'ja':
      return kana > 0 && count(prose, HAN) + kana > words + hangul;
    case 'ko':
      return hangul > words + kana;
    default:
      return false;
  }
}
