import { i18n, useTranslation } from '../i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Linking, Pressable, ScrollView, Share, StyleSheet, Text, View } from 'react-native';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';
import { DetailHeader } from '../components/DetailHeader';
import { FloatingReaderTools } from '../components/FloatingReaderTools';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime } from '../lib/connection/react';
import { invalidateAfterSavedMutation } from '../state/invalidation';
import { getRankingSavedState, saveRankingContent, setSavedContent } from '../lib/api';

import {
  TranslatableWebView,
  type TranslatableWebViewState,
} from '../components/TranslatableWebView';
import { RankingTitle } from '../components/RankingTitle';
import { XRankingContent } from '../components/XRankingContent';
import { translationResultMatchesRankingRoute } from '../domain/rankingTranslationProjection';
import { type TranslationDisplayMode } from '../domain/webTranslation';
import { useSegmentTranslationQueue } from '../hooks/useSegmentTranslationQueue';
import { useRankingTitleRetry, type RankingTitleRetryItem } from '../hooks/useRankingTitleRetry';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import type { RankingItem, TranslationSegmentResult } from '../lib/api';

const INITIAL_TRANSLATION_STATE: TranslatableWebViewState = {
  activeCount: 0,
  bridgeState: 'initializing',
  discoveredCount: 0,
  error: null,
};

export function RankingPreviewScreen({
  item,
  onBack,
  session,
}: {
  item: RankingItem;
  onBack: () => void;
  session: Session;
}) {
  const { t } = useTranslation('reader');
  const [translationMode, setTranslationMode] = useState<TranslationDisplayMode>('original');
  const [translationState, setTranslationState] = useState(INITIAL_TRANSLATION_STATE);
  const [liveItem, setLiveItem] = useState(item);
  const isXRanking = item.ranking_context?.kind === 'techtwitter';
  const isProductHunt = item.ranking_kind === 'product_hunt';
  const [previewMode, setPreviewMode] = useState<'reader' | 'web'>('reader');
  const [webOpened, setWebOpened] = useState(false);
  const showWeb = !isXRanking || previewMode === 'web';
  const translation = useTranslationPreference(session, true);
  const runtime = useReaderRuntime();
  const queryClient = useQueryClient();
  const routeKey = `${translation.targetLocale}\u0000${translation.effectiveEngineFingerprint ?? ''}`;
  const routeRef = useRef(routeKey);
  useEffect(() => {
    if (routeRef.current === routeKey) return;
    routeRef.current = routeKey;
    setLiveItem({ ...item, translated_title: null, translated_description: null, title_translation_status: null, description_translation_status: null, translation_locale: null,
      body_segments: item.body_segments?.map((segment) => ({ ...segment, translated_text: null, translation_status: null })) });
  }, [item, routeKey]);
  const [saved, setSaved] = useState(item.saved_state ?? false);
  const [contentId, setContentId] = useState(item.saved_content_id ?? null);
  const [saveBusy, setSaveBusy] = useState(false);
  const mutationBusy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  // Resolve persisted state before displaying an actionable save button.
  useEffect(() => {
    const context = captureRuntimeContext(runtime);
    if (!context || isProductHunt || item.saved_content_id) return;
    let cancelled = false;
    setSaveBusy(true);
    void getRankingSavedState(session, item.url, context.runtime).then((state) => {
      if (cancelled || !isRuntimeContextCurrent(context)) return;
      setSaved(state.is_saved);
      setContentId(state.content_id);
    }).catch(() => {
      // Clicking save retries the state read; a failed read never implies unsaved.
    }).finally(() => {
      if (!cancelled && isRuntimeContextCurrent(context)) setSaveBusy(false);
    });
    return () => { cancelled = true; };
  }, [isProductHunt, item.saved_content_id, item.url, runtime, session]);

  async function toggleSaved() {
    const context = captureRuntimeContext(runtime);
    if (!context || isProductHunt || mutationBusy.current) return;
    mutationBusy.current = true;
    setSaveBusy(true);
    try {
      const state = item.saved_content_id
        ? { content_id: contentId, is_saved: saved }
        : await getRankingSavedState(session, item.url, context.runtime);
      if (!mounted.current || !isRuntimeContextCurrent(context)) return;
      const nextSaved = !state.is_saved;
      let nextId = state.content_id;
      if (nextId) {
        await setSavedContent(session, nextId, nextSaved, context.runtime);
      } else {
        if (!item.ranking_context) throw new Error(i18n.t('reader:rankingSourceMissing'));
        const result = await saveRankingContent(session, { ...item.ranking_context, url: item.url }, context.runtime);
        nextId = result.content_id;
      }
      if (!mounted.current || !isRuntimeContextCurrent(context)) return;
      setContentId(nextId);
      setSaved(nextSaved);
      await invalidateAfterSavedMutation(queryClient, session.user.id, context.serverId, context);
    } catch (error) {
      if (mounted.current && isRuntimeContextCurrent(context)) Alert.alert(i18n.t('reader:saveFailed'), error instanceof Error ? error.message : i18n.t('reader:tryLater'));
    } finally {
      mutationBusy.current = false;
      if (mounted.current && isRuntimeContextCurrent(context)) setSaveBusy(false);
    }
  }

  async function share() {
    try { await Share.share({ message: item.url, url: item.url, title: liveItem.translated_title?.trim() || liveItem.title }); }
    catch { Alert.alert(i18n.t('reader:shareFailed')); }
  }
  const applyItemTranslations = useCallback((results: TranslationSegmentResult[]) => {
    if (!translation.enabled) return;
    const matched = results.filter((result) => translationResultMatchesRankingRoute(result, translation.targetLocale, translation.enabled, translation.effectiveEngineFingerprint));
    if (!matched.length) return;
    setLiveItem((current) => {
      const title = matched.find(
        (result) => result.segment_id === `${current.translation_key}:title` && result.purpose === 'ranking_title',
      );
      const description = matched.find(
        (result) => result.segment_id === `${current.translation_key}:description` && result.purpose === 'ranking_description',
      );
      const bodySegments = current.body_segments?.map((segment) => {
        const result = matched.find((candidate) => candidate.segment_id === segment.segment_id && candidate.purpose === 'paragraph');
        return result ? { ...segment, translated_text: result.translated_text, translation_status: result.translation_status } : segment;
      });
      if (!title && !description && !bodySegments?.some((segment, index) => segment !== current.body_segments?.[index])) return current;
      const next = {
        ...current,
        ...(title ? {
          translated_title: title.translated_text,
          title_translation_status: title.translation_status,
          translation_locale: title.translation_locale ?? current.translation_locale,
        } : {}),
        ...(description ? {
          translated_description: description.translated_text,
          description_translation_status: description.translation_status,
          translation_locale: description.translation_locale ?? current.translation_locale,
        } : {}),
        ...(bodySegments?.some((segment, index) => segment !== current.body_segments?.[index]) ? { body_segments: bodySegments, translation_locale: matched[0].translation_locale } : {}),
      };
      if (
        next.translated_title === current.translated_title &&
        next.title_translation_status === current.title_translation_status &&
        next.translated_description === current.translated_description &&
        next.description_translation_status === current.description_translation_status &&
        next.body_segments === current.body_segments &&
        next.translation_locale === current.translation_locale
      ) return current;
      return next;
    });
  }, [translation.enabled, translation.targetLocale, translation.effectiveEngineFingerprint]);
  const itemTranslationQueue = useSegmentTranslationQueue({
    enabled: translation.enabled,
    engineId: translation.effectiveEngineFingerprint ?? translation.effectiveEngineId,
    onResults: applyItemTranslations,
    session,
    targetLocale: translation.targetLocale,
  });
  const { clearFailedSegments, clearTimedOutSegments, enqueueSegments: enqueueItemTranslations } = itemTranslationQueue;
  const markTitleRetryFailure = useCallback((retryItem: RankingTitleRetryItem) => {
    clearTimedOutSegments([`${retryItem.translation_key}:title`]);
    setLiveItem((current) => current.translation_key === retryItem.translation_key
      ? { ...current, translated_title: null, title_translation_status: 'failed' }
      : current);
  }, [clearTimedOutSegments]);
  const handleTitleRetryResults = useCallback((results: TranslationSegmentResult[]) => {
    const segmentIds = results.map((result) => result.segment_id);
    clearFailedSegments(segmentIds);
    clearTimedOutSegments(segmentIds);
    applyItemTranslations(results);
  }, [applyItemTranslations, clearFailedSegments, clearTimedOutSegments]);
  const {
    isTitleRetrying,
    retryTitleTranslation,
  } = useRankingTitleRetry({
    onFailure: markTitleRetryFailure,
    onResults: handleTitleRetryResults,
    session,
    scopeKey: liveItem.translation_key,
  });

  useEffect(() => {
    if (item.ranking_context?.kind === 'techtwitter') {
      enqueueItemTranslations((liveItem.body_segments ?? []).filter((segment) => segment.text.trim() && segment.translation_status !== 'failed' && segment.translation_status !== 'succeeded').map((segment) => ({
        purpose: 'paragraph' as const, segment_id: segment.segment_id, text: segment.text,
      })));
      return;
    }
    const segments = [];
    if (
      !liveItem.translated_title &&
      liveItem.title_translation_status !== 'failed' &&
      liveItem.title_translation_status !== 'succeeded'
    ) {
      segments.push({
        purpose: 'ranking_title' as const,
        segment_id: `${liveItem.translation_key}:title`,
        text: liveItem.title,
      });
    }
    if (
      liveItem.description &&
      !liveItem.translated_description &&
      liveItem.description_translation_status !== 'failed' &&
      liveItem.description_translation_status !== 'succeeded'
    ) {
      segments.push({
        purpose: 'ranking_description' as const,
        segment_id: `${liveItem.translation_key}:description`,
        text: liveItem.description,
      });
    }
    enqueueItemTranslations(segments);
  }, [enqueueItemTranslations, item.ranking_context?.kind, liveItem]);
  function retryBodyTranslation() {
    itemTranslationQueue.retrySegments((liveItem.body_segments ?? [])
      .filter((segment) => segment.text.trim() && segment.translation_status !== 'succeeded')
      .map((segment) => ({ purpose: 'paragraph' as const, segment_id: segment.segment_id, text: segment.text })));
  }

  async function openBrowserTab() {
    try {
      await Linking.openURL(item.url);
    } catch {
      Alert.alert(i18n.t('reader:browserTabFailed'));
    }
  }

  function enableTranslation() {
    if (translationMode === 'original' && !translation.enabled) {
      Alert.alert(i18n.t('reader:translationDisabled'), t('enableTranslationInProfile'));
      return;
    }
    setTranslationMode((current) => current === 'original' ? 'bilingual' : 'original');
  }

  return (
    <View style={styles.page}>
      <DetailHeader title={liveItem.translated_title?.trim() || liveItem.title} onBack={onBack} onSave={isProductHunt ? undefined : () => void toggleSaved()} saved={saved} saveBusy={saveBusy} onShare={() => void share()} onExternalOpen={() => void openBrowserTab()} />
      <View style={styles.previewStage}>
        {isXRanking ? (
          <View
            testID="ranking_native_body"
            pointerEvents={showWeb ? 'none' : 'auto'}
            accessibilityElementsHidden={showWeb}
            importantForAccessibility={showWeb ? 'no-hide-descendants' : 'auto'}
            style={[styles.previewLayer, showWeb && styles.hiddenPreview]}
          >
            <ScrollView style={styles.webView} contentContainerStyle={styles.xPreviewContent}>
              <XRankingContent item={liveItem} translated={translationMode !== 'original' && translation.enabled} onRetryTranslation={retryBodyTranslation} translationBusy={itemTranslationQueue.isTranslating} translationRetryNeeded={liveItem.body_segments?.some((segment) => itemTranslationQueue.timedOutSegmentIds.has(segment.segment_id) || itemTranslationQueue.failedSegmentIds.has(segment.segment_id))} />
            </ScrollView>
          </View>
        ) : null}
        {!isXRanking || webOpened ? (
          <View
            testID="ranking_original_web"
            pointerEvents={showWeb ? 'auto' : 'none'}
            accessibilityElementsHidden={!showWeb}
            importantForAccessibility={showWeb ? 'auto' : 'no-hide-descendants'}
            style={[styles.previewLayer, !showWeb && styles.hiddenPreview]}
          >
            {!isXRanking && (liveItem.title_translation_status === 'failed' || isTitleRetrying(liveItem) || itemTranslationQueue.timedOutSegmentIds.has(`${liveItem.translation_key}:title`)) ? (
              <View style={styles.previewHeader}>
                <RankingTitle numberOfLines={2} onRetry={() => { void retryTitleTranslation(liveItem); }} retrying={isTitleRetrying(liveItem)} status={liveItem.title_translation_status} timedOut={itemTranslationQueue.timedOutSegmentIds.has(`${liveItem.translation_key}:title`)} title={liveItem.title} titleStyle={styles.title} translatedTitle={liveItem.translated_title} />
              </View>
            ) : null}
            <TranslatableWebView
              bridgeKind="web"
              javaScriptEnabled
              onShouldStartLoadWithRequest={(request) => (
                request.url.startsWith('https://') ||
                request.url.startsWith('http://') ||
                request.url === 'about:blank'
              )}
              onTranslationStateChange={setTranslationState}
              originWhitelist={['https://*', 'http://*']}
              session={session}
              setSupportMultipleWindows={false}
              source={{ uri: item.url }}
              style={styles.webView}
              targetLocale={translation.targetLocale}
              translationEngineId={translation.effectiveEngineId}
              translationEnabled={translation.enabled}
              translationMode={showWeb ? translationMode : 'original'}
              translationPurpose="web_segment"
            />
          </View>
        ) : null}
      </View>
      {showWeb && translationState.bridgeState === 'profile_mismatch' && translationMode !== 'original' ? (
        <View style={styles.errorBanner}>
          <Text numberOfLines={3} style={styles.errorText}>
            {t('webLayoutUnsupported')}
          </Text>
        </View>
      ) : showWeb && translationState.error && translationMode !== 'original' ? (
        <View style={styles.errorBanner}>
          <Text numberOfLines={2} style={styles.errorText}>{translationState.error}</Text>
          {translationState.retry ? (
            <Pressable accessibilityRole="button" onPress={translationState.retry} style={styles.translationRetry}>
              <Text style={styles.errorText}>{t('retryTranslation')}</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      <FloatingReaderTools
        translationActive={translationMode !== 'original' && translation.enabled}
        translationBusy={(showWeb ? translationState.activeCount > 0 : itemTranslationQueue.isTranslating) && translationMode !== 'original'}
        onTranslate={enableTranslation}
        translationLabel={showWeb ? t('toggleWebTranslation') : t('translateBody')}
        modeIcon={isXRanking ? (showWeb ? 'book-open-page-variant' : 'web') : undefined}
        modeLabel={showWeb ? t('switchToReader') : t('switchToWeb')}
        onSwitchMode={isXRanking ? () => {
          setWebOpened(true);
          setPreviewMode((current) => current === 'reader' ? 'web' : 'reader');
        } : undefined}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  page: { backgroundColor: '#FAFAF8', flex: 1 },
  previewHeader: { borderBottomColor: '#E5E5E3', borderBottomWidth: 1, paddingBottom: 8, paddingHorizontal: 20 },
  previewStage: { flex: 1, minHeight: 0, position: 'relative' },
  previewLayer: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 },
  hiddenPreview: { opacity: 0 },
  xPreviewContent: { paddingHorizontal: 20, paddingVertical: 12 },
  title: { color: '#111111', fontSize: 14, fontWeight: '600', lineHeight: 20 },
  webView: { flex: 1 },
  errorBanner: { backgroundColor: '#FFF4E5', paddingHorizontal: 12, paddingVertical: 8 },
  errorText: { color: '#7A4B00', fontSize: 12, lineHeight: 17 },
  translationRetry: { alignSelf: 'flex-start', justifyContent: 'center', minHeight: 40 },
});
