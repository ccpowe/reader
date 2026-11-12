/**
 * 我的's pure logic: the languages content can be translated into, how the translation setting
 * reads, the interface language choices, and the profile and password form rules.
 * Pure: runs under `node --test`.
 */
import type { PreferenceResponse } from './api/schema.gen.ts';

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
  if (!engine) return preference.engine_id || '未选择';
  return engine.available ? engine.label : `${engine.label}（暂不可用）`;
}

export type InterfaceLanguage = 'system' | 'zh' | 'en';
export const INTERFACE_LANGUAGES: { key: InterfaceLanguage; label: string }[] = [
  { key: 'system', label: '跟随系统' },
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
  if (!current) return { field: 'current', message: '请输入当前密码。' };
  if (next.length < 8 || next.length > 128) return { field: 'next', message: '新密码需要 8 到 128 个字符。' };
  if (next === current) return { field: 'next', message: '新密码和当前密码一样。' };
  if (confirm !== next) return { field: 'confirm', message: '两次输入的新密码不一样。' };
  return null;
}
