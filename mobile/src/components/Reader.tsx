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
import { useCallback, useReducer, useRef, useState } from 'react';
import { BackHandler, Linking, Share, View } from 'react-native';
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
import { WebView, type WebViewHandle } from '@/lib/webview';
import { backStaysInside, documentKey, initialReaderPage, readerPageReducer } from '@/lib/reader-page';
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

export function Reader({ doc, onToggleSave }: { doc: ReaderDoc; onToggleSave: () => Promise<string | null> }) {
  const url = doc.url;
  const kind = readerKind({ url, kind: doc.kind, source: doc.sourceType ? { type: doc.sourceType } : null });
  const readable = kind.kind === 'web' && hasFullBody(doc.bodyHtml);
  const discussion = safeHttpUrl(doc.discussionUrl);
  const [state, dispatch] = useReducer(readerPageReducer, undefined, () =>
    initialReaderPage(kind.kind === 'youtube' ? 'player' : 'page', url),
  );
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);
  const web = useRef<WebViewHandle | null>(null);
  const [host, setHost] = useState<WebTranslationHost | null>(null);
  const [mode, setMode] = useState<TranslationMode>('original');
  const [translation, setTranslation] = useState<HostState | null>(null);
  const [extracting, setExtracting] = useState(false);
  const { view } = state;
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
    setMode(next);
    host?.setMode(next);
  }

  function toggleTimelineTranslation() {
    const next = timelineMode === 'original' ? 'bilingual' : 'original';
    setTimelineMode(next);
    captionsHost?.setTimelineMode(next);
  }

  /** Reader mode: the server's full body, or else the article the open page gives (old app). */
  async function toggleReader() {
    if (view === 'reader') return dispatch({ type: 'switch', view: 'page' });
    if (readable) return dispatch({ type: 'switch', view: 'reader' });
    if (!host || extracting) return;
    setExtracting(true);
    const result = await host.extract();
    setExtracting(false);
    if (result.ok) dispatch({ type: 'show-extracted', article: result.article });
    else if (result.reason === 'too_large') notify('这篇正文太长，阅读模式打不开。');
    else if (result.reason !== 'changed') notify('没能从这个网页里找到正文。');
  }

  /**
   * Every navigation passes here (originWhitelist '*' keeps react-native-webview from handing
   * unknown schemes to the system). Only http(s) loads; a link tapped in reader mode opens as the
   * original page at that address.
   */
  function allowNavigation(request: ShouldStartLoadRequest): boolean {
    const target = safeHttpUrl(request.url);
    if (view === 'reader') {
      if (target && request.isTopFrame !== false) dispatch({ type: 'open-page', url: target });
      return false;
    }
    return target !== null || request.url === 'about:blank';
  }

  const tools: ReaderTool[] = [];
  if (translatable) {
    tools.push({ key: 'translate', icon: IconLanguage, label: '双语对照', on: mode === 'bilingual', onPress: toggleTranslation });
  } else {
    tools.push({
      key: 'translate',
      icon: IconLanguage,
      label: '翻译字幕轴',
      on: timelineMode === 'bilingual',
      onPress: () => toggleTimelineTranslation(),
    });
  }
  // Reader mode needs a body: the server's, or the page's once its runtime answers.
  if (kind.kind === 'web' && (readable || view === 'reader' || translation?.verified)) {
    tools.push({
      key: 'reader',
      icon: IconArticle,
      label: '阅读模式',
      on: view === 'reader' || extracting,
      onPress: () => void toggleReader(),
    });
  }
  if (discussion) {
    const onDiscussion = view === 'page' && state.pageUrl === discussion;
    tools.push({
      key: 'discussion',
      icon: IconMessages,
      label: onDiscussion ? '回到原文' : '讨论区',
      on: onDiscussion,
      onPress: () => dispatch(onDiscussion ? { type: 'back' } : { type: 'open-page', url: discussion }),
    });
  }
  if (kind.kind === 'youtube') {
    tools.push({
      key: 'player',
      icon: view === 'player' ? IconWorld : IconBrandYoutube,
      label: view === 'player' ? '打开视频网页' : '回到播放器',
      on: false,
      onPress: () => dispatch({ type: 'switch', view: view === 'player' ? 'page' : 'player' }),
    });
  }

  const extracted = view === 'reader' ? state.extracted : null;
  const source =
    view === 'reader' && extracted
      ? {
          html: readerDocument({
            url: extracted.url,
            title: extracted.title || doc.title,
            author: extracted.byline,
            sourceName: doc.sourceName,
            publishedAt: doc.publishedAt,
            bodyHtml: '',
            lang: extracted.lang,
          }),
          baseUrl: extracted.url,
        }
      : view === 'reader'
        ? {
            html: readerDocument({
              url,
              title: doc.displayTitle,
              author: doc.author,
              sourceName: doc.sourceName,
              publishedAt: doc.publishedAt,
              imageUrl: safeHttpUrl(doc.imageUrl),
              bodyHtml: doc.bodyHtml ?? '',
            }),
            baseUrl: url,
          }
        : view === 'player' && kind.kind === 'youtube'
          ? { uri: youtubeEmbedUrl(kind.videoId), headers: { Referer: YOUTUBE_APP_REFERER } }
          : { uri: kind.kind === 'youtube' && state.pageUrl === url ? youtubeWatchUrl(kind.videoId) : state.pageUrl };

  const webProps = {
    allowsFullscreenVideo: true,
    allowsInlineMediaPlayback: true,
    domStorageEnabled: true,
    // A new view or address is a new document: remount rather than navigate (react-guidelines §2).
    key: documentKey(state),
    mediaPlaybackRequiresUserAction: true,
    onError: (event: WebViewErrorEvent) =>
      dispatch({ type: 'failed', message: `页面没打开：${event.nativeEvent.description || '网络问题'}` }),
    onHttpError: (event: WebViewHttpErrorEvent) => {
      if (event.nativeEvent.statusCode >= 500)
        dispatch({ type: 'failed', message: `网站出错了（${event.nativeEvent.statusCode}）。` });
    },
    onLoadEnd: () => dispatch({ type: 'load-end' }),
    onLoadProgress: (event: WebViewProgressEvent) => dispatch({ type: 'progress', progress: event.nativeEvent.progress }),
    onLoadStart: () => dispatch({ type: 'load-start' }),
    onNavigationStateChange: (navigation: WebViewNavigation) => dispatch({ type: 'history', canGoBack: navigation.canGoBack }),
    onShouldStartLoadWithRequest: allowNavigation,
    originWhitelist: ['*'],
    setSupportMultipleWindows: false,
    source,
    style: { flex: 1, backgroundColor: 'transparent' },
  };
  const { key: documentId, ...shared } = webProps;
  const page = !translatable ? (
    <CaptionPlayer
      {...shared}
      key={documentId}
      mode={playerMode}
      onCaptionState={setCaptions}
      onHost={(current) => {
        captionHost.current = current;
        setCaptionHost(current);
      }}
      onModeRequest={(requested) => {
        setPlayerMode(requested);
        captionHost.current?.setMode(requested);
      }}
      onTimeline={(next) => {
        setTimeline(next);
        captionList.current?.follow(next);
      }}
      timelineMode={timelineMode}
      webRef={(handle) => {
        web.current = handle;
      }}
    />
  ) : translatable ? (
    <TranslatedWebView
      {...shared}
      key={documentId}
      mode={mode}
      onHost={setHost}
      onTranslationState={setTranslation}
      profile={view === 'reader' ? 'reader' : undefined}
      readerContent={extracted ? { html: extracted.html, url: extracted.url } : undefined}
      captions={
        kind.kind === 'youtube'
          ? {
              mode: playerMode,
              onModeRequest: (requested) => {
                setPlayerMode(requested);
                captionHost.current?.setMode(requested);
              },
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
  ) : (
    <WebView {...shared} javaScriptEnabled key={documentId} ref={web} />
  );
  const translationIssue = !translatable
    ? captions && captionProblem(captions, timelineMode === 'bilingual' || playerMode === 'bilingual')
    : translation && translationProblem(translation, mode);

  return (
    <ArticleView
      onBack={() => router.back()}
      onOpenExternal={() => void Linking.openURL(url).catch(() => undefined)}
      onShare={() => void Share.share({ message: `${doc.title}\n${url}`, url }).catch(() => undefined)}
      onToggleSave={() => void toggleSave()}
      problem={
        state.load.status === 'failed'
          ? {
              message: state.load.message,
              action: (
                <Button
                  onPress={() => {
                    dispatch({ type: 'retry' });
                    web.current?.reload();
                  }}
                  title="重试"
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
                    title="重试"
                    variant="ghost"
                  />
                ) : undefined,
              }
            : null
      }
      progress={state.load.status === 'loading' ? state.load.progress : 1}
      saved={doc.saved}
      title={kind.kind === 'youtube' ? '视频' : kind.kind === 'social' ? '帖子' : '文章'}
      tools={tools}
    >
      {view === 'player' ? (
        // The player keeps 16:9 at the top; below it the title and the 字幕轴 (old app).
        <View className="flex-1">
          <View className="aspect-video w-full bg-black">{page}</View>
          <CaptionList
            empty={captions?.captions === 'unavailable' ? '没找到字幕，这个视频可能没有字幕。' : '字幕出现后会显示在这里。'}
            header={
              <View className="gap-1 px-3 pb-3 pt-5">
                <Text level="card">{doc.displayTitle}</Text>
                <Text level="desc" tone="secondary">
                  {[doc.sourceName, doc.publishedAt ? articleDate(doc.publishedAt) : null].filter(Boolean).join(' · ')}
                </Text>
                <View className="mt-5 gap-0.5">
                  <Text level="section">字幕轴</Text>
                  <Text level="caption" tone="tertiary">
                    点击句子，跳转到这一刻
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
        page
      )}
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="none" style={toastLife}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
    </ArticleView>
  );
}
