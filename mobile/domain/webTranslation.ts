import { WEB_TRANSLATION_BOOTSTRAP_SOURCE } from './translationRuntimeSources.generated';
import {
  WEB_TRANSLATION_RULE_REGISTRY,
  type ResolvedWebTranslationRule,
} from './webTranslationRules';

export { WEB_TRANSLATION_RUNTIME_VERSION } from './translationRuntimeSources.generated';

export type TranslationDisplayMode = 'original' | 'bilingual' | 'translated';
export type { ResolvedWebTranslationRule, WebTranslationProfilePatch } from './webTranslationRules';

type WebTranslationScriptConfig = {
  channelToken: string;
  initialMode: TranslationDisplayMode;
  /** reader and audited social adapters may be explicitly selected by native code. */
  profile?: string;
  readerContent?: { html: string; url: string };
};

type WebTranslationBootstrapConfig = WebTranslationScriptConfig & {
  rules: readonly ResolvedWebTranslationRule[];
};

/** Build the isolated deterministic DOM runtime injected into a WebView. */
export function createWebTranslationScript(config: WebTranslationScriptConfig): string {
  const runtimeConfig: WebTranslationBootstrapConfig = {
    ...config,
    rules: WEB_TRANSLATION_RULE_REGISTRY,
  };
  return `(${WEB_TRANSLATION_BOOTSTRAP_SOURCE})(${JSON.stringify(runtimeConfig)});true;`;
}
