/**
 * The scripts injected into reader WebViews, built from the typed runtime sources in `domain/`
 * (`pnpm run generate:translation-runtime`). Moves into src/ with the rest of the old tree (block 8).
 */
import { createWebStorageScript } from '../../domain/webStorage';
import { createWebTranslationScript } from '../../domain/webTranslation';

import type { BootstrapConfig } from './web-translation-host';

/** Drops the site's Cache API bodies now and then; logins (cookies, storage) stay. */
export const WEB_STORAGE_SCRIPT = createWebStorageScript();

export function webTranslationScript(config: BootstrapConfig): string {
  return createWebTranslationScript(config);
}
