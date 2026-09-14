// RN 0.86's Hermes lacks Locale/PluralRules. Load only bundled language data.
import '@formatjs/intl-locale/polyfill.js';
import '@formatjs/intl-pluralrules/polyfill.js';
import '@formatjs/intl-pluralrules/locale-data/zh.js';
import '@formatjs/intl-pluralrules/locale-data/en.js';
import { createInstance, type Resource } from 'i18next';
import { initReactI18next } from 'react-i18next';
import { appLanguages, fallbackLanguage } from './registry';

export const resources: Resource = Object.fromEntries(
  appLanguages.map((pack) => [pack.id, pack.messages]),
);

// Keep this module independent of native APIs: domain code and mounted tests
// share the same synchronous, offline message runtime as the app.
export const i18n = createInstance();
void i18n.use(initReactI18next).init({
  resources,
  lng: fallbackLanguage,
  fallbackLng: fallbackLanguage,
  supportedLngs: appLanguages.map((pack) => pack.id),
  load: 'currentOnly',
  defaultNS: 'common',
  initAsync: false,
  interpolation: { escapeValue: false },
  returnEmptyString: false,
  react: { useSuspense: false },
});

export { useTranslation } from 'react-i18next';
