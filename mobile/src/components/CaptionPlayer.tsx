/**
 * The embedded YouTube player with the caption runtime injected (lib/injected/youtubeTranslation.ts) and
 * its host (src/lib/caption-host.ts). The runtime must run before the player fetches its caption
 * track, so it is injected before the content loads, and again after (it is idempotent). One host
 * per document: the parent remounts this with a `key`, and gets the host through `onHost`.
 */
import { useRef, useState } from 'react';
import type { WebViewProps } from 'react-native-webview';
import type { WebViewErrorEvent, WebViewMessageEvent, WebViewNavigationEvent } from 'react-native-webview/lib/WebViewTypes';

import { CaptionHost, type CaptionState, type CaptionTimeline } from '@/lib/caption-host';
import { translateTexts } from '@/lib/queries/translation';
import { useMountEffect } from '@/lib/use-mount-effect';
import { CAPTION_MESSAGES, captionScript } from '@/lib/web-runtime';
import type { TranslationMode } from '@/lib/web-translation-host';
import { WebView, type WebViewHandle } from '@/lib/webview';
import { interfaceLanguage } from '@/lib/interface-language';

export function CaptionPlayer({
  mode,
  timelineMode,
  onHost,
  onCaptionState,
  onTimeline,
  onModeRequest,
  webRef,
  onLoadStart,
  onLoadEnd,
  ...props
}: Omit<WebViewProps, 'injectedJavaScript' | 'injectedJavaScriptBeforeContentLoaded' | 'onMessage'> & {
  /** the modes this document starts in; later changes go through the host */
  mode: TranslationMode;
  timelineMode: TranslationMode;
  onHost?: (host: CaptionHost | null) => void;
  /** kept from the first render (pass stable functions, e.g. state setters) */
  onCaptionState?: (state: CaptionState) => void;
  onTimeline?: (timeline: CaptionTimeline) => void;
  onModeRequest?: (mode: TranslationMode) => void;
  webRef?: (handle: WebViewHandle | null) => void;
}) {
  const web = useRef<WebViewHandle | null>(null);
  const [host] = useState(
    () =>
      new CaptionHost({
        bootstrap: captionScript,
        send: (texts, signal) => translateTexts('caption', texts, signal),
        onState: (state) => onCaptionState?.(state),
        onTimeline: (timeline) => onTimeline?.(timeline),
        onModeRequest: (requested) => onModeRequest?.(requested),
        mode,
        timelineMode,
      }),
  );
  const [script] = useState(() => host.script(CAPTION_MESSAGES));
  useMountEffect(() => {
    onHost?.(host);
    // The player's 双语 button follows the interface language: the script, injected again, relabels it.
    const stopFollowing = interfaceLanguage.subscribe(() => web.current?.injectJavaScript(host.script(CAPTION_MESSAGES)));
    return () => {
      stopFollowing();
      host.dispose();
      onHost?.(null);
    };
  });

  return (
    <WebView
      {...props}
      injectedJavaScript={script}
      injectedJavaScriptBeforeContentLoaded={script}
      javaScriptEnabled
      onLoadEnd={(event: WebViewNavigationEvent | WebViewErrorEvent) => {
        // Built again: the script also sets the modes, which may have changed since mount.
        web.current?.injectJavaScript(host.script(CAPTION_MESSAGES));
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
        host.attach(handle ? (s) => handle.injectJavaScript(s) : null);
        webRef?.(handle);
      }}
    />
  );
}
