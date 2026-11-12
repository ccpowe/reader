/**
 * Reader page — assembly (react-guidelines §6): reads the article, chooses the view by kind
 * (src/lib/reader.ts), and drives one WebView: the original page, reader mode, or the YouTube
 * player. Translation joins in block 3b / 3c.
 */
import { IconArticle, IconBrandYoutube, IconWorld } from '@tabler/icons-react-native';
import { useQuery } from '@tanstack/react-query';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { useCallback, useReducer, useRef, useState } from 'react';
import { BackHandler, Linking, ScrollView, Share, View } from 'react-native';
import Animated from 'react-native-reanimated';
import type {
  ShouldStartLoadRequest,
  WebViewErrorEvent,
  WebViewHttpErrorEvent,
  WebViewNavigation,
  WebViewProgressEvent,
} from 'react-native-webview/lib/WebViewTypes';

import type { ArticleDetail } from '@/lib/api/schema.gen';
import { WebView, type WebViewHandle } from '@/lib/webview';
import { useIdentity } from '@/lib/app-session';
import { backStaysInside, documentKey, initialReaderPage, readerPageReducer } from '@/lib/reader-page';
import { articleQuery } from '@/lib/queries/article';
import { setSaved, UnknownOutcome } from '@/lib/queries/today';
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
import { errorMessage } from '@/lib/user-error';
import { ArticleView, type ReaderTool } from '@/screens/ArticleView';
import { Button, EmptyState, Skeleton, Text, Toast } from '@/ui';
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

function Reader({ identity, article, url }: { identity: string; article: ArticleDetail; url: string }) {
  const kind = readerKind(article);
  const readable = kind.kind === 'web' && hasFullBody(article.body_html);
  const [state, dispatch] = useReducer(readerPageReducer, undefined, () =>
    initialReaderPage(kind.kind === 'youtube' ? 'player' : 'page', url),
  );
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);
  const web = useRef<WebViewHandle>(null);
  const { view } = state;

  // Android back: inside the page first (its history, or from a link back to reader mode), then leave.
  const inside = backStaysInside(state);
  useFocusEffect(
    useCallback(() => {
      const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
        if (inside === 'history') web.current?.goBack();
        else if (inside === 'reader') dispatch({ type: 'back' });
        return inside !== null;
      });
      return () => subscription.remove();
    }, [inside]),
  );

  async function toggleSave() {
    try {
      await setSaved(identity, article.id, !article.is_saved);
    } catch (error) {
      const message =
        error instanceof UnknownOutcome
          ? '没收到服务器的确认，已重新加载收藏状态。'
          : `${article.is_saved ? '取消收藏' : '收藏'}没有成功：${errorMessage(error)}`;
      setNotice((current) => ({ id: (current?.id ?? 0) + 1, message }));
    }
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
  if (readable) {
    tools.push({
      key: 'reader',
      icon: IconArticle,
      label: '阅读模式',
      on: view === 'reader',
      onPress: () => dispatch({ type: 'switch', view: view === 'reader' ? 'page' : 'reader' }),
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

  const source =
    view === 'reader'
      ? {
          html: readerDocument({
            url,
            title: article.title_translation?.text ?? article.title,
            author: article.author,
            sourceName: article.source?.name,
            publishedAt: article.published_at ?? article.first_seen_at,
            imageUrl: safeHttpUrl(article.image_url),
            bodyHtml: article.body_html ?? '',
          }),
          baseUrl: url,
        }
      : view === 'player' && kind.kind === 'youtube'
        ? { uri: youtubeEmbedUrl(kind.videoId), headers: { Referer: YOUTUBE_APP_REFERER } }
        : { uri: kind.kind === 'youtube' && state.pageUrl === url ? youtubeWatchUrl(kind.videoId) : state.pageUrl };

  const page = (
    <WebView
      allowsFullscreenVideo
      allowsInlineMediaPlayback
      domStorageEnabled
      javaScriptEnabled={view !== 'reader'}
      // A new view or address is a new document: remount rather than navigate (react-guidelines §2).
      key={documentKey(state)}
      mediaPlaybackRequiresUserAction
      onError={(event: WebViewErrorEvent) =>
        dispatch({ type: 'failed', message: `页面没打开：${event.nativeEvent.description || '网络问题'}` })
      }
      onHttpError={(event: WebViewHttpErrorEvent) => {
        if (event.nativeEvent.statusCode >= 500)
          dispatch({ type: 'failed', message: `网站出错了（${event.nativeEvent.statusCode}）。` });
      }}
      onLoadEnd={() => dispatch({ type: 'load-end' })}
      onLoadProgress={(event: WebViewProgressEvent) => dispatch({ type: 'progress', progress: event.nativeEvent.progress })}
      onLoadStart={() => dispatch({ type: 'load-start' })}
      onNavigationStateChange={(navigation: WebViewNavigation) => dispatch({ type: 'history', canGoBack: navigation.canGoBack })}
      onShouldStartLoadWithRequest={allowNavigation}
      originWhitelist={['*']}
      ref={web}
      setSupportMultipleWindows={false}
      source={source}
      style={{ flex: 1, backgroundColor: 'transparent' }}
    />
  );

  return (
    <ArticleView
      onBack={() => router.back()}
      onOpenExternal={() => void Linking.openURL(url).catch(() => undefined)}
      onShare={() => void Share.share({ message: `${article.title}\n${url}`, url }).catch(() => undefined)}
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
          : null
      }
      progress={state.load.status === 'loading' ? state.load.progress : 1}
      saved={article.is_saved}
      title={kind.kind === 'youtube' ? '视频' : kind.kind === 'social' ? '帖子' : '文章'}
      tools={tools}
    >
      {view === 'player' ? (
        // The player keeps 16:9 at the top; below it the video's title and description (the
        // translated caption list joins here in block 3c, as in the old app).
        <View className="flex-1">
          <View className="aspect-video w-full bg-sunken">{page}</View>
          <ScrollView contentContainerClassName="gap-2 px-6 py-6">
            <Text level="card">{article.title_translation?.text ?? article.title}</Text>
            <Text level="desc" tone="secondary">
              {[article.source?.name, articleDate(article.published_at ?? article.first_seen_at)].filter(Boolean).join(' · ')}
            </Text>
            {article.summary ? (
              <Text className="mt-2" level="read" tone="secondary">
                {article.summary}
              </Text>
            ) : null}
          </ScrollView>
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

export default function ArticleRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const identity = useIdentity();
  const article = useQuery(articleQuery(identity, id));
  if (article.data) {
    const url = safeHttpUrl(article.data.url);
    if (url) return <Reader article={article.data} identity={identity} key={id} url={url} />;
    return (
      <ArticleView onBack={() => router.back()} saved={article.data.is_saved}>
        <EmptyState description="它的地址不是网页地址，为了安全不打开。" title="这条内容无法打开" />
      </ArticleView>
    );
  }
  return (
    <ArticleView onBack={() => router.back()} saved={false}>
      {article.isError ? (
        <EmptyState
          action={<Button onPress={() => void article.refetch()} title="重试" />}
          description={errorMessage(article.error)}
          title="没能打开"
        />
      ) : (
        <View className="gap-4 px-6 py-6">
          <Skeleton lines={2} />
          <Skeleton lines={6} />
        </View>
      )}
    </ArticleView>
  );
}
