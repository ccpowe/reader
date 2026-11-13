/**
 * The reader page (PRODUCT §3.6) for one document: chooses the view by kind (src/lib/reader.ts)
 * and drives one WebView — the original page, reader mode, or the YouTube player. Web pages and
 * reader mode carry the translation runtime (TranslatedWebView): 原文 ↔ 双语 (P24), and reader
 * mode for articles without a full body extracts the article from the page. Videos: the embedded
 * player with the caption runtime (CaptionPlayer) and the 字幕轴 under it (CaptionList, old app);
 * the floating tool translates the list, the player's own 双语 button the captions on the video.
 * Used by the article route and the board entry route (P29), which supply the document and saving.
 */
import { IconArticle, IconBrandYoutube, IconLanguage, IconMessages, IconWorld } from '@tabler/icons-react-native';
import { router, useFocusEffect } from 'expo-router';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useReducer, useRef, useState } from 'react';
import { Appearance, BackHandler, Linking, Share, useColorScheme, View } from 'react-native';
import Animated from 'react-native-reanimated';
import type {
  ShouldStartLoadRequest,
  WebViewErrorEvent,
  WebViewHttpErrorEvent,
  WebViewNavigation,
  WebViewProgressEvent,
} from 'react-native-webview/lib/WebViewTypes';

import { CaptionList, type CaptionListHandle } from '@/components/CaptionList';
import { CaptionPlayer } from '@/components/CaptionPlayer';
import { TranslatedWebView } from '@/components/TranslatedWebView';
import { EMPTY_TIMELINE, captionProblem, type CaptionHost, type CaptionState, type CaptionTimeline } from '@/lib/caption-host';
import type { SchedulerStatus } from '@/lib/translation-scheduler';
import type { WebViewHandle } from '@/lib/webview';
import { backStaysInside, documentKey, initialReaderPage, readerKey, readerPageReducer, shareUrl } from '@/lib/reader-page';
import {
  YOUTUBE_APP_REFERER,
  articleDate,
  hasFullBody,
  readerDocument,
  readerKind,
  safeHttpUrl,
  youtubeEmbedUrl,
  youtubeWatchUrl,
} from '@/lib/reader';
import { translationProblem, type HostState, type TranslationMode, type WebTranslationHost } from '@/lib/web-translation-host';
import { ArticleView, type ReaderTool } from '@/screens/ArticleView';
import { Button, Text, Toast } from '@/ui';
import { duration } from '@/ui/theme';
import { queryClient, useIdentity } from '@/lib/app-session';
import { translationPreferenceQuery } from '@/lib/queries/translation';
import { t } from '@/lib/i18n';
import { useMountEffect } from '@/lib/use-mount-effect';

const toastLife = {
  animationName: {
    '0%': { opacity: 0 },
    '4%': { opacity: 1 },
    '94%': { opacity: 1 },
    '100%': { opacity: 0 },
  },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

/** What the reader page shows: an article of a subscription, or a board entry (P29). */
export type ReaderDoc = {
  url: string;
  title: string;
  /** the title to show, the translation when there is one */
  displayTitle: string;
  /** the article kind ("post", "video") and the source type, to choose the view */
  kind?: string;
  sourceType?: string | null;
  sourceName?: string | null;
  author?: string | null;
  publishedAt?: string | null;
  imageUrl?: string | null;
  /** the server's article body, when it has one (reader mode without extraction) */
  bodyHtml?: string | null;
  summary?: string | null;
  saved: boolean;
  /** HN: the comments page, a tool switches to it */
  discussionUrl?: string | null;
};

/** Translation requests are going through (not stopped by a pause or an outage). */
function working(status: SchedulerStatus | undefined): boolean {
  return !!status && status.working > 0 && status.blocked === null;
}

export function Reader({ doc, onToggleSave }: { doc: ReaderDoc; onToggleSave: () => Promise<string | null> }) {
  const url = doc.url;
  const kind = readerKind({ url, kind: doc.kind, source: doc.sourceType ? { type: doc.sourceType } : null });
  const readable = kind.kind === 'web' && hasFullBody(doc.bodyHtml);
  const discussion = safeHttpUrl(doc.discussionUrl);
  const [state, dispatch] = useReducer(readerPageReducer, undefined, () =>
    initialReaderPage(kind.kind === 'youtube' ? 'player' : 'page', url),
  );
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);
  // The page (original page or YouTube player) and, over it, the reader-mode document.
  const web = useRef<WebViewHandle | null>(null);
  const readerWeb = useRef<WebViewHandle | null>(null);
  // The reader document follows the app's light / dark theme (P20). It is built with the theme of
  // the moment reader mode opens; a later change is applied in place, since a new document would
  // reload the page (scroll and translations lost).
  const scheme = useColorScheme() === 'dark' ? 'dark' : 'light';
  const [theme, setTheme] = useState<'light' | 'dark'>(scheme);
  useMountEffect(() => {
    const listener = Appearance.addChangeListener(({ colorScheme }) => {
      const next = JSON.stringify(colorScheme === 'dark' ? 'dark' : 'light');
      // Only the app's own reader document (marked), never a site's page.
      readerWeb.current?.injectJavaScript(
        `if(document.querySelector('meta[name="reader-document"]'))document.documentElement.setAttribute('data-theme',${next});true;`,
      );
    });
    return () => listener.remove();
  });
  // One translation host per document: the page's, and reader mode's while it is shown.
  const [pageHost, setPageHost] = useState<WebTranslationHost | null>(null);
  const [readerHost, setReaderHost] = useState<WebTranslationHost | null>(null);
  const [mode, setMode] = useState<TranslationMode>('original');
  const [pageTranslation, setPageTranslation] = useState<HostState | null>(null);
  const [readerTranslation, setReaderTranslation] = useState<HostState | null>(null);
  const [extracting, setExtracting] = useState(false);
  const { view } = state;
  const host = view === 'reader' ? readerHost : pageHost;
  const translation = view === 'reader' ? readerTranslation : pageTranslation;
  // The player has its own caption translation; every other view is a page the runtime translates.
  const translatable = view !== 'player';
  // State for the tools and handlers; the ref for the caption list's mount-time callback.
  const [captionsHost, setCaptionHost] = useState<CaptionHost | null>(null);
  const captionHost = useRef<CaptionHost | null>(null);
  const captionList = useRef<CaptionListHandle | null>(null);
  const [playerMode, setPlayerMode] = useState<TranslationMode>('original');
  const [timelineMode, setTimelineMode] = useState<TranslationMode>('original');
  const [timeline, setTimeline] = useState<CaptionTimeline>(EMPTY_TIMELINE);
  const [captions, setCaptions] = useState<CaptionState | null>(null);

  function notify(message: string) {
    setNotice((current) => ({ id: (current?.id ?? 0) + 1, message }));
  }

  // Translation switched off in 我的: the tools here do not turn it on, they say where to (old app).
  // Read at the moment it is asked (the in-page caption buttons keep the callback they started with).
  const preferenceQuery = translationPreferenceQuery(useIdentity());
  useQuery(preferenceQuery);
  function mayTranslate(next: TranslationMode): boolean {
    if (next === 'original' || queryClient.getQueryData(preferenceQuery.queryKey)?.enabled !== false) return true;
    notify(t().reader.translationOff);
    return false;
  }
  /** The player's own captions button (in the page) asks; refused, the page shows the original again. */
  function requestPlayerMode(requested: TranslationMode) {
    const next = mayTranslate(requested) ? requested : 'original';
    setPlayerMode(next);
    captionHost.current?.setMode(next);
  }

  // Android back: inside the page first (its history, or from a link back to reader mode), then leave.
  const inside = backStaysInside(state);
  useFocusEffect(
    useCallback(() => {
      const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
        if (inside === 'history') web.current?.goBack();
        else if (inside === 'origin') dispatch({ type: 'back' });
        return inside !== null;
      });
      return () => subscription.remove();
    }, [inside]),
  );

  async function toggleSave() {
    const problem = await onToggleSave();
    if (problem) notify(problem);
  }

  function toggleTranslation() {
    const next = mode === 'original' ? 'bilingual' : 'original';
    if (!mayTranslate(next)) return;
    setMode(next);
    // Both documents follow it: reader mode and the page underneath.
    pageHost?.setMode(next);
    readerHost?.setMode(next);
  }

  function toggleTimelineTranslation() {
    const next = timelineMode === 'original' ? 'bilingual' : 'original';
    if (!mayTranslate(next)) return;
    setTimelineMode(next);
    captionsHost?.setTimelineMode(next);
  }

  /** Reader mode: the server's full body, or else the article the open page gives (old app). */
  async function toggleReader() {
    if (view === 'reader') return dispatch({ type: 'switch', view: 'page' });
    // Tapped again while the page is asked for its article: stop asking (old app).
    if (extracting) return pageHost?.cancelExtract();
    setTheme(scheme);
    if (readable) return dispatch({ type: 'switch', view: 'reader' });
    if (!pageHost) return;
    setExtracting(true);
    const result = await pageHost.extract();
    setExtracting(false);
    if (result.ok) dispatch({ type: 'show-extracted', article: result.article });
    else if (result.reason === 'too_large') notify(t().reader.tooLong);
    else if (result.reason !== 'changed') notify(t().reader.noArticle);
  }

  /**
   * Every navigation passes here (originWhitelist '*' keeps react-native-webview from handing
   * unknown schemes to the system). Only http(s) loads; a link tapped in reader mode opens as the
   * original page at that address.
   */
  function allowPageNavigation(request: ShouldStartLoadRequest): boolean {
    return safeHttpUrl(request.url) !== null || request.url === 'about:blank';
  }
  function allowReaderNavigation(request: ShouldStartLoadRequest): boolean {
    const target = safeHttpUrl(request.url);
    if (target && request.isTopFrame !== false) dispatch({ type: 'open-page', url: target });
    return false;
  }

  const tools: ReaderTool[] = [];
  if (translatable) {
    tools.push({
      key: 'translate',
      icon: IconLanguage,
      label: t().reader.bilingual,
      on: mode === 'bilingual',
      busy: mode === 'bilingual' && working(translation?.scheduler),
      onPress: toggleTranslation,
    });
  } else {
    tools.push({
      key: 'translate',
      icon: IconLanguage,
      label: t().reader.translateCaptions,
      on: timelineMode === 'bilingual',
      busy: timelineMode === 'bilingual' && working(captions?.scheduler),
      onPress: () => toggleTimelineTranslation(),
    });
  }
  // Reader mode needs a body: the server's, or the page's once its runtime answers.
  if (kind.kind === 'web' && (readable || view === 'reader' || translation?.verified)) {
    tools.push({
      key: 'reader',
      icon: IconArticle,
      label: t().reader.readerMode,
      on: view === 'reader' || extracting,
      busy: extracting,
      onPress: () => void toggleReader(),
    });
  }
  if (discussion) {
    const onDiscussion = view === 'page' && state.pageUrl === discussion;
    tools.push({
      key: 'discussion',
      icon: IconMessages,
      label: onDiscussion ? t().reader.backToArticle : t().reader.discussion,
      on: onDiscussion,
      onPress: () => dispatch(onDiscussion ? { type: 'back' } : { type: 'open-page', url: discussion }),
    });
  }
  if (kind.kind === 'youtube') {
    tools.push({
      key: 'player',
      icon: view === 'player' ? IconWorld : IconBrandYoutube,
      label: view === 'player' ? t().reader.openVideoPage : t().reader.backToPlayer,
      on: false,
      onPress: () => dispatch({ type: 'switch', view: view === 'player' ? 'page' : 'player' }),
    });
  }

  const extracted = view === 'reader' ? state.extracted : null;
  const readerSource =
    view !== 'reader'
      ? null
      : extracted
        ? {
            html: readerDocument({
              url: extracted.url,
              title: extracted.title || doc.title,
              author: extracted.byline,
              sourceName: doc.sourceName,
              publishedAt: doc.publishedAt,
              bodyHtml: '',
              lang: extracted.lang,
              theme,
            }),
            baseUrl: extracted.url,
          }
        : {
            html: readerDocument({
              url,
              title: doc.displayTitle,
              author: doc.author,
              sourceName: doc.sourceName,
              publishedAt: doc.publishedAt,
              imageUrl: safeHttpUrl(doc.imageUrl),
              bodyHtml: doc.bodyHtml ?? '',
              theme,
            }),
            baseUrl: url,
          };
  const pageSource =
    view === 'player' && kind.kind === 'youtube'
      ? { uri: youtubeEmbedUrl(kind.videoId), headers: { Referer: YOUTUBE_APP_REFERER } }
      : { uri: kind.kind === 'youtube' && state.pageUrl === url ? youtubeWatchUrl(kind.videoId) : state.pageUrl };

  const common = {
    allowsFullscreenVideo: true,
    allowsInlineMediaPlayback: true,
    domStorageEnabled: true,
    mediaPlaybackRequiresUserAction: true,
    originWhitelist: ['*'],
    setSupportMultipleWindows: false,
    style: { flex: 1, backgroundColor: 'transparent' },
  };
  // The page's load drives the progress line and problems; reader mode is the app's own document.
  const pageEvents = {
    onError: (event: WebViewErrorEvent) =>
      dispatch({ type: 'failed', message: t().reader.pageFailed(event.nativeEvent.description || t().reader.networkProblem) }),
    onHttpError: (event: WebViewHttpErrorEvent) => {
      if (event.nativeEvent.statusCode >= 500)
        dispatch({ type: 'failed', message: t().reader.siteError(event.nativeEvent.statusCode) });
    },
    onLoadEnd: () => dispatch({ type: 'load-end' }),
    onLoadProgress: (event: WebViewProgressEvent) => dispatch({ type: 'progress', progress: event.nativeEvent.progress }),
    onLoadStart: () => dispatch({ type: 'load-start' }),
    onNavigationStateChange: (navigation: WebViewNavigation) =>
      dispatch({ type: 'history', canGoBack: navigation.canGoBack, url: safeHttpUrl(navigation.url) ?? undefined }),
    onShouldStartLoadWithRequest: allowPageNavigation,
  };
  // A new page address is a new document: remount rather than navigate (react-guidelines §2).
  const pageKey = documentKey(state);
  const page = !translatable ? (
    <CaptionPlayer
      {...common}
      {...pageEvents}
      key={pageKey}
      mode={playerMode}
      onCaptionState={setCaptions}
      onHost={(current) => {
        captionHost.current = current;
        setCaptionHost(current);
      }}
      onModeRequest={requestPlayerMode}
      onTimeline={(next) => {
        setTimeline(next);
        captionList.current?.follow(next);
      }}
      source={pageSource}
      timelineMode={timelineMode}
      webRef={(handle) => {
        web.current = handle;
      }}
    />
  ) : (
    <TranslatedWebView
      {...common}
      {...pageEvents}
      key={pageKey}
      mode={mode}
      onHost={setPageHost}
      onTranslationState={setPageTranslation}
      source={pageSource}
      captions={
        kind.kind === 'youtube'
          ? {
              mode: playerMode,
              onModeRequest: requestPlayerMode,
              onHost: (current) => {
                captionHost.current = current;
                setCaptionHost(current);
              },
            }
          : undefined
      }
      webRef={(handle) => {
        web.current = handle;
      }}
    />
  );
  // Reader mode lies over the page, which stays loaded underneath (back from it is where you were).
  const readerLayer = readerSource ? (
    <View className="absolute inset-0 bg-canvas">
      <TranslatedWebView
        {...common}
        key={readerKey(state)}
        mode={mode}
        onHost={setReaderHost}
        onShouldStartLoadWithRequest={allowReaderNavigation}
        onTranslationState={setReaderTranslation}
        profile="reader"
        readerContent={extracted ? { html: extracted.html, url: extracted.url } : undefined}
        source={readerSource}
        webRef={(handle) => {
          readerWeb.current = handle;
        }}
      />
    </View>
  ) : null;
  const translationIssue = !translatable
    ? captions && captionProblem(captions, timelineMode === 'bilingual' || playerMode === 'bilingual')
    : translation && translationProblem(translation, mode);

  return (
    <ArticleView
      onBack={() => router.back()}
      // The page as it is now (links followed), or the article reader mode shows.
      onOpenExternal={() => void Linking.openURL(shareUrl(state)).catch(() => undefined)}
      onShare={() => {
        const address = shareUrl(state);
        // The article's title only goes with the article; a page reached by a link is shared as its address.
        const message = address === state.articleUrl ? `${doc.title}\n${address}` : address;
        void Share.share({ message, url: address }).catch(() => undefined);
      }}
      onToggleSave={() => void toggleSave()}
      problem={
        // A failure of the page underneath reader mode is not reader mode's problem.
        state.load.status === 'failed' && view !== 'reader'
          ? {
              message: state.load.message,
              action: (
                <Button
                  onPress={() => {
                    dispatch({ type: 'retry' });
                    web.current?.reload();
                  }}
                  title={t().common.retry}
                  variant="ghost"
                />
              ),
            }
          : translationIssue
            ? {
                message: translationIssue.message,
                action: translationIssue.retry ? (
                  <Button
                    onPress={() => (translatable ? host?.retry() : captionHost.current?.retry())}
                    title={t().common.retry}
                    variant="ghost"
                  />
                ) : undefined,
              }
            : null
      }
      progress={view !== 'reader' && state.load.status === 'loading' ? state.load.progress : 1}
      saved={doc.saved}
      title={kind.kind === 'youtube' ? t().reader.video : kind.kind === 'social' ? t().reader.post : t().reader.article}
      tools={tools}
    >
      {view === 'player' ? (
        // The player keeps 16:9 at the top; below it the title and the 字幕轴 (old app).
        <View className="flex-1">
          <View className="aspect-video w-full bg-black">{page}</View>
          <CaptionList
            empty={captions?.captions === 'unavailable' ? t().reader.noCaptions : t().reader.captionsLater}
            header={
              <View className="gap-1 px-3 pb-3 pt-5">
                <Text level="card">{doc.displayTitle}</Text>
                <Text level="desc" tone="secondary">
                  {[doc.sourceName, doc.publishedAt ? articleDate(doc.publishedAt) : null].filter(Boolean).join(' · ')}
                </Text>
                <View className="mt-5 gap-0.5">
                  <Text level="section">{t().reader.captions}</Text>
                  <Text level="caption" tone="tertiary">
                    {t().reader.captionsHint}
                  </Text>
                </View>
              </View>
            }
            ref={captionList}
            onMoveWindow={(direction, firstId) => captionHost.current?.moveWindow(direction, firstId)}
            onSeek={(caption) => captionHost.current?.seekTo(caption.startMs)}
            onVisible={(ids) => captionHost.current?.setVisible(ids)}
            timeline={timeline}
            translated={timelineMode === 'bilingual'}
          />
        </View>
      ) : (
        <View className="flex-1">
          {page}
          {readerLayer}
        </View>
      )}
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="none" style={toastLife}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
    </ArticleView>
  );
}
