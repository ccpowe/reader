import chinese from './packs/zh-CN';
import english from './packs/en';

/** Enabled interface packs. Content translation locales belong to the server. */
export const appLanguages = [
  { id: 'zh-CN', nativeName: '简体中文', language: 'zh', script: 'Hans', messages: chinese },
  { id: 'en', nativeName: 'English', language: 'en', script: 'Latn', messages: english },
] as const;

export type AppLanguage = typeof appLanguages[number]['id'];
export type AppLanguagePreference = 'system' | AppLanguage;
export const fallbackLanguage: AppLanguage = 'zh-CN';

type LanguagePack = { id: string; language: string; script?: string };

/** Respect preference order before trying the next device language. */
export function resolveSystemLanguage(
  preferences: readonly string[],
  packs: readonly LanguagePack[] = appLanguages,
  fallback: string = fallbackLanguage,
): string {
  for (const preference of preferences) {
    // Hermes supports Intl date/number formatting, but not Intl.Locale.
    // OS tags are BCP-47; ignore Unicode/private extensions for pack matching.
    const tag = preference.replace(/_/g, '-').toLowerCase();
    if (!/^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$/.test(tag)) continue;
    const parts = tag.split('-');
    const extensionIndex = parts.findIndex((part) => part.length === 1);
    const baseParts = extensionIndex < 0 ? parts : parts.slice(0, extensionIndex);
    const baseTag = baseParts.join('-');
    const exact = packs.find((pack) => pack.id.toLowerCase() === baseTag);
    if (exact) return exact.id;
    const language = baseParts[0];
    const candidates = packs.filter((pack) => pack.language === language);
    const explicitScript = baseParts.slice(1).find((part) => /^[a-z]{4}$/.test(part));
    const region = baseParts.slice(1).find((part) => /^[a-z]{2}$/.test(part));
    const script = explicitScript ?? (language === 'zh'
      ? ['tw', 'hk', 'mo'].includes(region ?? '') ? 'hant' : 'hans'
      : undefined);
    const matchingScript = script && candidates.find((pack) => pack.script?.toLowerCase() === script);
    if (matchingScript) return matchingScript.id;
    if (candidates.length) return candidates[0].id;
  }
  return packs.find((pack) => pack.id === fallback)?.id ?? packs[0]?.id ?? fallback;
}

export function normalizeLanguagePreference(value: unknown): AppLanguagePreference {
  return appLanguages.some((pack) => pack.id === value) ? value as AppLanguage : 'system';
}
