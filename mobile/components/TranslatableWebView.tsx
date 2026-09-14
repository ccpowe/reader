import { i18n, useTranslation } from '../i18n';
import { createReaderInterfaceScript } from '../domain/readerHtml';
import { isWebReaderArticle, MAX_WEB_READER_BYTES, utf8ByteLength, type WebDocumentIdentity, type WebReaderResult } from '../domain/webReader';
import { exportTranslationDiagnostics, recordTranslationDiagnostic } from '../domain/translationDiagnostics';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { Session } from '../lib/readerAuth';
import {
  WebView,
  type WebViewMessageEvent,
  type WebViewProps,
} from 'react-native-webview';

import {
  createWebTranslationScript,
  WEB_TRANSLATION_RUNTIME_VERSION,
  type TranslationDisplayMode,
} from '../domain/webTranslation';
import { createYouTubeTranslationScript, getYouTubeInterfaceMessages } from '../domain/youtubeTranslation';
import { useRealtimeTranslationScheduler, type RealtimeTranslationBatch } from '../hooks/useRealtimeTranslationScheduler';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import type { TranslationPurpose, TranslationSegment } from '../lib/api';

const MAX_BRIDGE_MESSAGE_CHARS = 100_000;
const MAX_SEGMENTS_PER_DOCUMENT = 5_000;
const MAX_SEGMENT_CHARS = 8_000;
const MAX_ACCEPTED_CHARACTERS_PER_DOCUMENT = 750_000;
const MAX_TIMELINE_CUES = 180;
const MAX_TIMELINE_CHARACTERS = 40_000;

type BridgeKind = 'web' | 'youtube';

export type TranslatableWebViewState = {
  activeCount: number;
  bridgeState: string;
  discoveredCount: number;
  error: string | null;
  retry?: () => void;
  exportDiagnostics?: () => string;
  inspectSelection?: (onResult: (message: string) => void) => void;
  failedCount?: number;
  extractReader?: (onResult: (result: WebReaderResult) => void) => () => void;
};

export type SynchronizedCaption = {
  endMs: number;
  segmentId: string;
  startMs: number;
  sourceText: string;
  translatedText: string | null;
};

export type SynchronizedCaptionTimeline = {
  captions: SynchronizedCaption[];
  currentSegmentId: string | null;
  hasPrevious?: boolean;
  hasNext?: boolean;
};

type TranslatableWebViewProps = WebViewProps & {
  bridgeKind: BridgeKind;
  onCaptionChange?: (caption: SynchronizedCaption | null) => void;
  onCaptionTimelineChange?: (timeline: SynchronizedCaptionTimeline) => void;
  onTranslationModeRequest?: (mode: TranslationDisplayMode) => void;
  onTranslationStateChange?: (state: TranslatableWebViewState) => void;
  session: Session;
  targetLocale: string;
  translationEngineId?: string | null;
  translationEnabled: boolean;
  translationMode: TranslationDisplayMode;
  /** Internal reader documents request the reader profile; ordinary pages self-select. */
  translationProfile?: 'reader';
  /** Reader-owned shell metadata, kept separate from source and translation language. */
  readerPublishedAt?: string;
  /** Untrusted candidate data, purified again inside the app-owned document. */
  readerContent?: { html: string; url: string };
  onWebDocumentChange?: (document: WebDocumentIdentity) => void;
  onReaderSnapshotInvalidated?: (requestId: string) => void;
  /** Explicit navigation, including a URL equal to the original source prop. */
  navigationCommand?: { url: string; sequence: number } | null;
  translationPurpose: TranslationPurpose;
  seekToMs?: number | null;
  seekCommand?: { positionMs: number; sequence: number } | null;
  timelineWindowCommand?: { direction: 'previous' | 'next'; firstId: string; sequence: number } | null;
  captionTimelineMode?: TranslationDisplayMode;
  visibleTimelineSegmentIds?: string[];
  youtubePage?: boolean;
  playerTranslationMode?: TranslationDisplayMode;
  onPlayerTranslationModeRequest?: (mode: TranslationDisplayMode) => void;
};

type BridgeEnvelope = {
  rule_index?: unknown;
  tag_name?: unknown;
  reason?: unknown;
  display_status?: unknown;
  challenge_nonce?: unknown;
  channel_token?: unknown;
  captions?: unknown;
  has_previous?: unknown;
  has_next?: unknown;
  count?: unknown;
  current_segment_id?: unknown;
  end_ms?: unknown;
  mode?: unknown;
  media_epoch?: unknown;
  navigation_id?: unknown;
  document_epoch?: unknown;
  document_url?: unknown;
  dirty_roots?: unknown;
  deferred_roots?: unknown;
  paragraphs_detected?: unknown;
  paragraphs_filtered?: unknown;
  paragraphs_queued?: unknown;
  pending_roots?: unknown;
  processing_roots?: unknown;
  profile_id?: unknown;
  rect_reads?: unknown;
  render_failures?: unknown;
  scope_id?: unknown;
  segment_id?: unknown;
  style_reads?: unknown;
  batches?: unknown;
  start_ms?: unknown;
  source_text?: unknown;
  state?: unknown;
  traversal_ms?: unknown;
  tree_nodes?: unknown;
  truncated_reason?: unknown;
  translated_text?: unknown;
  type?: unknown;
  request_id?: unknown;
  article?: unknown;
  error?: unknown;
};

function injectedValue(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

function joinScripts(...scripts: (string | undefined)[]): string | undefined {
  const joined = scripts.filter(Boolean).join('\n;');
  return joined ? `${joined}\n;true;` : undefined;
}

/** WebView boundary shared by article paragraphs, arbitrary pages, and captions. */
function useTranslationBridge({
  bridgeKind,
  injectedJavaScript,
  injectedJavaScriptBeforeContentLoaded,
  onCaptionChange,
  onCaptionTimelineChange,
  onLoadEnd,
  onLoadStart,
  onMessage,
  onTranslationModeRequest,
  onTranslationStateChange,
  session,
  targetLocale,
  translationEngineId,
  translationEnabled,
  translationMode,
  translationProfile,
  readerPublishedAt,
  readerContent,
  onWebDocumentChange,
  onReaderSnapshotInvalidated,
  navigationCommand,
  translationPurpose,
  seekToMs,
  seekCommand,
  timelineWindowCommand,
  captionTimelineMode = 'original',
  visibleTimelineSegmentIds,
  ...webViewProps
}: TranslatableWebViewProps, webViewRef: RefObject<Pick<WebView, 'injectJavaScript'> | null>, bridgeName = '__readerTranslationBridge') {
  const { t } = useTranslation('reader');
  const interfaceLocale = i18n.resolvedLanguage ?? i18n.language;
  const { effectiveEngineFingerprint, refetch: refetchPreference } = useTranslationPreference(session, translationEnabled);
  const engineIdentity = effectiveEngineFingerprint ?? translationEngineId;
  const bridgeNameRef = useRef(bridgeName);
  const inspectionRef = useRef<{ nonce: string; onResult: (message: string) => void; timer: ReturnType<typeof setTimeout> } | null>(null);
  useEffect(() => () => {
    if (inspectionRef.current) clearTimeout(inspectionRef.current.timer);
    inspectionRef.current = null;
  }, []);
  const channelTokenRef = useRef(
    `reader-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
  );
  const navigationIdRef = useRef<string | null>(null);
  const mediaEpochRef = useRef<number | null>(null);
  const documentEpochRef = useRef<string | null>(null);
  const documentUrlRef = useRef<string | null>(null);
  const readerRevisionRef = useRef(0);
  const readerRequestRef = useRef<{ id: string; revision: number; document: WebDocumentIdentity; callback: (result: WebReaderResult) => void; timer: ReturnType<typeof setTimeout> } | null>(null);
  const readerSnapshotRef = useRef<{ id: string; revision: number } | null>(null);
  const readerMountedRef = useRef(true);
  const abandonReader = useCallback((reason: 'changed' | 'cancelled' | 'timeout' = 'changed') => {
    const request = readerRequestRef.current;
    if (!request) return;
    clearTimeout(request.timer);
    readerRequestRef.current = null;
    webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.cancelReader?.(${injectedValue(request.id)},${request.revision});true;`);
    request.callback({ ok: false, reason });
  }, [webViewRef]);
  const invalidateReaderSnapshot = useCallback(() => {
    const snapshot = readerSnapshotRef.current;
    if (!snapshot) return;
    readerSnapshotRef.current = null;
    webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.cancelReader?.(${injectedValue(snapshot.id)},${snapshot.revision});true;`);
    onReaderSnapshotInvalidated?.(snapshot.id);
  }, [onReaderSnapshotInvalidated, webViewRef]);
  const readerUserRef = useRef(session.user.id);
  useEffect(() => {
    if (readerUserRef.current === session.user.id) return;
    readerUserRef.current = session.user.id;
    abandonReader('changed');
    invalidateReaderSnapshot();
  }, [session.user.id, abandonReader, invalidateReaderSnapshot]);
  useEffect(() => {
    readerMountedRef.current = true;
    return () => {
      readerMountedRef.current = false;
      if (readerRequestRef.current) clearTimeout(readerRequestRef.current.timer);
      readerRequestRef.current = null;
      readerSnapshotRef.current = null;
    };
  }, []);
  const verifiedDocumentRef = useRef(false);
  const challengeRef = useRef<string | null>(null);
  const handshakeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [handshakeError, setHandshakeError] = useState(false);
  const [budgetExhausted, setBudgetExhausted] = useState(false);
  const acceptedTextRef = useRef(new Map<string, string>());
  const acceptedSourceTextRef = useRef(new Map<string, string>());
  const acceptedCharactersRef = useRef(0);
  const [bridgeState, setBridgeState] = useState('initializing');
  const [discoveredCount, setDiscoveredCount] = useState(0);
  const segmentEventRate = useRef({ since: 0, count: 0 });
  const failedSegments = useRef(new Set<string>());
  const [failedCount, setFailedCount] = useState(0);
  const displayMode = translationEnabled ? translationMode : 'original';
  const timelineMode = translationEnabled ? captionTimelineMode : 'original';
  const effectiveMode = displayMode !== 'original' ? displayMode : bridgeKind === 'youtube' ? timelineMode : 'original';

  useEffect(() => {
    if (bridgeKind !== 'web' || translationProfile === 'reader' || !navigationCommand) return;
    const { url, sequence } = navigationCommand;
    if (!Number.isSafeInteger(sequence) || !/^https?:\/\//i.test(url) || url.length > 4096) return;
    webViewRef.current?.injectJavaScript(`window.location.assign(${injectedValue(url)});true;`);
  }, [bridgeKind, navigationCommand, translationProfile, webViewRef]);

  useEffect(() => {
    if (!translationEnabled || effectiveMode === 'original') return;
    // Server model configuration may change without changing the managed engine ID.
    const timer = setInterval(() => { void refetchPreference(); }, 30_000);
    return () => clearInterval(timer);
  }, [translationEnabled, effectiveMode, refetchPreference]);

  const applyResults = useCallback((results: unknown[]) => {
    const mediaEpoch = bridgeKind === 'youtube' ? mediaEpochRef.current : null;
    if (bridgeKind === 'youtube' && mediaEpoch === null) return;
    const resultsWithSource = results.map((result) => {
      if (!result || typeof result !== 'object') return result;
      const segmentId = (result as { segment_id?: unknown }).segment_id;
      if (typeof segmentId !== 'string') return result;
      const sourceText = acceptedSourceTextRef.current.get(segmentId);
      return sourceText === undefined ? result : { ...result, source_text: sourceText };
    });
    const payload = injectedValue(resultsWithSource);
    const epoch = injectedValue(bridgeKind === 'youtube' ? mediaEpoch : documentEpochRef.current);
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.applyTranslations(${payload},${epoch});true;`,
    );
  }, [bridgeKind, webViewRef]);

  const {
    activeCount,
    enqueuePlan,
    error,
    reset,
    replaceWindow,
    forgetSegments,
    retryFailed,
    rejectResult,
    retryAt,
  } = useRealtimeTranslationScheduler({
    enabled: translationEnabled && effectiveMode !== 'original',
    engineIdentity,
    onResults: applyResults,
    session,
    targetLocale,
  });

  const resetBridgeState = useCallback(() => {
    failedSegments.current.clear();
    setFailedCount(0);
    acceptedTextRef.current.clear();
    acceptedSourceTextRef.current.clear();
    acceptedCharactersRef.current = 0;
    setBudgetExhausted(false);
    reset();
    setBridgeState('initializing');
    setDiscoveredCount(0);
    onCaptionChange?.(null);
    onCaptionTimelineChange?.({ captions: [], currentSegmentId: null });
  }, [onCaptionChange, onCaptionTimelineChange, reset]);

  const acceptMediaEpoch = useCallback((nextEpoch: number): boolean => {
    const currentEpoch = mediaEpochRef.current;
    if (currentEpoch !== null && nextEpoch < currentEpoch) return false;
    if (currentEpoch === nextEpoch) return true;
    mediaEpochRef.current = nextEpoch;
    // A new media epoch is a hard boundary. Reset before dispatching the
    // message that established it so no prior caption/result can leak across
    // videos or tracks.
    resetBridgeState();
    return true;
  }, [resetBridgeState]);

  const bootstrapScript = useMemo(() => {
    const config = {
      channelToken: channelTokenRef.current,
      initialMode: displayMode,
      initialTimelineMode: timelineMode,
      bridgeName: bridgeNameRef.current,
      profile: translationProfile,
      readerContent,
    };
    return bridgeKind === 'youtube'
      ? createYouTubeTranslationScript(config)
      : createWebTranslationScript(config);
  }, [bridgeKind, displayMode, timelineMode, translationProfile, readerContent]);

  // Android emits load-start for history updates too. Verify the runtime in
  // the live WebView instead of treating a URL or a replayed reset as identity.
  const requestHandshake = useCallback((restart = false) => {
    if (bridgeKind !== 'web' || (!restart && challengeRef.current)) return;
    if (handshakeTimerRef.current) clearTimeout(handshakeTimerRef.current);
    const nonce = `challenge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    challengeRef.current = nonce;
    verifiedDocumentRef.current = false;
    setHandshakeError(false);
    setBridgeState('initializing');
    recordTranslationDiagnostic('handshake_waiting', { stage: 'bridge', documentEpoch: documentEpochRef.current, runtimeVersion: WEB_TRANSLATION_RUNTIME_VERSION });
    console.debug('[web-translation-bridge]', { event: 'handshake_waiting' });
    let attempts = 0;
    const probe = () => {
      if (challengeRef.current !== nonce) return;
      if (attempts++ >= 5) {
        challengeRef.current = null;
        handshakeTimerRef.current = null;
        recordTranslationDiagnostic('handshake_timeout', { stage: 'bridge', attempt: 5 });
        console.debug('[web-translation-bridge]', { event: 'handshake_timeout', attempts: 5 });
        setBridgeState('bridge_timeout');
        setHandshakeError(true);
        return;
      }
      webViewRef.current?.injectJavaScript(
        `window.${bridgeNameRef.current}?.reconnect?.(${injectedValue(nonce)});true;`,
      );
      handshakeTimerRef.current = setTimeout(probe, 1_000);
    };
    probe();
  }, [bridgeKind, webViewRef]);

  const retryHandshake = useCallback(() => {
    webViewRef.current?.injectJavaScript(bootstrapScript);
    requestHandshake(true);
  }, [bootstrapScript, requestHandshake, webViewRef]);

  useEffect(() => () => {
    challengeRef.current = null;
    if (handshakeTimerRef.current) clearTimeout(handshakeTimerRef.current);
  }, []);

  const interfaceScript = useMemo(() => {
    // Locale is an explicit dependency: this script only changes app-owned copy.
    if (!interfaceLocale) return undefined;
    return joinScripts(
      bridgeKind === 'youtube'
        ? `window.${bridgeNameRef.current}?.setInterfaceMessages?.(${injectedValue(getYouTubeInterfaceMessages())});true;`
        : undefined,
      translationProfile === 'reader' && readerPublishedAt
        ? createReaderInterfaceScript(readerPublishedAt)
        : undefined,
    );
  }, [bridgeKind, interfaceLocale, readerPublishedAt, translationProfile]);

  useEffect(() => {
    if (interfaceScript) webViewRef.current?.injectJavaScript(interfaceScript);
  }, [interfaceScript, webViewRef]);

  const afterContentScript = useMemo(
    () => joinScripts(bootstrapScript, interfaceScript, injectedJavaScript),
    [bootstrapScript, interfaceScript, injectedJavaScript],
  );
  const beforeContentScript = useMemo(
    () => joinScripts(
      bridgeKind === 'youtube' ? bootstrapScript : undefined,
      interfaceScript,
      injectedJavaScriptBeforeContentLoaded,
    ),
    [bootstrapScript, bridgeKind, interfaceScript, injectedJavaScriptBeforeContentLoaded],
  );

  useEffect(() => {
    if (effectiveMode === 'original') {
      acceptedTextRef.current.clear();
      acceptedSourceTextRef.current.clear();
      acceptedCharactersRef.current = 0;
      setBudgetExhausted(false);
    }
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.setMode(${injectedValue(displayMode)});window.${bridgeNameRef.current}?.setTimelineMode?.(${injectedValue(timelineMode)});true;`,
    );
    if (effectiveMode !== 'original') requestHandshake(true);
  }, [displayMode, timelineMode, effectiveMode, requestHandshake, webViewRef]);

  useEffect(() => {
    acceptedTextRef.current.clear();
    acceptedSourceTextRef.current.clear();
    acceptedCharactersRef.current = 0;
    setBudgetExhausted(false);
    reset();
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.resetTranslations();true;`,
    );
  }, [reset, targetLocale, translationEnabled, translationEngineId, engineIdentity, session.user.id, webViewRef]);

  useEffect(() => {
    const positionMs = seekCommand?.positionMs ?? seekToMs;
    if (bridgeKind !== 'youtube' || positionMs === null || positionMs === undefined) return;
    if (!Number.isFinite(positionMs)) return;
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.seekTo(${Math.max(0, positionMs)});true;`,
    );
  }, [bridgeKind, seekToMs, seekCommand, webViewRef]);

  useEffect(() => {
    if (bridgeKind !== 'youtube' || mediaEpochRef.current === null) return;
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.setTimelineVisibleSegments?.(${injectedValue(visibleTimelineSegmentIds ?? [])},${mediaEpochRef.current});true;`,
    );
  }, [bridgeKind, visibleTimelineSegmentIds, webViewRef]);

  useEffect(() => {
    if (bridgeKind !== 'youtube' || !timelineWindowCommand || mediaEpochRef.current === null) return;
    webViewRef.current?.injectJavaScript(
      `window.${bridgeNameRef.current}?.setTimelineWindow?.(${injectedValue(timelineWindowCommand.direction)},${injectedValue(timelineWindowCommand.firstId)},${mediaEpochRef.current});true;`,
    );
  }, [bridgeKind, timelineWindowCommand, webViewRef]);

  const retryFailedParagraphs = useCallback(() => {
    retryFailed();
    failedSegments.current.clear();
    setFailedCount(0);
    webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.retryFailed?.();true;`);
  }, [retryFailed, webViewRef]);
  const exportDiagnostics = useCallback(() => {
    recordTranslationDiagnostic('state_snapshot', { stage: 'native', failedCount,
      detectedCount: discoveredCount, queuedCount: activeCount, status: bridgeState,
      documentEpoch: documentEpochRef.current, runtimeVersion: WEB_TRANSLATION_RUNTIME_VERSION });
    return exportTranslationDiagnostics();
  }, [activeCount, bridgeState, discoveredCount, failedCount]);
  const inspectSelection = useCallback((onResult: (message: string) => void) => {
    if (inspectionRef.current) clearTimeout(inspectionRef.current.timer);
    const nonce = `selection-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const timer = setTimeout(() => {
      if (inspectionRef.current?.nonce !== nonce) return;
      inspectionRef.current = null;
      onResult(i18n.t('reader:inspectionTimeout'));
    }, 2_000);
    inspectionRef.current = { nonce, onResult, timer };
    webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.diagnoseSelection?.(${injectedValue(nonce)});true;`);
  }, [webViewRef]);

  const extractReader = useCallback((callback: (result: WebReaderResult) => void) => {
    if (!readerMountedRef.current || readerUserRef.current !== session.user.id) {
      callback({ ok: false, reason: 'cancelled' });
      return () => {};
    }
    abandonReader('cancelled');
    if (!verifiedDocumentRef.current || !navigationIdRef.current || !documentEpochRef.current || !documentUrlRef.current) {
      callback({ ok: false, reason: 'unavailable' });
      retryHandshake();
      return () => {};
    }
    const id = `reader-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const revision = ++readerRevisionRef.current;
    const currentDocument = { navigationId: navigationIdRef.current, epoch: documentEpochRef.current, url: documentUrlRef.current };
    const timer = setTimeout(() => { if (readerRequestRef.current?.id === id) abandonReader('timeout'); }, 5_000);
    readerRequestRef.current = { id, revision, document: currentDocument, callback, timer };
    webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.extractReader?.(${injectedValue(id)},${injectedValue(currentDocument.epoch)},${revision});true;`);
    return () => {
      if (readerRequestRef.current?.id === id) abandonReader('cancelled');
      if (readerSnapshotRef.current?.id === id) readerSnapshotRef.current = null;
      webViewRef.current?.injectJavaScript(`window.${bridgeNameRef.current}?.cancelReader?.(${injectedValue(id)},${revision});true;`);
    };
  }, [abandonReader, retryHandshake, session.user.id, webViewRef]);

  useEffect(() => {
    onTranslationStateChange?.({
      activeCount,
      bridgeState,
      discoveredCount,
      error: handshakeError ? t('bridgeTimeout') : retryAt ? t('retryScheduled', { time: new Date(retryAt).toLocaleTimeString(interfaceLocale) }) : failedCount ? t('segmentsFailed', { count: failedCount }) : error ?? (budgetExhausted ? t('translationLimit') : null),
      retry: handshakeError ? retryHandshake : !retryAt && (failedCount || error) ? retryFailedParagraphs : undefined,
      failedCount,
      exportDiagnostics,
      inspectSelection: bridgeKind === 'web' ? inspectSelection : undefined,
      extractReader: bridgeKind === 'web' && translationProfile !== 'reader' ? extractReader : undefined,
    });
  }, [activeCount, bridgeState, discoveredCount, error, budgetExhausted, handshakeError, onTranslationStateChange, retryHandshake, failedCount, retryFailedParagraphs, exportDiagnostics, bridgeKind, inspectSelection, retryAt, t, interfaceLocale, extractReader, translationProfile]);

  const handleLoadStart: NonNullable<WebViewProps['onLoadStart']> = useCallback((event) => {
    if (bridgeKind === 'web') {
      abandonReader('changed');
      // Keep accepted work until a live challenge proves the document changed.
      console.debug('[web-translation-bridge]', { event: 'load_start', hadDocument: navigationIdRef.current !== null });
      requestHandshake(true);
    } else {
      navigationIdRef.current = null;
      mediaEpochRef.current = null;
      documentEpochRef.current = null;
      resetBridgeState();
    }
    onLoadStart?.(event);
  }, [bridgeKind, onLoadStart, requestHandshake, resetBridgeState, abandonReader]);

  const handleLoadEnd: NonNullable<WebViewProps['onLoadEnd']> = useCallback((event) => {
    // Android's document-start hook can run before the messaging bridge settles.
    // Re-running the idempotent bootstrap reconnects and replays visible work.
    webViewRef.current?.injectJavaScript(joinScripts(bootstrapScript, interfaceScript) ?? bootstrapScript);
    requestHandshake(true);
    onLoadEnd?.(event);
  }, [bootstrapScript, interfaceScript, onLoadEnd, requestHandshake, webViewRef]);

  const handleMessage = useCallback((event: WebViewMessageEvent) => {
    const rawMessage = event.nativeEvent.data;
    if (typeof rawMessage !== 'string' || rawMessage.length > MAX_WEB_READER_BYTES) {
      onMessage?.(event);
      return;
    }
    let envelope: BridgeEnvelope;
    try {
      envelope = JSON.parse(rawMessage) as BridgeEnvelope;
    } catch {
      onMessage?.(event);
      return;
    }
    if (!envelope || typeof envelope !== 'object') return;
    if (envelope.type === 'reader_extraction_result'
      ? utf8ByteLength(rawMessage) > MAX_WEB_READER_BYTES
      : rawMessage.length > MAX_BRIDGE_MESSAGE_CHARS) return;
    if (
      envelope.channel_token !== channelTokenRef.current ||
      typeof envelope.navigation_id !== 'string' ||
      envelope.navigation_id.length < 8 ||
      envelope.navigation_id.length > 120
    ) {
      onMessage?.(event);
      return;
    }
    if (bridgeKind === 'youtube') {
      const mediaEpoch = envelope.media_epoch;
      if (typeof mediaEpoch !== 'number' || !Number.isSafeInteger(mediaEpoch) || mediaEpoch < 0) return;
      if (!acceptMediaEpoch(mediaEpoch)) return;
    }
    if (bridgeKind === 'web') {
      if (
        typeof envelope.document_epoch !== 'string' ||
        envelope.document_epoch.length < 8 ||
        envelope.document_epoch.length > 120
      ) return;
      if (envelope.type === 'reader_translation_navigation_reset') {
        requestHandshake(navigationIdRef.current !== envelope.navigation_id ||
          documentEpochRef.current !== envelope.document_epoch);
        return;
      }
      // Invalidation only removes an already known snapshot/request. Accept it
      // during a same-document handshake too, without authorizing new content.
      if (envelope.type === 'reader_snapshot_invalidated') {
        if (navigationIdRef.current !== envelope.navigation_id || documentEpochRef.current !== envelope.document_epoch ||
          typeof envelope.request_id !== 'string') return;
        if (readerRequestRef.current?.id === envelope.request_id) abandonReader('changed');
        if (readerSnapshotRef.current?.id === envelope.request_id) invalidateReaderSnapshot();
        return;
      }
      if (envelope.type === 'reader_translation_handshake') {
        if (!challengeRef.current || envelope.challenge_nonce !== challengeRef.current) {
          recordTranslationDiagnostic('handshake_rejected', { stage: 'bridge', reason: 'stale_challenge' });
          console.debug('[web-translation-bridge]', { event: 'handshake_rejected', reason: 'stale_challenge' });
          return;
        }
        const changed = navigationIdRef.current !== envelope.navigation_id ||
          documentEpochRef.current !== envelope.document_epoch;
        console.debug('[web-translation-bridge]', { event: 'live_document', changed });
        navigationIdRef.current = envelope.navigation_id;
        if (typeof envelope.document_url === 'string' && /^https?:\/\//i.test(envelope.document_url) && envelope.document_url.length <= 4096) {
          documentUrlRef.current = envelope.document_url;
        } else documentUrlRef.current = null;
        if (changed) {
          abandonReader('changed');
          invalidateReaderSnapshot();
          failedSegments.current.clear();
          setFailedCount(0);
          documentEpochRef.current = envelope.document_epoch;
          acceptedTextRef.current.clear();
          acceptedSourceTextRef.current.clear();
          acceptedCharactersRef.current = 0;
          setBudgetExhausted(false);
          reset();
          setBridgeState('initializing');
          setDiscoveredCount(0);
        }
        webViewRef.current?.injectJavaScript(
          `window.${bridgeNameRef.current}?.confirmEpoch(${injectedValue(envelope.document_epoch)},${injectedValue(envelope.challenge_nonce)});true;`,
        );
        return;
      }
      if (envelope.type === 'reader_translation_handshake_ready') {
        if (!challengeRef.current || envelope.challenge_nonce !== challengeRef.current ||
          navigationIdRef.current !== envelope.navigation_id ||
          documentEpochRef.current !== envelope.document_epoch) return;
        challengeRef.current = null;
        if (handshakeTimerRef.current) clearTimeout(handshakeTimerRef.current);
        handshakeTimerRef.current = null;
        verifiedDocumentRef.current = true;
        // An anchor can change after the proof while location notifications are
        // paused by this handshake. Ready describes the confirmed current URL.
        documentUrlRef.current = typeof envelope.document_url === 'string' &&
          /^https?:\/\//i.test(envelope.document_url) && envelope.document_url.length <= 4096
          ? envelope.document_url : null;
        if (documentUrlRef.current) onWebDocumentChange?.({ navigationId: navigationIdRef.current!, epoch: documentEpochRef.current!, url: documentUrlRef.current });
        recordTranslationDiagnostic('handshake_ready', { stage: 'bridge', documentEpoch: documentEpochRef.current });
        console.debug('[web-translation-bridge]', { event: 'handshake_ready' });
        setHandshakeError(false);
        return;
      }
      if (
        !verifiedDocumentRef.current ||
        navigationIdRef.current !== envelope.navigation_id ||
        documentEpochRef.current !== envelope.document_epoch
      ) {
        recordTranslationDiagnostic('message_discarded', { stage: 'bridge', reason: 'obsolete_epoch', documentEpoch: documentEpochRef.current });
        return;
      }
    } else {
      if (navigationIdRef.current === null) navigationIdRef.current = envelope.navigation_id;
      if (navigationIdRef.current !== envelope.navigation_id) return;
    }

    if (bridgeKind === 'web' && envelope.type === 'reader_translation_location_changed') {
      if (typeof envelope.document_url !== 'string' || !/^https?:\/\//i.test(envelope.document_url) || envelope.document_url.length > 4096) return;
      documentUrlRef.current = envelope.document_url;
      onWebDocumentChange?.({ navigationId: navigationIdRef.current!, epoch: documentEpochRef.current!, url: envelope.document_url });
      return;
    }

    if (bridgeKind === 'web' && envelope.type === 'reader_extraction_result') {
      const request = readerRequestRef.current;
      if (!request || request.id !== envelope.request_id || request.document.navigationId !== envelope.navigation_id ||
        request.document.epoch !== envelope.document_epoch || typeof envelope.document_url !== 'string' ||
        request.document.url.split('#')[0] !== envelope.document_url.split('#')[0]) return;
      clearTimeout(request.timer);
      readerRequestRef.current = null;
      if (isWebReaderArticle(envelope.article, envelope.document_url)) {
        readerSnapshotRef.current = { id: request.id, revision: request.revision };
        request.callback({ ok: true, requestId: request.id, document: { ...request.document, url: envelope.document_url }, article: envelope.article });
      } else {
        request.callback({ ok: false, reason: envelope.error === 'too_large' || envelope.error === 'changed' ? envelope.error : 'unavailable' });
      }
      return;
    }

    if (envelope.type === 'reader_translation_mode_request') {
      if (
        bridgeKind === 'youtube' &&
        (envelope.mode === 'original' || envelope.mode === 'bilingual')
      ) {
        onTranslationModeRequest?.(envelope.mode);
      }
      return;
    }

    if (envelope.type === 'reader_translation_caption_timeline') {
      if (!Array.isArray(envelope.captions)) return;
      const captions: SynchronizedCaption[] = [];
      let characterCount = 0;
      for (const raw of envelope.captions.slice(0, MAX_TIMELINE_CUES)) {
        if (!raw || typeof raw !== 'object') continue;
        const candidate = raw as {
          end_ms?: unknown;
          segment_id?: unknown;
          source_text?: unknown;
          start_ms?: unknown;
          translated_text?: unknown;
        };
        const startMs = typeof candidate.start_ms === 'number' ? candidate.start_ms : NaN;
        const endMs = typeof candidate.end_ms === 'number' ? candidate.end_ms : NaN;
        const sourceText = typeof candidate.source_text === 'string'
          ? candidate.source_text.trim()
          : '';
        const translatedText = candidate.translated_text === null
          ? null
          : typeof candidate.translated_text === 'string'
            ? candidate.translated_text.trim()
            : undefined;
        if (
          typeof candidate.segment_id !== 'string' ||
          !candidate.segment_id.includes(envelope.navigation_id) ||
          candidate.segment_id.length > 160 ||
          !sourceText ||
          sourceText.length > MAX_SEGMENT_CHARS ||
          translatedText === undefined ||
          (translatedText !== null && translatedText.length > MAX_SEGMENT_CHARS) ||
          !Number.isFinite(startMs) ||
          !Number.isFinite(endMs) ||
          startMs < 0 ||
          endMs < startMs
        ) continue;
        const nextCharacterCount = characterCount + sourceText.length + (translatedText?.length ?? 0);
        if (nextCharacterCount > MAX_TIMELINE_CHARACTERS) break;
        characterCount = nextCharacterCount;
        captions.push({
          endMs,
          segmentId: candidate.segment_id,
          startMs,
          sourceText,
          translatedText: translatedText || null,
        });
      }
      const requestedCurrentId = typeof envelope.current_segment_id === 'string'
        && envelope.current_segment_id.includes(envelope.navigation_id)
        && envelope.current_segment_id.length <= 160
        ? envelope.current_segment_id
        : null;
      onCaptionTimelineChange?.({
        captions,
        hasPrevious: envelope.has_previous === true,
        hasNext: envelope.has_next === true,
        currentSegmentId: captions.some((caption) => caption.segmentId === requestedCurrentId)
          ? requestedCurrentId
          : null,
      });
      return;
    }

    if (envelope.type === 'reader_translation_caption') {
      if (
        envelope.segment_id === null &&
        envelope.source_text === null &&
        envelope.translated_text === null
      ) {
        onCaptionChange?.(null);
        return;
      }
      if (
        typeof envelope.segment_id === 'string' &&
        envelope.segment_id.includes(envelope.navigation_id) &&
        envelope.segment_id.length <= 160 &&
        typeof envelope.source_text === 'string' &&
        envelope.source_text.length <= MAX_SEGMENT_CHARS &&
        (envelope.translated_text === null || typeof envelope.translated_text === 'string') &&
        (typeof envelope.translated_text !== 'string' || envelope.translated_text.length <= MAX_SEGMENT_CHARS)
      ) {
        const startMs = typeof envelope.start_ms === 'number' && Number.isFinite(envelope.start_ms)
          ? Math.max(0, envelope.start_ms)
          : 0;
        const endMs = typeof envelope.end_ms === 'number' && Number.isFinite(envelope.end_ms)
          ? Math.max(startMs, envelope.end_ms)
          : startMs;
        onCaptionChange?.({
          endMs,
          segmentId: envelope.segment_id,
          startMs,
          sourceText: envelope.source_text,
          translatedText: typeof envelope.translated_text === 'string'
            ? envelope.translated_text
            : null,
        });
      }
      return;
    }
    if (envelope.type === 'reader_translation_selection_diagnostic') {
      const inspection = inspectionRef.current;
      const descriptions: Record<string, string> = {
        no_selection: i18n.t('reader:inspectNoSelection'),
        excluded: i18n.t('reader:inspectExcluded'), outside_scope: i18n.t('reader:inspectOutsideScope'),
        preformatted: i18n.t('reader:inspectPreformatted'), not_discovered: i18n.t('reader:inspectNotDiscovered'),
        discovered: i18n.t('reader:inspectDiscovered'), queued: i18n.t('reader:inspectQueued'),
        translated: i18n.t('reader:inspectTranslated'), failed: i18n.t('reader:inspectFailed'),
        stale: i18n.t('reader:inspectStale'), disposed: i18n.t('reader:inspectDisposed'),
      };
      const displayDescriptions: Record<string, string> = {
        not_inserted: i18n.t('reader:displayNotInserted'), mode_hidden: i18n.t('reader:displayModeHidden'),
        host_hidden: i18n.t('reader:displayHostHidden'), clipped: i18n.t('reader:displayClipped'),
        offscreen: i18n.t('reader:displayOffscreen'), partial: i18n.t('reader:displayPartial'),
        visible: i18n.t('reader:displayVisible'),
        unknown: i18n.t('reader:displayUnknown'),
      };
      if (bridgeKind !== 'web' || !inspection || envelope.challenge_nonce !== inspection.nonce ||
          typeof envelope.reason !== 'string' || !Object.hasOwn(descriptions, envelope.reason) ||
          (envelope.scope_id !== undefined && (typeof envelope.scope_id !== 'string' || !/^(main|scope-[1-9][0-9]{0,8})$/.test(envelope.scope_id))) ||
          (envelope.display_status !== undefined && (typeof envelope.display_status !== 'string' || !Object.hasOwn(displayDescriptions, envelope.display_status))) ||
          !Number.isInteger(envelope.rule_index) || Number(envelope.rule_index) < -1 || Number(envelope.rule_index) > 255 ||
          typeof envelope.tag_name !== 'string' || !/^[A-Z0-9-]{0,32}$/.test(envelope.tag_name) ||
          typeof envelope.profile_id !== 'string' || !/^[a-z0-9-]{1,64}$/.test(envelope.profile_id)) return;
      clearTimeout(inspection.timer);
      inspectionRef.current = null;
      recordTranslationDiagnostic('selection_diagnostic', { stage: 'dom', reason: envelope.reason,
        displayStatus: envelope.display_status, profileId: envelope.profile_id, ruleIndex: envelope.rule_index, tagName: envelope.tag_name,
        segmentId: typeof envelope.segment_id === 'string' && envelope.segment_id.length <= 160 ? envelope.segment_id : undefined,
        documentEpoch: documentEpochRef.current, scopeId: envelope.scope_id ?? 'main' });
      inspection.onResult(descriptions[envelope.reason] + (typeof envelope.display_status === 'string' ? i18n.t('reader:inspectionDisplay', { description: displayDescriptions[envelope.display_status] }) : '') + (envelope.reason === 'excluded'
        ? i18n.t('reader:inspectionRule', { profile: envelope.profile_id, index: envelope.rule_index }) : ''));
      return;
    }
    if (envelope.type === 'reader_translation_scope_disposed') {
      if (bridgeKind !== 'web' || typeof envelope.scope_id !== 'string' || !/^scope-[1-9][0-9]{0,8}$/.test(envelope.scope_id)) return;
      const prefix = `${navigationIdRef.current}:${documentEpochRef.current}:${envelope.scope_id}:`;
      const removed: string[] = [];
      for (const [segmentId, source] of acceptedSourceTextRef.current) {
        if (!segmentId.startsWith(prefix)) continue;
        removed.push(segmentId);
        acceptedCharactersRef.current = Math.max(0, acceptedCharactersRef.current - source.length);
        acceptedSourceTextRef.current.delete(segmentId);
        acceptedTextRef.current.delete(segmentId);
        failedSegments.current.delete(segmentId);
      }
      if (!removed.length) return;
      forgetSegments(removed);
      setFailedCount(failedSegments.current.size);
      recordTranslationDiagnostic('scope_disposed', { stage: 'bridge', scopeId: envelope.scope_id, count: removed.length, documentEpoch: documentEpochRef.current });
      return;
    }
    if (envelope.type === 'reader_translation_segment_event') {
      if (bridgeKind !== 'web' || typeof envelope.segment_id !== 'string' ||
          !acceptedSourceTextRef.current.has(envelope.segment_id) ||
          typeof envelope.reason !== 'string' || !/^[a-z_]{1,64}$/.test(envelope.reason) ||
          !['failed', 'translated', 'disposed'].includes(String(envelope.state))) return;
      const segmentId = envelope.segment_id;
      // Limit diagnostic volume, never delivery of accepted request state.
      const rate = segmentEventRate.current;
      if (Date.now() - rate.since >= 1000) { rate.since = Date.now(); rate.count = 0; }
      if (++rate.count <= 100) recordTranslationDiagnostic('segment_render', { stage: 'dom', segmentId,
        level: envelope.state === 'translated' && !failedSegments.current.has(segmentId) ? 'debug' : 'info',
        documentEpoch: documentEpochRef.current, reason: envelope.reason, status: envelope.state });
      if (envelope.state === 'failed') {
        failedSegments.current.add(segmentId);
        if (envelope.reason === 'placeholder_mismatch') rejectResult(segmentId);
      } else failedSegments.current.delete(segmentId);
      setFailedCount((count) => count === failedSegments.current.size ? count : failedSegments.current.size);
      return;
    }
    if (envelope.type === 'reader_translation_engine_diagnostics') {
      const finiteCount = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0;
      const failureEntries = envelope.render_failures && typeof envelope.render_failures === 'object' && !Array.isArray(envelope.render_failures)
        ? Object.entries(envelope.render_failures)
        : [];
      const validFailures = failureEntries.length <= 32 && failureEntries.every(([reason, count]) =>
        reason.length > 0 && reason.length <= 64 && /^[a-z0-9_-]+$/i.test(reason) && finiteCount(count));
      const validTruncatedReason = envelope.truncated_reason === undefined ||
        envelope.truncated_reason === 'node_limit' ||
        envelope.truncated_reason === 'segment_limit' ||
        envelope.truncated_reason === 'disposed';
      if (
        bridgeKind === 'web' &&
        typeof envelope.profile_id === 'string' && envelope.profile_id.length <= 64 &&
        envelope.scope_id === 'main' &&
        [envelope.traversal_ms, envelope.tree_nodes, envelope.style_reads, envelope.rect_reads,
          envelope.paragraphs_detected, envelope.paragraphs_filtered, envelope.paragraphs_queued,
          envelope.pending_roots, envelope.dirty_roots, envelope.deferred_roots,
          envelope.processing_roots].every(finiteCount) &&
        validFailures && validTruncatedReason
      ) {
        recordTranslationDiagnostic('engine_summary', { stage: 'dom', profileId: envelope.profile_id,
          detectedCount: envelope.paragraphs_detected, queuedCount: envelope.paragraphs_queued,
          documentEpoch: documentEpochRef.current, runtimeVersion: WEB_TRANSLATION_RUNTIME_VERSION, formatVersion: 'reader-rich-text-v1' });
        console.debug('[web-translation-engine]', {
          dirtyRoots: envelope.dirty_roots,
          deferredRoots: envelope.deferred_roots,
          paragraphsDetected: envelope.paragraphs_detected,
          paragraphsQueued: envelope.paragraphs_queued,
          pendingRoots: envelope.pending_roots,
          processingRoots: envelope.processing_roots,
          profileId: envelope.profile_id,
          renderFailures: envelope.render_failures,
          traversalMs: envelope.traversal_ms,
          truncatedReason: envelope.truncated_reason,
        });
      }
      return;
    }
    if (envelope.type === 'reader_translation_status') {
      if (bridgeKind === 'web' && envelope.state === 'budget_exhausted') setBudgetExhausted(true);
      if (typeof envelope.state === 'string' && envelope.state.length <= 80) {
        setBridgeState(envelope.state);
      }
      if (typeof envelope.count === 'number' && Number.isFinite(envelope.count)) {
        setDiscoveredCount(Math.max(0, Math.min(Math.round(envelope.count), 100_000)));
      }
      return;
    }
    if (
      envelope.type !== 'reader_translation_batch_plan' ||
      effectiveMode === 'original' ||
      !Array.isArray(envelope.batches)
    ) {
      onMessage?.(event);
      return;
    }

    const accepted: RealtimeTranslationBatch[] = [];
    for (const rawBatch of envelope.batches.slice(0, 20)) {
      if (!rawBatch || typeof rawBatch !== 'object') continue;
      const candidateBatch = rawBatch as { id?: unknown; priority?: unknown; segments?: unknown; window_id?: unknown };
      if (typeof candidateBatch.id !== 'string' || typeof candidateBatch.window_id !== 'string' ||
        (candidateBatch.priority !== 'urgent' && candidateBatch.priority !== 'prefetch') || !Array.isArray(candidateBatch.segments)) continue;
      const segments: TranslationSegment[] = [];
      for (const raw of candidateBatch.segments.slice(0, 5)) {
      if (!raw || typeof raw !== 'object') continue;
      const candidate = raw as {
        segment_id?: unknown;
        text?: unknown;
      };
      if (
        typeof candidate.segment_id !== 'string' ||
        typeof candidate.text !== 'string' ||
        !candidate.segment_id.includes(envelope.navigation_id) ||
        (bridgeKind === 'web' && !candidate.segment_id.includes(documentEpochRef.current ?? '')) ||
        candidate.segment_id.length > 160
      ) continue;
      const text = candidate.text.trim();
      if (!text || text.length > MAX_SEGMENT_CHARS) continue;
      const signature = text;
      const knownSignature = acceptedTextRef.current.get(candidate.segment_id);
      if (!knownSignature && acceptedTextRef.current.size >= MAX_SEGMENTS_PER_DOCUMENT) continue;
      const acceptedCharacters = text.length;
      const isNewSource = knownSignature !== signature;
      if (isNewSource && acceptedCharactersRef.current + acceptedCharacters - (knownSignature?.length ?? 0) > MAX_ACCEPTED_CHARACTERS_PER_DOCUMENT) {
        continue;
      }
      if (isNewSource) acceptedCharactersRef.current += acceptedCharacters - (knownSignature?.length ?? 0);
      acceptedTextRef.current.set(candidate.segment_id, signature);
      acceptedSourceTextRef.current.set(candidate.segment_id, text);
      segments.push({
        purpose: translationPurpose,
        segment_id: candidate.segment_id,
        text,
      });
    }
      if (segments.length) accepted.push({ id: candidateBatch.id, priority: candidateBatch.priority, segments, windowId: candidateBatch.window_id });
    }
    if (accepted.length) {
      replaceWindow(accepted[0].windowId);
      enqueuePlan(accepted);
    }
  }, [acceptMediaEpoch, bridgeKind, effectiveMode, enqueuePlan, onCaptionChange, onCaptionTimelineChange, onMessage, onTranslationModeRequest, replaceWindow, requestHandshake, reset, translationPurpose, rejectResult, forgetSegments, webViewRef, abandonReader, onWebDocumentChange, invalidateReaderSnapshot]);

  return {
    ...webViewProps,
    injectedJavaScript: afterContentScript,
    injectedJavaScriptBeforeContentLoaded: beforeContentScript,
    onLoadEnd: handleLoadEnd,
    onLoadStart: handleLoadStart,
    onMessage: handleMessage,
  };
}

/** Independent, token-isolated page and caption controllers share one native WebView. */
export function TranslatableWebView({
  youtubePage = false,
  playerTranslationMode = 'original',
  onPlayerTranslationModeRequest,
  ...props
}: TranslatableWebViewProps) {
  const webViewRef = useRef<WebView>(null);
  const captionRef = useMemo(() => ({
    get current() {
      if (!youtubePage || !webViewRef.current) return null;
      return webViewRef.current;
    },
  }), [youtubePage]);
  const captions = useTranslationBridge({
    bridgeKind: 'youtube',
    session: props.session,
    targetLocale: props.targetLocale,
    translationEngineId: props.translationEngineId,
    translationEnabled: youtubePage && props.translationEnabled,
    translationMode: playerTranslationMode,
    translationPurpose: 'caption',
    onTranslationModeRequest: onPlayerTranslationModeRequest,
  }, captionRef, '__readerCaptionBridge');
  const primary = useTranslationBridge(props, webViewRef);
  const captionScript = (script?: string) => youtubePage ? script : undefined;
  return <WebView
    {...primary}
    ref={webViewRef}
    injectedJavaScript={joinScripts(primary.injectedJavaScript, captionScript(captions.injectedJavaScript))}
    injectedJavaScriptBeforeContentLoaded={joinScripts(primary.injectedJavaScriptBeforeContentLoaded, captionScript(captions.injectedJavaScriptBeforeContentLoaded))}
    onLoadStart={(event) => { primary.onLoadStart(event); if (youtubePage) captions.onLoadStart(event); }}
    onLoadEnd={(event) => { primary.onLoadEnd(event); if (youtubePage) captions.onLoadEnd(event); }}
    onMessage={(event) => { primary.onMessage(event); if (youtubePage) captions.onMessage(event); }}
  />;
}
