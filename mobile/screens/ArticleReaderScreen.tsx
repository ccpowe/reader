import { i18n, useTranslation } from '../i18n';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Pressable,
  Share,
  StyleSheet,
  Text,
  View,
  type ViewToken,
} from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';
import { DetailHeader } from '../components/DetailHeader';
import { FloatingReaderTools } from '../components/FloatingReaderTools';

import {
  TranslatableWebView,
  type SynchronizedCaption,
  type SynchronizedCaptionTimeline,
  type TranslatableWebViewState,
} from '../components/TranslatableWebView';
import { TitleTranslationNotice } from '../components/TitleTranslationNotice';
import {
  YOUTUBE_APP_REFERER,
  youtubeEmbedUrl,
  youtubeVideoId,
  youtubeWatchUrl,
} from '../domain/media';
import { type TranslationDisplayMode } from '../domain/webTranslation';
import { buildExtractedReaderHtml, hasReadableBody } from '../domain/readerHtml';
import type { WebDocumentIdentity, WebReaderFailure, WebReaderResult } from '../domain/webReader';
import { useTitleTranslationConvergence } from '../hooks/useTitleTranslationConvergence';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import {
  displayTitle,
  getArticle,
  setSavedContent,
  type Article,
  type FeedItem,
} from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime } from '../lib/connection/react';
import { beginSavedMutation } from '../state/savedMutation';
import { invalidateAfterSavedMutation } from '../state/invalidation';
import { colors, radii, touchTarget } from '../ui/tokens';

const INITIAL_TRANSLATION_STATE: TranslatableWebViewState = {
  activeCount: 0,
  bridgeState: 'initializing',
  discoveredCount: 0,
  error: null,
};

const EMPTY_CAPTION_TIMELINE: SynchronizedCaptionTimeline = {
  captions: [],
  currentSegmentId: null,
};

type ArticleReaderScreenProps = {
  item: FeedItem;
  onBack: () => void;
  renderReaderHtml: (article: Article, item: FeedItem) => string;
  session: Session;
};

export function ArticleReaderScreen({
  item,
  onBack,
  renderReaderHtml,
  session,
}: ArticleReaderScreenProps) {
  const { t } = useTranslation('reader');
  const isVideo = item.source_kind === 'youtube';
  const isReddit = item.source_kind === 'reddit';
  const isX = item.source_kind === 'x';
  const isWebArticle = item.source_kind === 'web' || item.source_kind === 'rss';
  const videoId = isVideo ? youtubeVideoId(item.external_url) : null;
  const [bodySize, setBodySize] = useState({ width: 0, height: 0 });
  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(!isVideo && !isReddit && !isWebArticle);
  const [mode, setMode] = useState<'reader' | 'web'>(isReddit || isX || isWebArticle ? 'web' : 'reader');
  const modeChosenRef = useRef(false);
  const [youtubeMode, setYoutubeMode] = useState<'player' | 'web'>('player');
  const [translationMode, setTranslationMode] = useState<TranslationDisplayMode>('original');
  const [timelineTranslationMode, setTimelineTranslationMode] = useState<TranslationDisplayMode>('original');
  const [playerTranslationMode, setPlayerTranslationMode] = useState<TranslationDisplayMode>('original');
  const [saveBusy, setSaveBusy] = useState(false);
  const saveBusyRef = useRef(false);
  const [readerTranslationState, setTranslationState] = useState(INITIAL_TRANSLATION_STATE);
  const [webTranslationState, setWebTranslationState] = useState(INITIAL_TRANSLATION_STATE);
  const [webNavigationCommand, setWebNavigationCommand] = useState<{ url: string; sequence: number } | null>(null);
  const [currentWebUrl, setCurrentWebUrl] = useState(item.external_url);
  const webDocumentRef = useRef<WebDocumentIdentity | null>(null);
  const originalWebDocumentRef = useRef<WebDocumentIdentity | null>(null);
  const [isOriginalWebDocument, setIsOriginalWebDocument] = useState(false);
  const [extractedReader, setExtractedReader] = useState<Extract<WebReaderResult, { ok: true }> | null>(null);
  const extractedReaderRef = useRef(extractedReader);
  extractedReaderRef.current = extractedReader;
  const cancelExtractionRef = useRef<(() => void) | null>(null);
  const extractionGenerationRef = useRef(0);
  const [extracting, setExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState<WebReaderFailure | null>(null);
  const [captionTimeline, setCaptionTimeline] = useState(EMPTY_CAPTION_TIMELINE);
  const [visibleTimelineSegmentIds, setVisibleTimelineSegmentIds] = useState<string[]>([]);
  const timelineViewability = useRef({ itemVisiblePercentThreshold: 10 });
  const onTimelineViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken<SynchronizedCaption>[] }) => {
    const next = viewableItems.filter(token => token.isViewable).map(token => token.item.segmentId).slice(0, 20);
    setVisibleTimelineSegmentIds(current => current.join('|') === next.join('|') ? current : next);
  });
  const [seekCommand, setSeekCommand] = useState<{ positionMs: number; sequence: number } | null>(null);
  const [timelineWindowCommand, setTimelineWindowCommand] = useState<{ direction: 'previous' | 'next'; firstId: string; sequence: number } | null>(null);
  const timelineListRef = useRef<FlatList<SynchronizedCaption>>(null);
  const timelineTouchStartY = useRef<number | null>(null);
  const timelineContentHeight = useRef(0);
  const timelineViewportHeight = useRef(0);
  const timelineDragging = useRef(false);
  const timelineDragDirection = useRef<'previous' | 'next' | null>(null);
  const timelineScrollOffset = useRef(0);
  const timelinePositionPending = useRef(false);
  const requestTimelineWindow = (direction: 'previous' | 'next') => {
    if (timelineDragDirection.current !== direction) return;
    if (direction === 'previous' ? !captionTimeline.hasPrevious : !captionTimeline.hasNext) return;
    const firstId = captionTimeline.captions[0]?.segmentId;
    if (!firstId) return;
    timelineDragDirection.current = null;
    timelinePositionPending.current = true;
    timelineDragging.current = false;
    timelineTouchStartY.current = null;
    setTimelineWindowCommand(current => ({ direction, firstId, sequence: (current?.sequence ?? 0) + 1 }));
  };
  const [message, setMessage] = useState<{ text: string } | { key: 'articleLoadFailed' } | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saved, setSaved] = useState(item.is_saved);
  const client = useQueryClient();
  const runtime = useReaderRuntime();
  const translation = useTranslationPreference(session, true);
  const titleTranslations = useTitleTranslationConvergence(
    session,
    [article ?? item],
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    `article:${item.content_id}`,
  );
  const liveTitle = titleTranslations.find(
    (candidate) => candidate.content_id === item.content_id,
  );
  const effectiveMode = isReddit ? 'web' : mode;
  const translationState = isWebArticle && effectiveMode === 'web' ? webTranslationState : readerTranslationState;
  const translationProfile = effectiveMode === 'reader' ? 'reader' : undefined;

  useEffect(() => {
    originalWebDocumentRef.current = null;
    setIsOriginalWebDocument(false);
  }, [item.content_id, item.external_url, session.user.id]);

  useEffect(() => {
    modeChosenRef.current = false;
    setMode(isReddit || isX || isWebArticle ? 'web' : 'reader');
    extractionGenerationRef.current++;
    cancelExtractionRef.current?.();
    cancelExtractionRef.current = null;
    webDocumentRef.current = null;
    setWebNavigationCommand(null);
    setCurrentWebUrl(item.external_url);
    extractedReaderRef.current = null;
    setExtractedReader(null);
    setExtracting(false);
    setExtractionError(null);
    // The keyed original WebView reports its own initial state before this
    // parent effect. Do not erase its extraction command in original mode.
    setYoutubeMode('player');
    setTranslationMode('original');
    setTimelineTranslationMode('original');
    setPlayerTranslationMode('original');
    setTranslationState(INITIAL_TRANSLATION_STATE);
    setCaptionTimeline(EMPTY_CAPTION_TIMELINE);
    setVisibleTimelineSegmentIds([]);
    setSeekCommand(null);
    setTimelineWindowCommand(null);
    timelineDragDirection.current = null;
    timelinePositionPending.current = false;
  }, [isReddit, isVideo, isX, isWebArticle, item.content_id, item.external_url]); // saved changes must not replace an open document

  useEffect(() => { setSaved(item.is_saved); }, [item.content_id, item.is_saved]);
  useEffect(() => () => {
    extractionGenerationRef.current++;
    cancelExtractionRef.current?.();
  }, []);

  useEffect(() => {
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    setArticle(null);
    setMessage(null);
    if (isVideo || isReddit || isWebArticle) {
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    void (async () => {
      try {
        const nextArticle = await getArticle(session, item.content_id, context.runtime);
        if (!cancelled && isRuntimeContextCurrent(context)) {
          setArticle(nextArticle);
          if (!isX && !modeChosenRef.current && !hasReadableBody(nextArticle)) setMode('web');
        }
      } catch (error) {
        if (!cancelled && isRuntimeContextCurrent(context)) {
          setMessage(error instanceof Error ? { text: error.message } : { key: 'articleLoadFailed' });
        }
      } finally {
        if (!cancelled && isRuntimeContextCurrent(context)) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isReddit, isVideo, isX, isWebArticle, item.content_id, loadAttempt, runtime, session]);

  useEffect(() => {
    if (!liveTitle) return;
    setArticle((current) => {
      if (!current) return current;
      if (
        current.translated_title === liveTitle.translated_title &&
        current.translation_locale === liveTitle.translation_locale &&
        current.translation_status === liveTitle.translation_status
      ) return current;
      return { ...current, ...liveTitle };
    });
  }, [liveTitle]);

  async function openBrowser(targetUrl = isWebArticle ? currentWebUrl : article?.external_url ?? item.external_url) {
    try {
      await Linking.openURL(targetUrl);
    } catch {
      Alert.alert(i18n.t('reader:externalOpenFailed'));
    }
  }

  async function toggleSaved() {
    if (saveBusyRef.current) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const release = beginSavedMutation(client, session.user.id, context.serverId, item.content_id);
    if (!release) return;
    let committed = false;
    saveBusyRef.current = true;
    setSaveBusy(true);
    const previousSaved = saved;
    const next = !previousSaved;
    setSaved(next);
    try {
      await setSavedContent(session, item.content_id, next, context.runtime);
      committed = true;
      if (!isRuntimeContextCurrent(context)) return;
      await invalidateAfterSavedMutation(client, session.user.id, context.serverId, context);
    } catch (error) {
      if (!isRuntimeContextCurrent(context)) return;
      if (committed) return;
      setSaved(previousSaved);
      Alert.alert(i18n.t('reader:saveFailed'), error instanceof Error ? error.message : i18n.t('reader:tryLater'));
    } finally {
      release();
      saveBusyRef.current = false;
      setSaveBusy(false);
    }
  }

  async function share() {
    const url = isWebArticle ? currentWebUrl : article?.external_url ?? item.external_url;
    try {
      await Share.share({
        message: url,
        title: isWebArticle ? extractedReader?.article.title ?? url : article ? displayTitle(article) : displayTitle(item),
        url,
      });
    } catch {
      Alert.alert(i18n.t('reader:shareFailed'));
    }
  }

  function requestTranslationMode(nextMode: TranslationDisplayMode) {
    if (nextMode !== 'original' && !translation.enabled) {
      Alert.alert(i18n.t('reader:translationDisabled'), t('enableTranslationInProfile'));
      return;
    }
    setTranslationMode(nextMode);
  }

  function requestPlayerTranslationMode(nextMode: TranslationDisplayMode) {
    if (nextMode !== 'original' && !translation.enabled) {
      Alert.alert(i18n.t('reader:translationDisabled'), t('enableTranslationInProfile'));
      return;
    }
    setPlayerTranslationMode(nextMode);
  }

  function toggleTranslation() {
    if (isVideo && youtubeMode === 'player') {
      if (!translation.enabled) {
        Alert.alert(i18n.t('reader:translationDisabled'), t('enableTranslationInProfile'));
        return;
      }
      setTimelineTranslationMode((current) => current === 'original' ? 'bilingual' : 'original');
      return;
    }
    requestTranslationMode(translationMode === 'original' ? 'bilingual' : 'original');
  }

  function changeYoutubeMode(nextMode: 'player' | 'web') {
    if (youtubeMode === nextMode) return;
    setYoutubeMode(nextMode);
    setCaptionTimeline(EMPTY_CAPTION_TIMELINE);
    setVisibleTimelineSegmentIds([]);
    setSeekCommand(null);
    setTimelineWindowCommand(null);
    timelineDragDirection.current = null;
    timelinePositionPending.current = false;
  }

  function retryArticle() {
    if (loading) return;
    setMessage(null);
    setLoadAttempt((current) => current + 1);
  }

  function chooseMode(nextMode: 'reader' | 'web') {
    modeChosenRef.current = true;
    if (isWebArticle) {
      if (extracting || nextMode === 'web') {
        extractionGenerationRef.current++;
        cancelExtractionRef.current?.();
        cancelExtractionRef.current = null;
        setExtracting(false);
        extractedReaderRef.current = null;
        setExtractedReader(null);
        setExtractionError(null);
        setMode('web');
        return;
      }
      const extract = webTranslationState.extractReader;
      if (!extract) { setExtractionError('unavailable'); return; }
      const generation = ++extractionGenerationRef.current;
      setExtractionError(null);
      setExtracting(true);
      cancelExtractionRef.current = extract(result => {
        if (generation !== extractionGenerationRef.current) return;
        setExtracting(false);
        if (!result.ok) { setExtractionError(result.reason === 'cancelled' ? null : result.reason); return; }
        extractedReaderRef.current = result;
        setExtractedReader(result);
        setMode('reader');
      });
      return;
    }
    setMode(nextMode);
  }

  const onWebDocumentChange = useCallback((next: WebDocumentIdentity) => {
    const previous = webDocumentRef.current;
    webDocumentRef.current = next;
    setCurrentWebUrl(next.url);
    if (next.url === item.external_url) originalWebDocumentRef.current = next;
    const original = originalWebDocumentRef.current;
    // Only the runtime's confirmed same-document anchors retain the original
    // epoch. Hash routes and other navigation cannot inherit this association.
    setIsOriginalWebDocument(Boolean(original && original.navigationId === next.navigationId && original.epoch === next.epoch));
    if (previous && (previous.navigationId !== next.navigationId || previous.epoch !== next.epoch)) {
      extractionGenerationRef.current++;
      cancelExtractionRef.current?.();
      cancelExtractionRef.current = null;
      extractedReaderRef.current = null;
      setExtractedReader(null);
      setExtracting(false);
      setExtractionError(null);
      setMode('web');
    }
  }, [item.external_url]);
  const onReaderSnapshotInvalidated = useCallback((requestId: string) => {
    if (extractedReaderRef.current?.requestId !== requestId) return;
    extractedReaderRef.current = null;
    setExtractedReader(null);
    setMode('web');
    setExtractionError('changed');
  }, []);
  const webSource = useMemo(() => ({ uri: item.external_url }), [item.external_url]);
  const extractedSource = useMemo(() => extractedReader
    // Keep the shell's navigation identity separate from every source link.
    // Its HTML base and sanitized content still resolve against the article URL.
    ? { html: buildExtractedReaderHtml(extractedReader.article), baseUrl: 'about:blank' }
    : null, [extractedReader]);
  const readerContent = useMemo(() => extractedReader
    ? { html: extractedReader.article.html, url: extractedReader.article.url }
    : undefined, [extractedReader]);

  // Interface copy is patched in place by the bridge; changing it must not
  // reload the article document or discard its scroll/translation state.
  const contentSource = useMemo(() => effectiveMode === 'web'
    ? isReddit || isX
      ? { uri: item.external_url }
      : { uri: article?.external_url ?? item.external_url }
    : article
      ? { html: renderReaderHtml(article, item), baseUrl: article.external_url }
      : null, [effectiveMode, isReddit, isX, item, article, renderReaderHtml]);
  const youtubeSource = videoId
    ? youtubeMode === 'web'
      ? { uri: youtubeWatchUrl(videoId) }
      : {
          headers: { Referer: YOUTUBE_APP_REFERER },
          uri: youtubeEmbedUrl(videoId),
        }
    : null;

  const header = <DetailHeader
    title={isVideo ? t('video') : isX ? t('post') : t('article')}
    onBack={onBack}
    saved={saved}
    saveBusy={saveBusy}
    onSave={isWebArticle && currentWebUrl !== item.external_url && !isOriginalWebDocument ? undefined : () => void toggleSaved()}
    onShare={() => void share()}
    onExternalOpen={() => void openBrowser()}
  />;

  if (isVideo) {
    return (
      <View style={styles.page}>
        {header}
        <View style={styles.body} onLayout={(event) => {
          const { width, height } = event.nativeEvent.layout;
          setBodySize((current) => current.width === width && current.height === height ? current : { width, height });
        }}>
          <View style={youtubeMode === 'player' ? styles.videoStage : styles.youtubeWebStage}>
            {youtubeSource ? (
              <View style={youtubeMode === 'player' ? [styles.playerFrame, bodySize.width > 0 && { width: Math.min(bodySize.width, bodySize.height * 0.62 * 16 / 9) }] : styles.youtubeWebFrame}>
                <TranslatableWebView
                  allowsFullscreenVideo
                  allowsInlineMediaPlayback
                  bridgeKind={youtubeMode === 'web' ? 'web' : 'youtube'}
                  youtubePage={youtubeMode === 'web'}
                  playerTranslationMode={playerTranslationMode}
                  onPlayerTranslationModeRequest={requestPlayerTranslationMode}
                  captionTimelineMode={youtubeMode === 'player' ? timelineTranslationMode : 'original'}
                  visibleTimelineSegmentIds={youtubeMode === 'player' ? visibleTimelineSegmentIds : undefined}
                  domStorageEnabled
                  javaScriptEnabled
                  key={`${item.content_id}:${youtubeMode}`}
                  mediaPlaybackRequiresUserAction
                  onCaptionTimelineChange={setCaptionTimeline}
                  onTranslationModeRequest={requestPlayerTranslationMode}
                  onTranslationStateChange={setTranslationState}
                  originWhitelist={['https://*']}
                  seekCommand={seekCommand}
                  timelineWindowCommand={timelineWindowCommand}
                  session={session}
                  setSupportMultipleWindows={false}
                  source={youtubeSource}
                  style={styles.player}
                  targetLocale={translation.targetLocale}
                  translationEngineId={translation.effectiveEngineId}
                  translationEnabled={translation.enabled}
                  translationMode={youtubeMode === 'player' ? playerTranslationMode : translationMode}
                  translationPurpose={youtubeMode === 'web' ? 'web_segment' : 'caption'}
                />
              </View>
            ) : (
              <View style={styles.unavailable}>
                <MaterialCommunityIcons color={colors.textTertiary} name="video-outline" size={28} />
                <Text style={styles.unavailableText}>{t('unknownVideo')}</Text>
              </View>
            )}
          </View>
          {videoId && youtubeMode === 'player' ? (
            <View style={styles.timelineStage}>
              <View style={styles.timelineToolbar}>
                <Text style={styles.timelineTitle}>{t('captionTimeline')}</Text>
                <Text style={styles.timelineSubtitle}>{t('captionTimelineHint')}</Text>
              </View>
              {timelineTranslationMode !== 'original' && translationState.error ? (
                <Text accessibilityLiveRegion="polite" style={styles.hint}>{t('timelineTranslationFailed', { error: translationState.error })}</Text>
              ) : null}
              <FlatList
                key={captionTimeline.captions[0]?.segmentId ?? 'empty-timeline'}
                ref={timelineListRef}
                onLayout={(event) => { timelineViewportHeight.current = event.nativeEvent.layout.height; }}
                onTouchStart={(event) => { timelineTouchStartY.current = event.nativeEvent.pageY; }}
                onTouchEnd={(event) => {
                  const startY = timelineTouchStartY.current;
                  timelineTouchStartY.current = null;
                  if (startY === null || timelineContentHeight.current > timelineViewportHeight.current) return;
                  const distance = startY - event.nativeEvent.pageY;
                  if (Math.abs(distance) < 30) return;
                  const direction = distance > 0 ? 'next' : 'previous';
                  timelineDragDirection.current = direction;
                  requestTimelineWindow(direction);
                }}
                onScrollBeginDrag={(event) => {
                  timelineDragging.current = true;
                  timelineScrollOffset.current = event.nativeEvent.contentOffset.y;
                  timelineDragDirection.current = null;
                }}
                onScroll={(event) => {
                  if (!timelineDragging.current) return;
                  const offset = event.nativeEvent.contentOffset.y;
                  if (offset !== timelineScrollOffset.current) {
                    timelineDragDirection.current = offset > timelineScrollOffset.current ? 'next' : 'previous';
                  }
                  timelineScrollOffset.current = offset;
                  if (offset <= 10) requestTimelineWindow('previous');
                  if (offset + event.nativeEvent.layoutMeasurement.height >= event.nativeEvent.contentSize.height - 10) requestTimelineWindow('next');
                }}
                scrollEventThrottle={16}
                onEndReached={() => requestTimelineWindow('next')}
                onStartReached={() => requestTimelineWindow('previous')}
                onEndReachedThreshold={0.1}
                onStartReachedThreshold={0.1}
                onContentSizeChange={(_width, height) => {
                  timelineContentHeight.current = height;
                  if (!timelinePositionPending.current) return;
                  if (captionTimeline.captions[0]?.segmentId === timelineWindowCommand?.firstId) return;
                  timelinePositionPending.current = false;
                  if (timelineWindowCommand?.direction === 'previous') timelineListRef.current?.scrollToEnd({ animated: false });
                }}
                contentContainerStyle={styles.timelineContent}
                data={captionTimeline.captions}
                onViewableItemsChanged={onTimelineViewableItemsChanged.current}
                viewabilityConfig={timelineViewability.current}
                extraData={`${captionTimeline.currentSegmentId}:${translation.enabled}:${timelineTranslationMode}`}
                keyExtractor={(caption) => caption.segmentId}
                ListEmptyComponent={(
                  <Text style={styles.timelineEmpty}>
                    {translationState.bridgeState === 'captions_unavailable'
                      ? t('captionsUnavailable')
                      : t('captionsWaiting')}
                  </Text>
                )}
                renderItem={({ item: caption }) => (
                  <CaptionTimelineRow
                    active={caption.segmentId === captionTimeline.currentSegmentId}
                    caption={caption}
                    translated={translation.enabled && timelineTranslationMode !== 'original'}
                    onPress={() => {
                      setSeekCommand(current => ({ positionMs: caption.startMs, sequence: (current?.sequence ?? 0) + 1 }));
                    }}
                  />
                )}
                showsVerticalScrollIndicator={false}
                style={styles.timelineList}
              />
            </View>
          ) : null}
          {youtubeMode === 'web' && translationMode !== 'original' && translationState.error ? (
            <View accessibilityLiveRegion="polite" style={styles.translationErrorBanner}>
              <Text numberOfLines={2} style={styles.translationErrorText}>{translationState.error}</Text>
              {translationState.retry ? <Pressable accessibilityRole="button" onPress={translationState.retry} style={styles.translationRetry}><Text style={styles.translationErrorText}>{t('retryTranslation')}</Text></Pressable> : null}
            </View>
          ) : null}
        </View>
        <FloatingReaderTools
          translationActive={translation.enabled && (youtubeMode === 'player' ? timelineTranslationMode : translationMode) !== 'original'}
          translationBusy={translationState.activeCount > 0 && (youtubeMode === 'player' ? timelineTranslationMode : translationMode) !== 'original'}
          translationLabel={youtubeMode === 'player' ? t('translateTimeline') : t('translateWeb')}
          onTranslate={toggleTranslation}
          modeIcon={youtubeMode === 'player' ? 'web' : 'play-box-outline'}
          modeLabel={youtubeMode === 'player' ? t('switchToWeb') : t('switchToPlayer')}
          onSwitchMode={videoId ? () => changeYoutubeMode(youtubeMode === 'player' ? 'web' : 'player') : undefined}
        />
      </View>
    );
  }

  return (
    <View style={styles.page}>
      {header}
      <View style={styles.body}>
        <TitleTranslationNotice
          contentIds={titleTranslations.timedOutContentIds}
          message={titleTranslations.timeoutMessage}
          onRetry={titleTranslations.retryTimedOut}
        />
        {loading && effectiveMode === 'reader' ? <View style={styles.loading}><ActivityIndicator color={colors.textStrong} /></View> : null}
        {message && effectiveMode === 'reader' ? (
          <View accessibilityLiveRegion="polite" style={styles.readerError}>
            <MaterialCommunityIcons color={colors.danger} name="file-alert-outline" size={30} />
            <Text style={styles.readerErrorTitle}>{t('readerLoadFailed')}</Text>
            <Text style={styles.message}>{message && ('text' in message ? message.text : t(message.key))}</Text>
            <View style={styles.readerErrorActions}>
              <Pressable accessibilityRole="button" onPress={retryArticle} style={styles.readerErrorPrimary}>
                <MaterialCommunityIcons color={colors.surface} name="refresh" size={17} />
                <Text style={styles.readerErrorPrimaryText}>{t('retry')}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => chooseMode('web')} style={styles.readerErrorSecondary}>
                <MaterialCommunityIcons color={colors.textStrong} name="web" size={17} />
                <Text style={styles.readerErrorSecondaryText}>{t('openWeb')}</Text>
              </Pressable>
            </View>
          </View>
        ) : null}
        {isWebArticle ? (
          <View style={styles.webArticleContainer}>
            <View pointerEvents={effectiveMode === 'reader' ? 'none' : 'auto'}
              accessibilityElementsHidden={effectiveMode === 'reader'}
              importantForAccessibility={effectiveMode === 'reader' ? 'no-hide-descendants' : 'auto'}
              style={[styles.webArticleLayer, effectiveMode === 'reader' && styles.hiddenWebArticle]}>
              <TranslatableWebView
                key={`web:${item.content_id}`}
                bridgeKind="web"
                javaScriptEnabled
                domStorageEnabled
                source={webSource}
                navigationCommand={webNavigationCommand}
                session={session}
                originWhitelist={['http://*', 'https://*']}
                setSupportMultipleWindows={false}
                style={styles.webView}
                onTranslationStateChange={setWebTranslationState}
                onWebDocumentChange={onWebDocumentChange}
                onReaderSnapshotInvalidated={onReaderSnapshotInvalidated}
                targetLocale={translation.targetLocale}
                translationEngineId={translation.effectiveEngineId}
                translationEnabled={translation.enabled}
                translationMode={effectiveMode === 'web' ? translationMode : 'original'}
                translationPurpose="web_segment"
              />
            </View>
            {effectiveMode === 'reader' && extractedSource && extractedReader ? (
              <View style={styles.webArticleLayer}>
                <TranslatableWebView
                  key={`reader:${extractedReader.requestId}`}
                  bridgeKind="web"
                  javaScriptEnabled
                  source={extractedSource}
                  readerContent={readerContent}
                  session={session}
                  originWhitelist={['*']}
                  setSupportMultipleWindows={false}
                  style={styles.webView}
                  onTranslationStateChange={setTranslationState}
                  onShouldStartLoadWithRequest={request => {
                    if (request.url === 'about:blank') return true;
                    if (/^https?:\/\//i.test(request.url)) {
                      chooseMode('web');
                      setWebNavigationCommand(current => ({ url: request.url, sequence: (current?.sequence ?? 0) + 1 }));
                    }
                    return false;
                  }}
                  targetLocale={translation.targetLocale}
                  translationEngineId={translation.effectiveEngineId}
                  translationEnabled={translation.enabled}
                  translationMode={translationMode}
                  translationProfile="reader"
                  translationPurpose="web_segment"
                />
              </View>
            ) : null}
            {extractionError ? <View accessibilityLiveRegion="polite" style={styles.translationErrorBanner}>
              <Text style={styles.translationErrorText}>{t(extractionError === 'changed' ? 'readerPageChanged' : extractionError === 'too_large' ? 'readerPageTooLarge' : 'readerExtractionFailed')}</Text>
            </View> : null}
          </View>
        ) : contentSource ? (
          <TranslatableWebView
            bridgeKind="web"
            javaScriptEnabled
            onTranslationStateChange={setTranslationState}
            originWhitelist={['*']}
            session={session}
            setSupportMultipleWindows={effectiveMode === 'web' && (isReddit || isX)}
            source={contentSource}
            style={styles.webView}
            targetLocale={translation.targetLocale}
            translationEngineId={translation.effectiveEngineId}
            translationEnabled={translation.enabled}
            translationMode={translationMode}
            translationProfile={translationProfile}
            readerPublishedAt={effectiveMode === 'reader' ? article?.published_at ?? item.published_at ?? item.fetched_at : undefined}
            translationPurpose={effectiveMode === 'web' ? 'web_segment' : 'paragraph'}
          />
        ) : null}
        {translationState.bridgeState === 'profile_mismatch' &&
        translationMode !== 'original' &&
        effectiveMode === 'web' ? (
          <View style={styles.translationErrorBanner}>
            <Text numberOfLines={3} style={styles.translationErrorText}>
              {t('webLayoutUnsupported')}
            </Text>
          </View>
        ) : translationState.error && translationMode !== 'original' ? (
          <View style={styles.translationErrorBanner}>
            <Text numberOfLines={2} style={styles.translationErrorText}>
              {translationState.error}
            </Text>
            {translationState.retry ? (
              <Pressable accessibilityRole="button" onPress={translationState.retry} style={styles.translationRetry}>
                <Text style={styles.translationErrorText}>{t('retryTranslation')}</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>
      <FloatingReaderTools
        translationActive={translationMode !== 'original' && translation.enabled}
        translationBusy={translationState.activeCount > 0 && translationMode !== 'original'}
        translationLabel={effectiveMode === 'web' ? t('translateWeb') : t('translateBody')}
        onTranslate={toggleTranslation}
        modeIcon={effectiveMode === 'reader' ? 'web' : 'book-open-page-variant'}
        modeBusy={extracting}
        modeLabel={extracting ? t('cancelReaderExtraction') : effectiveMode === 'reader' ? t('switchToWeb') : t('switchToReader')}
        onSwitchMode={isReddit ? undefined : () => chooseMode(effectiveMode === 'reader' ? 'web' : 'reader')}
      />
    </View>
  );
}

function formatCaptionTime(timeMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(timeMs / 1_000));
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function CaptionTimelineRow({
  active,
  caption,
  translated,
  onPress,
}: {
  active: boolean;
  caption: SynchronizedCaption;
  translated: boolean;
  onPress: () => void;
}) {
  const { t } = useTranslation('reader');
  return (
    <Pressable
      accessibilityState={{ selected: active }}
      accessibilityLabel={t('seekCaption', { time: formatCaptionTime(caption.startMs), text: caption.sourceText })}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.timelineRow,
        active && styles.timelineRowActive,
        pressed && styles.timelineRowPressed,
      ]}
    >
      <Text style={[styles.timelineTime, active && styles.timelineTimeActive]}>
        {formatCaptionTime(caption.startMs)}
      </Text>
      <View style={styles.timelineCopy}>
        <Text style={[styles.timelineSource, active && styles.timelineSourceActive]}>{caption.sourceText}</Text>
        {translated && caption.translatedText ? <Text style={[styles.timelineTranslation, active && styles.timelineTranslationActive]}>{caption.translatedText}</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  body: { flex: 1, minHeight: 0, position: 'relative' },
  timelineTranslation: { color: '#93988d', fontSize: 12, lineHeight: 22, marginTop: 5 },
  page: { backgroundColor: colors.background, flex: 1 },
  webView: { flex: 1 },
  webArticleContainer: { flex: 1, minHeight: 0, position: 'relative' },
  webArticleLayer: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 },
  hiddenWebArticle: { opacity: 0 },
  loading: { alignItems: 'center', paddingVertical: 28 },
  message: { color: colors.textTertiary, fontSize: 14, lineHeight: 21, marginTop: 7, textAlign: 'center' },
  readerError: { alignItems: 'center', flex: 1, justifyContent: 'center', paddingBottom: 84, paddingHorizontal: 28 },
  readerErrorTitle: { color: colors.textStrong, fontSize: 18, fontWeight: '700', marginTop: 12 },
  readerErrorActions: { flexDirection: 'row', gap: 10, marginTop: 18 },
  readerErrorPrimary: { alignItems: 'center', backgroundColor: colors.textStrong, borderRadius: radii.pill, flexDirection: 'row', gap: 6, minHeight: touchTarget, paddingHorizontal: 18 },
  readerErrorPrimaryText: { color: colors.surface, fontSize: 14, fontWeight: '700' },
  readerErrorSecondary: { alignItems: 'center', backgroundColor: colors.surface, borderColor: colors.borderStrong, borderRadius: radii.pill, borderWidth: 1, flexDirection: 'row', gap: 6, minHeight: touchTarget, paddingHorizontal: 18 },
  readerErrorSecondaryText: { color: colors.textStrong, fontSize: 14, fontWeight: '700' },
  translationErrorBanner: { backgroundColor: colors.warningSurface, borderRadius: radii.sm, bottom: 70, left: 20, paddingHorizontal: 12, paddingVertical: 8, position: 'absolute', right: 20, zIndex: 2 },
  translationErrorText: { color: colors.warningText, fontSize: 12, lineHeight: 17 },
  translationRetry: { alignSelf: 'flex-start', justifyContent: 'center', minHeight: 40 },
  videoStage: { alignItems: 'center', flexGrow: 0, flexShrink: 0, paddingBottom: 0 },
  playerFrame: { aspectRatio: 16 / 9, backgroundColor: '#000000', overflow: 'hidden', width: '100%' },
  player: { backgroundColor: '#000000', flex: 1 },
  youtubeWebStage: { flex: 1, minHeight: 0, paddingTop: 8 },
  youtubeWebFrame: { flex: 1, minHeight: 0, overflow: 'hidden' },
  timelineStage: { flex: 1, minHeight: 0 },
  timelineToolbar: { alignItems: 'center', flexDirection: 'row', gap: 12, justifyContent: 'space-between', paddingHorizontal: 22, paddingTop: 18, paddingBottom: 14 },
  hint: { color: colors.textTertiary, fontSize: 12, lineHeight: 18, marginBottom: 4, marginHorizontal: 22 },
  timelineTitle: { color: colors.textStrong, fontSize: 16, fontWeight: '600' },
  timelineSubtitle: { color: '#969a90', fontSize: 10, flexShrink: 1 },
  timelineContent: { paddingBottom: 32, paddingHorizontal: 12 },
  timelineList: { flex: 1 },
  timelineEmpty: { color: colors.textTertiary, fontSize: 14, lineHeight: 22, paddingHorizontal: 10, paddingTop: 18 },
  timelineRow: { alignItems: 'flex-start', borderRadius: 10, flexDirection: 'row', gap: 9, marginBottom: 2, paddingTop: 14, paddingBottom: 15, paddingLeft: 10, paddingRight: 12 },
  timelineRowActive: { backgroundColor: '#f0f0eb' },
  timelineRowPressed: { opacity: 0.7 },
  timelineTime: { color: '#a0a499', fontSize: 10, fontVariant: ['tabular-nums'], minWidth: 38, paddingTop: 4 },
  timelineTimeActive: { color: '#545e49' },
  timelineCopy: { flex: 1 },
  timelineSource: { color: '#73786e', fontSize: 14, lineHeight: 24 },
  timelineTranslationActive: { color: '#69715f' },
  timelineSourceActive: { color: '#282f25', fontWeight: '500' },
  unavailable: { alignItems: 'center', backgroundColor: colors.surfaceMuted, borderRadius: radii.md, gap: 8, justifyContent: 'center', minHeight: 180, padding: 20 },
  unavailableText: { color: colors.textTertiary, fontSize: 14, lineHeight: 20, textAlign: 'center' },
});
