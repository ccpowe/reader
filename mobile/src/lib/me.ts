/**
 * 我的's pure logic: the languages content can be translated into, how the translation setting
 * reads, the interface language choices, and the profile and password form rules.
 * Pure: runs under `node --test`.
 */
import type { PreferenceResponse } from './api/schema.gen.ts';
import { t } from './i18n/index.ts';

/** Content languages offered (the server takes any BCP 47 tag; these are the common ones). */
export const TARGET_LOCALES = [
  { key: 'zh-CN', label: '简体中文' },
  { key: 'zh-TW', label: '繁體中文' },
  { key: 'en', label: 'English' },
  { key: 'ja', label: '日本語' },
  { key: 'ko', label: '한국어' },
  { key: 'fr', label: 'Français' },
  { key: 'de', label: 'Deutsch' },
  { key: 'es', label: 'Español' },
] as const;

export function localeLabel(tag: string): string {
  const lower = tag.toLowerCase();
  return TARGET_LOCALES.find((l) => l.key.toLowerCase() === lower)?.label ?? tag;
}

export function engineLabel(preference: Pick<PreferenceResponse, 'engine_id' | 'engines'>): string {
  const engine = preference.engines.find((e) => e.id === preference.engine_id);
  if (!engine) return preference.engine_id || t().me.noEngine;
  return engine.available ? engine.label : t().me.engineLabelUnavailable(engine.label);
}

export type InterfaceLanguage = 'system' | 'zh' | 'en';
export const INTERFACE_LANGUAGES: { key: InterfaceLanguage; label: string }[] = [
  {
    key: 'system',
    get label() {
      return t().me.followSystem;
    },
  },
  { key: 'zh', label: '简体中文' },
  { key: 'en', label: 'English' },
];

export function interfaceLanguageLabel(value: InterfaceLanguage): string {
  return INTERFACE_LANGUAGES.find((l) => l.key === value)?.label ?? value;
}

/** A display name as typed: trimmed, at most 120 characters; empty = none (the email's name shows). */
export function displayNameInput(text: string): string | null {
  const name = text.trim();
  return name ? name.slice(0, 120) : null;
}

export type PasswordProblem = { field: 'current' | 'next' | 'confirm'; message: string } | null;

/** What stops a password change before it is sent. */
export function passwordProblem(current: string, next: string, confirm: string): PasswordProblem {
  const me = t().me;
  if (!current) return { field: 'current', message: me.passwordRequired };
  if (next.length < 8 || next.length > 128) return { field: 'next', message: me.passwordLength };
  if (next === current) return { field: 'next', message: me.passwordSame };
  if (confirm !== next) return { field: 'confirm', message: me.passwordMismatch };
  return null;
}
