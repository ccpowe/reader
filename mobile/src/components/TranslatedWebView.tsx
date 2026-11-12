/**
 * A WebView with the page runtime injected (bilingual translation, article extraction) and its host
 * (src/lib/web-translation-host.ts). One host per document: the parent remounts this with a `key`
 * when the document changes, and gets the host through `onHost` to switch the mode, retry, or
 * extract. Everything else is passed to the WebView.
 */
import { useRef, useState } from 'react';
import { Platform } from 'react-native';
import type { WebViewProps } from 'react-native-webview';
import type { WebViewErrorEvent, WebViewMessageEvent, WebViewNavigationEvent } from 'react-native-webview/lib/WebViewTypes';

import { translateTexts } from '@/lib/queries/translation';
import { useMountEffect } from '@/lib/use-mount-effect';
import { WEB_STORAGE_SCRIPT, webTranslationScript } from '@/lib/web-runtime';
import { WebTranslationHost, type HostState, type TranslationMode } from '@/lib/web-translation-host';
import { WebView, type WebViewHandle } from '@/lib/webview';

/** Android keeps no HTTP cache for these pages; cleared once per app run (old app, domain/webStorage.ts). */
let clearedAndroidCache = false;

export function TranslatedWebView({
  mode,
  profile,
  readerContent,
  onHost,
  onTranslationState,
  webRef,
  onLoadStart,
  onLoadEnd,
  ...props
}: Omit<WebViewProps, 'injectedJavaScript' | 'onMessage'> & {
  /** the mode this document starts in; later changes go through the host */
  mode: TranslationMode;
  profile?: 'reader';
  readerContent?: { html: string; url: string };
  onHost?: (host: WebTranslationHost | null) => void;
  /** kept from the first render (pass a stable function, e.g. a state setter) */
  onTranslationState?: (state: HostState) => void;
  webRef?: (handle: WebViewHandle | null) => void;
}) {
  // The host injects into whichever WebView instance is mounted (`attach` in `ref` below).
  const [host] = useState(
    () =>
      new WebTranslationHost({
        bootstrap: webTranslationScript,
        send: (texts, signal) => translateTexts('web_segment', texts, signal),
        onState: (state) => onTranslationState?.(state),
        mode,
        profile,
        readerContent,
      }),
  );
  const web = useRef<WebViewHandle | null>(null);
  useMountEffect(() => {
    onHost?.(host);
    return () => {
      host.dispose();
      onHost?.(null);
    };
  });

  return (
    <WebView
      {...props}
      // iOS cacheEnabled=false would switch to a non-persistent store (logins lost); Android only.
      cacheEnabled={Platform.OS === 'android' ? false : props.cacheEnabled}
      cacheMode={Platform.OS === 'android' ? 'LOAD_NO_CACHE' : props.cacheMode}
      injectedJavaScript={`${WEB_STORAGE_SCRIPT}\n;${host.script()}\n;true;`}
      javaScriptEnabled
      onLoadEnd={(event: WebViewNavigationEvent | WebViewErrorEvent) => {
        web.current?.injectJavaScript(WEB_STORAGE_SCRIPT);
        if (Platform.OS === 'android' && !clearedAndroidCache && web.current) {
          web.current.clearCache?.(true);
          clearedAndroidCache = true;
        }
        host.loadEnd();
        onLoadEnd?.(event);
      }}
      onLoadStart={(event: WebViewNavigationEvent) => {
        host.loadStart();
        onLoadStart?.(event);
      }}
      onMessage={(event: WebViewMessageEvent) => host.message(event.nativeEvent.data)}
      ref={(handle: WebViewHandle | null) => {
        web.current = handle;
        host.attach(handle ? (script) => handle.injectJavaScript(script) : null);
        webRef?.(handle);
      }}
    />
  );
}
