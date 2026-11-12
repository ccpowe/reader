/**
 * The scripts injected into reader WebViews, built from the typed runtime sources in `injected/`
 * (`pnpm run generate:translation-runtime`).
 */
import { YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE } from './injected/translationRuntimeSources.generated';
import { createWebStorageScript } from './injected/webStorage';
import { createWebTranslationScript } from './injected/webTranslation';

import type { CaptionBootstrapConfig } from './caption-host';
import { jsValue, type BootstrapConfig } from './web-translation-host';
import { t } from './i18n/index.ts';

/** Drops the site's Cache API bodies now and then; logins (cookies, storage) stay. */
export const WEB_STORAGE_SCRIPT = createWebStorageScript();

export function webTranslationScript(config: BootstrapConfig): string {
  return createWebTranslationScript(config);
}

/** The labels of the 双语 button the caption runtime adds to the player (interface language, read when injected). */
export const CAPTION_MESSAGES: CaptionBootstrapConfig['interfaceMessages'] = {
  get disableBilingualCaptions() {
    return t().reader.web.disableBilingualCaptions;
  },
  get enableBilingualCaptions() {
    return t().reader.web.enableBilingualCaptions;
  },
  get bilingual() {
    return t().reader.web.bilingual;
  },
};

export function captionScript(config: CaptionBootstrapConfig): string {
  return `(${YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE})(${jsValue(config)});true;`;
}
