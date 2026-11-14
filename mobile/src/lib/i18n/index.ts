/**
 * Interface language (block 7b): two tables of texts, zh (the reference) and en, and the one in
 * force. `t()` returns the current table — pure code (lib) and views read texts through it at the
 * moment they build them. The language is set by interface-language.ts (the device's choice, or the
 * system language); routes subscribe to it so a change shows at once.
 * Pure: runs under `node --test` (default Chinese).
 */
import { en } from './en.ts';
import { zh, type Messages } from './zh.ts';

export type Language = 'zh' | 'en';
export type { Messages };

const tables: Record<Language, Messages> = { zh, en };
let current: Language = 'zh';

export function t(): Messages {
  return tables[current];
}

export function language(): Language {
  return current;
}

export function setLanguage(next: Language) {
  current = next;
}

/** The system's preferred languages (BCP 47 tags, first preferred): Chinese ones (zh, yue) → zh, else en. */
export function systemLanguage(tags: readonly string[]): Language {
  const first = tags.find((tag) => /^[a-z]{2,3}\b/i.test(tag));
  return first && /^(zh|yue)\b/i.test(first) ? 'zh' : 'en';
}
