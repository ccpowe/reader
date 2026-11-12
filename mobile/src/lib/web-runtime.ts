/**
 * The scripts injected into reader WebViews, built from the typed runtime sources in `domain/`
 * (`pnpm run generate:translation-runtime`). Moves into src/ with the rest of the old tree (block 8).
 */
import { YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE } from '../../domain/translationRuntimeSources.generated';
import { createWebStorageScript } from '../../domain/webStorage';
import { createWebTranslationScript } from '../../domain/webTranslation';

import type { CaptionBootstrapConfig } from './caption-host';
import { jsValue, type BootstrapConfig } from './web-translation-host';

/** Drops the site's Cache API bodies now and then; logins (cookies, storage) stay. */
export const WEB_STORAGE_SCRIPT = createWebStorageScript();

export function webTranslationScript(config: BootstrapConfig): string {
  return createWebTranslationScript(config);
}

/** The labels of the 双语 button the caption runtime adds to the player. */
export const CAPTION_MESSAGES: CaptionBootstrapConfig['interfaceMessages'] = {
  disableBilingualCaptions: '关闭双语字幕',
  enableBilingualCaptions: '开启双语字幕',
  bilingual: '双语',
};

export function captionScript(config: CaptionBootstrapConfig): string {
  return `(${YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE})(${jsValue(config)});true;`;
}
