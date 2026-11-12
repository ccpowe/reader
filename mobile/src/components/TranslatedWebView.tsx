/**
 * A WebView with the page runtime injected (bilingual translation, article extraction) and its host
 * (src/lib/web-translation-host.ts). One host per document: the parent remounts this with a `key`
 * when the document changes, and gets the host through `onHost` to switch the mode, retry, or
 * extract. Everything else is passed to the WebView.
 *
 * `captions` (the YouTube web page, old app): the caption runtime runs there too, under its own
 * bridge name and host, so the player on the page keeps its 双语 button and translated captions.
 */
import { useRef, useState } from 'react';
import { Platform } from 'react-native';
import type { WebViewProps } from 'react-native-webview';
import type { WebViewErrorEvent, WebViewMessageEvent, WebViewNavigationEvent } from 'react-native-webview/lib/WebViewTypes';

import { CaptionHost } from '@/lib/caption-host';
import { translateTexts } from '@/lib/queries/translation';
import { useMountEffect } from '@/lib/use-mount-effect';
import { CAPTION_MESSAGES, WEB_STORAGE_SCRIPT, captionScript, webTranslationScript } from '@/lib/web-runtime';
import { WebTranslationHost, type HostState, type TranslationMode } from '@/lib/web-translation-host';
import { WebView, type WebViewHandle } from '@/lib/webview';
import { interfaceLanguage } from '@/lib/interface-language';

/** Android keeps no HTTP cache for these pages; cleared once per app run (old app, domain/webStorage.ts). */
let clearedAndroidCache = false;

export function TranslatedWebView({
  mode,
  profile,
  readerContent,
  onHost,
  onTranslationState,
  webRef,
  captions,
  onLoadStart,
  onLoadEnd,
  ...props
}: Omit<WebViewProps, 'injectedJavaScript' | 'injectedJavaScriptBeforeContentLoaded' | 'onMessage'> & {
  /** the mode this document starts in; later changes go through the host */
  mode: TranslationMode;
  profile?: 'reader';
  readerContent?: { html: string; url: string };
  onHost?: (host: WebTranslationHost | null) => void;
  /** kept from the first render (pass a stable function, e.g. a state setter) */
  onTranslationState?: (state: HostState) => void;
  webRef?: (handle: WebViewHandle | null) => void;
  /** the player's caption translation on a YouTube web page; kept from the first render */
  captions?: {
    mode: TranslationMode;
    onModeRequest: (mode: TranslationMode) => void;
    onHost?: (host: CaptionHost | null) => void;
  };
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
  const [captionHost] = useState(() =>
    captions
      ? new CaptionHost({
          bootstrap: captionScript,
          send: (texts, signal) => translateTexts('caption', texts, signal),
          onState: () => {},
          onTimeline: () => {},
          onModeRequest: (requested) => captions.onModeRequest(requested),
          mode: captions.mode,
          timelineMode: 'original',
          bridgeName: '__readerCaptionBridge',
        })
      : null,
  );
  // Before the content loads, so it sees the player's caption requests.
  const [captionStart] = useState(() => captionHost?.script(CAPTION_MESSAGES));
  const web = useRef<WebViewHandle | null>(null);
  useMountEffect(() => {
    onHost?.(host);
    if (captionHost) captions?.onHost?.(captionHost);
    // The player's 双语 button follows the interface language: the script, injected again, relabels it.
    const stopFollowing = captionHost
      ? interfaceLanguage.subscribe(() => web.current?.injectJavaScript(captionHost.script(CAPTION_MESSAGES)))
      : () => undefined;
    return () => {
      stopFollowing();
      host.dispose();
      captionHost?.dispose();
      onHost?.(null);
      if (captionHost) captions?.onHost?.(null);
    };
  });

  return (
    <WebView
      {...props}
      // iOS cacheEnabled=false would switch to a non-persistent store (logins lost); Android only.
      cacheEnabled={Platform.OS === 'android' ? false : props.cacheEnabled}
      cacheMode={Platform.OS === 'android' ? 'LOAD_NO_CACHE' : props.cacheMode}
      injectedJavaScript={`${WEB_STORAGE_SCRIPT}\n;${host.script()}\n;${captionStart ?? ''}\n;true;`}
      injectedJavaScriptBeforeContentLoaded={captionStart}
      javaScriptEnabled
      onLoadEnd={(event: WebViewNavigationEvent | WebViewErrorEvent) => {
        web.current?.injectJavaScript(WEB_STORAGE_SCRIPT);
        if (Platform.OS === 'android' && !clearedAndroidCache && web.current) {
          web.current.clearCache?.(true);
          clearedAndroidCache = true;
        }
        host.loadEnd();
        if (captionHost) {
          web.current?.injectJavaScript(captionHost.script(CAPTION_MESSAGES));
          captionHost.loadEnd();
        }
        onLoadEnd?.(event);
      }}
      onLoadStart={(event: WebViewNavigationEvent) => {
        host.loadStart();
        captionHost?.loadStart();
        onLoadStart?.(event);
      }}
      onMessage={(event: WebViewMessageEvent) => {
        host.message(event.nativeEvent.data);
        captionHost?.message(event.nativeEvent.data);
      }}
      ref={(handle: WebViewHandle | null) => {
        web.current = handle;
        host.attach(handle ? (script) => handle.injectJavaScript(script) : null);
        captionHost?.attach(handle ? (script) => handle.injectJavaScript(script) : null);
        webRef?.(handle);
      }}
    />
  );
}
