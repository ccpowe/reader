import type { TranslationDisplayMode } from './webTranslation';
import { YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE } from './translationRuntimeSources.generated';
import { i18n } from '../i18n';

type YouTubeInterfaceMessages = {
  disableBilingualCaptions: string;
  enableBilingualCaptions: string;
  bilingual: string;
};

export function getYouTubeInterfaceMessages(): YouTubeInterfaceMessages {
  return {
    disableBilingualCaptions: i18n.t('reader:disableBilingualCaptions'),
    enableBilingualCaptions: i18n.t('reader:enableBilingualCaptions'),
    bilingual: i18n.t('reader:bilingual'),
  };
}

type YouTubeTranslationBootstrapConfig = {
  channelToken: string;
  bridgeName?: string;
  initialMode: TranslationDisplayMode;
  initialTimelineMode?: TranslationDisplayMode;
  interfaceMessages?: YouTubeInterfaceMessages;
};

type CaptionCue = {
  endMs: number;
  startMs: number;
  text: string;
};

type CaptionGroup = CaptionCue & {
  id: string;
};

type YouTubeTranslationWindow = Window & {
  ReactNativeWebView?: { postMessage: (message: string) => void };
  __readerTranslationBridge?: {
    applyTranslations: (results: unknown[], mediaEpoch?: number) => void;
    cleanup?: () => void;
    kind: string;
    refresh?: () => void;
    resetTranslations: () => void;
    retryFailed?: () => void;
    seekTo?: (timeMs: number) => void;
    setMode: (mode: TranslationDisplayMode) => void;
    setInterfaceMessages?: (messages: YouTubeInterfaceMessages) => void;
    setTimelineWindow?: (direction: 'previous' | 'next', firstId: string, epoch: number) => void;
    setTimelineMode?: (mode: TranslationDisplayMode) => void;
    setTimelineVisibleSegments?: (ids: string[], epoch: number) => void;
  };
};

/** Build timed-text interception and a synchronized native-caption bridge. */
export function createYouTubeTranslationScript(
  config: YouTubeTranslationBootstrapConfig,
): string {
  const payload = JSON.stringify({ ...config, interfaceMessages: config.interfaceMessages ?? getYouTubeInterfaceMessages() })
    .replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  return `(${YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE})(${payload});true;`;
}

// This typed function is the source of the generated, Hermes-safe runtime string.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function youTubeTranslationBootstrap(config: YouTubeTranslationBootstrapConfig) {
  const bridgeWindow = window as YouTubeTranslationWindow;
  // Namespace is configuration, never a text replacement over caption result payloads.
  const bridgeName = config.bridgeName === '__readerCaptionBridge' ? '__readerCaptionBridge' : '__readerTranslationBridge';
  const bridgeSlots = bridgeWindow as unknown as Record<string, YouTubeTranslationWindow['__readerTranslationBridge']>;
  const existing = bridgeSlots[bridgeName];
  if (existing?.kind === 'youtube') {
    if (config.interfaceMessages) existing.setInterfaceMessages?.(config.interfaceMessages);
    existing.setMode(config.initialMode);
    existing.setTimelineMode?.(config.initialTimelineMode ?? 'original');
    existing.refresh?.();
    return;
  }

  const navigationId = `youtube-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  const translations = new Map<string, string>();
  const translationExpiresAt = new Map<string, number>();
  let captionWindowEpoch = 0;
  let lastRequestedIndex = -1;
  let lastWindowSignature = '';
  let timelineStart = 0;
  let timelineEnd = 0;
  let lastTimelineCurrentId: string | null = null;
  const inspectedResourceUrls = new Set<string>();
  const xhrRequests = new WeakMap<XMLHttpRequest, { epoch: number; url: string }>();
  let mode = config.initialMode;
  let interfaceMessages = config.interfaceMessages;
  let timelineMode = config.initialTimelineMode ?? 'original';
  let visibleTimelineSegmentIds: string[] = [];
  let mediaEpoch = 0;
  let mediaIdentity = '';
  let mediaSettlingUntil = 0;
  let observedTimedTextVideoId = '';
  let lastLocationKey = '';
  let groups: CaptionGroup[] = [];
  let trackKey = '';
  let lastCaptionSignature = '';
  let lastTimelineSignature = '';
  let liveCandidateText = '';
  let liveCandidateSince = 0;
  let liveCandidateStartMs = 0;
  let hasTimedTextTrack = false;
  let resourceCursor = 0;
  let timerTicks = 0;
  let intervalId: number | null = null;
  let unavailableTimeoutId: number | null = null;
  let overlayPlayer: HTMLElement | null = null;
  let overlayHost: HTMLElement | null = null;
  let overlaySource: HTMLElement | null = null;
  let overlayTarget: HTMLElement | null = null;
  let overlayStyle: HTMLStyleElement | null = null;
  let overlayPlayerWidth = 0;
  let overlayPlayerFullscreen = false;
  let captionControlPlayer: HTMLElement | null = null;
  let captionControlNativeButton: HTMLButtonElement | null = null;
  let captionControlButton: HTMLButtonElement | null = null;
  let captionControlLabel: HTMLSpanElement | null = null;
  let lastNativeCaptionRequestAt = 0;

  const overlayHostId = `reader-bilingual-caption-${navigationId}`;
  const overlayStyleId = `reader-bilingual-caption-style-${navigationId}`;
  const overlayActiveClass = 'reader-bilingual-caption-active';

  const post = (payload: Record<string, unknown>) => {
    try {
      bridgeWindow.ReactNativeWebView?.postMessage(JSON.stringify({
        ...payload,
        channel_token: config.channelToken,
        media_epoch: mediaEpoch,
        navigation_id: navigationId,
      }));
    } catch {
      // Playback must never depend on the native translation bridge.
    }
  };

  const normalizeText = (value: string | null | undefined) =>
    (value ?? '')
      .replace(/[\u200b-\u200d\ufeff]/g, '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

  const hashText = (value: string) => {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36);
  };

  const videoIdFromUrl = (value: string) => {
    try {
      const parsed = new URL(value, location.href);
      const queryId = parsed.searchParams.get('v');
      if (queryId) return queryId;
      const pathMatch = parsed.pathname.match(/\/(?:embed|shorts|live)\/([^/?#]+)/);
      return pathMatch?.[1] ?? '';
    } catch {
      return '';
    }
  };

  const currentMediaIdentity = () => {
    let locationKey = location.href;
    try {
      const parsed = new URL(location.href);
      parsed.hash = '';
      locationKey = parsed.toString();
    } catch {
      // Keep the raw location for non-standard embedded player URLs.
    }
    if (lastLocationKey && lastLocationKey !== locationKey) observedTimedTextVideoId = '';
    lastLocationKey = locationKey;
    if (observedTimedTextVideoId) return `video:${observedTimedTextVideoId}`;
    const pageId = videoIdFromUrl(location.href);
    if (pageId) return `video:${pageId}`;
    const video = document.querySelector('video');
    if (video instanceof HTMLVideoElement) {
      const sourceUrl = video.currentSrc || video.getAttribute('src') || '';
      const sourceId = videoIdFromUrl(sourceUrl);
      if (sourceId) return `video:${sourceId}`;
      if (sourceUrl && !sourceUrl.startsWith('blob:')) return `source:${hashText(sourceUrl)}`;
    }
    try {
      const parsed = new URL(location.href);
      parsed.hash = '';
      return `page:${parsed.origin}${parsed.pathname}${parsed.search}`;
    } catch {
      return `page:${location.href}`;
    }
  };

  const decodeMarkup = (value: string) => {
    const container = document.createElement('div');
    container.innerHTML = value;
    return normalizeText(container.textContent);
  };

  const secondsToMs = (value: string | null | undefined) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(parsed * 1_000, 0) : 0;
  };

  const parseClock = (value: string) => {
    const parts = value.trim().replace(',', '.').split(':').map(Number);
    if (parts.some((part) => !Number.isFinite(part))) return 0;
    if (parts.length === 3) return ((parts[0] * 60 + parts[1]) * 60 + parts[2]) * 1_000;
    if (parts.length === 2) return (parts[0] * 60 + parts[1]) * 1_000;
    return (parts[0] ?? 0) * 1_000;
  };

  const parseJson3 = (body: string): CaptionCue[] => {
    try {
      const parsed = JSON.parse(body) as {
        events?: { dDurationMs?: number; segs?: { utf8?: string }[]; tStartMs?: number }[];
      };
      if (!Array.isArray(parsed.events)) return [];
      return parsed.events.flatMap((event) => {
        const text = normalizeText(event.segs?.map((segment) => segment.utf8 ?? '').join(''));
        const startMs = Number(event.tStartMs ?? 0);
        const durationMs = Number(event.dDurationMs ?? 2_000);
        if (!text || !Number.isFinite(startMs)) return [];
        return [{
          endMs: Math.max(startMs + (Number.isFinite(durationMs) ? durationMs : 2_000), startMs + 600),
          startMs,
          text,
        }];
      });
    } catch {
      return [];
    }
  };

  const parseXml = (body: string): CaptionCue[] => {
    try {
      const documentNode = new DOMParser().parseFromString(body, 'text/xml');
      if (documentNode.querySelector('parsererror')) return [];
      return [...documentNode.querySelectorAll('text')].flatMap((node) => {
        const text = normalizeText(node.textContent);
        const startMs = secondsToMs(node.getAttribute('start'));
        const durationMs = secondsToMs(node.getAttribute('dur')) || 2_000;
        return text ? [{ endMs: startMs + Math.max(durationMs, 600), startMs, text }] : [];
      });
    } catch {
      return [];
    }
  };

  const parseVtt = (body: string): CaptionCue[] => {
    const lines = body.replace(/\r/g, '').split('\n');
    const cues: CaptionCue[] = [];
    for (let index = 0; index < lines.length; index += 1) {
      const timing = lines[index].match(/((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})/);
      if (!timing) continue;
      const textLines: string[] = [];
      index += 1;
      while (index < lines.length && lines[index].trim()) {
        textLines.push(lines[index]);
        index += 1;
      }
      const text = decodeMarkup(textLines.join(' '));
      if (text) cues.push({ endMs: parseClock(timing[2]), startMs: parseClock(timing[1]), text });
    }
    return cues;
  };

  const parseTimedText = (body: string) => {
    const trimmed = body.trim();
    if (!trimmed) return [];
    if (trimmed.startsWith('{')) return parseJson3(trimmed);
    if (trimmed.startsWith('WEBVTT')) return parseVtt(trimmed);
    if (trimmed.startsWith('<')) return parseXml(trimmed);
    return [];
  };

  const maxDisplayChars = 140;
  const maxDisplayDurationMs = 9_000;
  const targetDisplayChars = 100;
  const targetDisplayDurationMs = 6_500;
  const sentenceEnd = /[.!?。！？…][\])}"'’”]*$/;
  const clauseEnd = /[,;:，；：—–-][\])}"'’”]*$/;
  const danglingEnglishWord = /\b(?:a|an|the|of|to|for|and|or|but|can|could|will|would|should|is|are|was|were|has|have|had|do|does|did|my|your|his|her|their|our|this|that|these|those|with|from|in|on|at|by|as|than|if|when|while|because|so)$/i;

  const splitDisplayText = (value: string, minimumParts: number) => {
    const parts: string[] = [];
    let remaining = normalizeText(value);
    const requiredParts = Math.max(
      minimumParts,
      Math.ceil(remaining.length / maxDisplayChars),
    );
    while (remaining) {
      const partsLeft = Math.max(
        requiredParts - parts.length,
        Math.ceil(remaining.length / maxDisplayChars),
      );
      if (partsLeft <= 1 && remaining.length <= maxDisplayChars) {
        parts.push(remaining);
        break;
      }
      const target = Math.min(maxDisplayChars, Math.ceil(remaining.length / partsLeft));
      const minimumCut = Math.max(1, Math.floor(target * 0.55));
      const maximumCut = Math.max(
        minimumCut,
        Math.min(maxDisplayChars, remaining.length - Math.max(partsLeft - 1, 1)),
      );
      let bestCut = 0;
      let bestScore = Number.NEGATIVE_INFINITY;
      for (let index = minimumCut; index <= maximumCut; index += 1) {
        const before = remaining.slice(0, index).trim();
        const after = remaining.slice(index).trim();
        if (!before || !after) continue;
        const whitespaceBoundary = /\s/.test(remaining[index - 1] ?? '') ||
          /\s/.test(remaining[index] ?? '');
        const atSentenceEnd = sentenceEnd.test(before);
        const atClauseEnd = clauseEnd.test(before);
        if (!whitespaceBoundary && !atSentenceEnd && !atClauseEnd) continue;
        const score = (atSentenceEnd ? 48 : atClauseEnd ? 18 : 0) -
          Math.abs(index - target);
        if (score > bestScore) {
          bestCut = index;
          bestScore = score;
        }
      }
      if (!bestCut) bestCut = maximumCut;
      const part = normalizeText(remaining.slice(0, bestCut));
      remaining = normalizeText(remaining.slice(bestCut));
      if (part) parts.push(part);
      if (!part && remaining) {
        parts.push(remaining);
        break;
      }
    }
    return parts;
  };

  const splitCueForDisplay = (cue: CaptionCue): CaptionCue[] => {
    const durationMs = Math.max(cue.endMs - cue.startMs, 600);
    const minimumParts = Math.max(
      1,
      Math.ceil(cue.text.length / maxDisplayChars),
      Math.ceil(durationMs / maxDisplayDurationMs),
    );
    if (minimumParts === 1) return [cue];
    const parts = splitDisplayText(cue.text, minimumParts);
    if (parts.length <= 1) return [cue];
    return parts.map((text, index) => ({
      endMs: index === parts.length - 1
        ? cue.endMs
        : cue.startMs + Math.round(durationMs * (index + 1) / parts.length),
      startMs: index === 0
        ? cue.startMs
        : cue.startMs + Math.round(durationMs * index / parts.length),
      text,
    }));
  };

  const groupCues = (rawCues: CaptionCue[], nextTrackKey: string): CaptionGroup[] => {
    const cues = rawCues
      .filter((cue) => cue.text && Number.isFinite(cue.startMs))
      .flatMap(splitCueForDisplay)
      .sort((left, right) => left.startMs - right.startMs)
      .filter((cue, index, values) => index === 0 || cue.text !== values[index - 1].text || Math.abs(cue.startMs - values[index - 1].startMs) > 200);
    const result: CaptionGroup[] = [];
    let bucket: CaptionCue[] = [];
    const bucketText = (items: CaptionCue[]) => normalizeText(items.map((cue) => cue.text).join(' '));
    const flush = (count = bucket.length) => {
      const flushed = bucket.slice(0, count);
      if (!flushed.length) return;
      const text = bucketText(flushed);
      const startMs = flushed[0].startMs;
      const endMs = Math.max(...flushed.map((cue) => cue.endMs), startMs + 1_200);
      if (text) {
        result.push({
          endMs,
          id: `caption:${navigationId}:${mediaEpoch.toString(36)}:${nextTrackKey}:${Math.round(startMs).toString(36)}:${hashText(text)}`,
          startMs,
          text,
        });
      }
      bucket = bucket.slice(count);
    };
    const preferredSplit = () => {
      const earliest = Math.max(1, Math.floor(bucket.length * 0.4));
      for (let index = bucket.length - 2; index >= earliest; index -= 1) {
        const text = bucketText(bucket.slice(0, index + 1));
        const gap = bucket[index + 1].startMs - bucket[index].endMs;
        if ((clauseEnd.test(text) || gap >= 700) && !danglingEnglishWord.test(text)) {
          return index + 1;
        }
      }
      return 0;
    };
    for (const cue of cues) {
      const previous = bucket[bucket.length - 1];
      const precedingText = bucketText(bucket);
      if (
        previous &&
        cue.startMs - previous.endMs > 1_100 &&
        !danglingEnglishWord.test(precedingText)
      ) flush();
      while (bucket.length) {
        const candidateText = bucketText([...bucket, cue]);
        const candidateDuration = Math.max(cue.endMs, ...bucket.map((item) => item.endMs)) -
          bucket[0].startMs;
        if (
          candidateText.length <= maxDisplayChars &&
          candidateDuration <= maxDisplayDurationMs
        ) break;
        const split = preferredSplit();
        if (split) flush(split);
        else flush();
      }
      bucket.push(cue);
      const text = bucketText(bucket);
      const duration = cue.endMs - bucket[0].startMs;
      if (sentenceEnd.test(text)) {
        flush();
        continue;
      }
      if (
        text.length >= targetDisplayChars ||
        duration >= targetDisplayDurationMs
      ) {
        const split = preferredSplit();
        if (split) flush(split);
      }
    }
    flush();
    return result.map((group, index) => {
      const next = result[index + 1];
      const naturalEnd = Math.max(group.endMs, group.startMs + 1_200);
      return {
        ...group,
        endMs: next
          ? Math.min(Math.max(naturalEnd, next.startMs), group.startMs + maxDisplayDurationMs)
          : Math.min(naturalEnd + 1_000, group.startMs + maxDisplayDurationMs),
      };
    });
  };

  const currentTimeMs = () => {
    const video = document.querySelector('video');
    return video instanceof HTMLVideoElement && Number.isFinite(video.currentTime)
      ? video.currentTime * 1_000
      : 0;
  };

  const activeIndex = (timeMs: number) => {
    let low = 0;
    let high = groups.length - 1;
    let match = -1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (groups[middle].startMs <= timeMs + 120) {
        match = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (match < 0 || timeMs > groups[match].endMs + 250) return -1;
    return match;
  };

  const activePlayer = () => {
    const videos = [...document.querySelectorAll('video')]
      .filter((candidate): candidate is HTMLVideoElement => candidate instanceof HTMLVideoElement);
    const video = videos.find((candidate) => !candidate.paused && candidate.readyState > 0)
      ?? videos.find((candidate) => candidate.readyState > 0)
      ?? videos[0];
    const player = video?.closest('.html5-video-player');
    return player instanceof HTMLElement ? player : null;
  };

  const nativeCaptionButton = () => (
    activePlayer()?.querySelector<HTMLButtonElement>('.ytp-subtitles-button')
    ?? document.querySelector<HTMLButtonElement>('.ytmClosedCaptioningButtonButton')
  );

  const nativeCaptionsEnabled = () => {
    const subtitlesButton = nativeCaptionButton();
    // Desktop exposes aria-pressed; mobile watch pages encode the same state in
    // the temporary control's accessible label. Unknown variants remain enabled
    // so timed-text discovery can continue even while controls are hidden.
    const pressed = subtitlesButton?.getAttribute('aria-pressed');
    if (pressed === 'true' || pressed === 'false') return pressed === 'true';
    const stateLabel = [
      subtitlesButton?.getAttribute('aria-label'),
      subtitlesButton?.getAttribute('title'),
    ].filter(Boolean).join(' ');
    if (/(?:turned\s+off|disabled|\boff\b|关闭|關閉|オフ|désactiv|desactiv|deaktiv|disattiv|deslig)/i.test(stateLabel)) {
      return false;
    }
    return true;
  };

  const removeCaptionControl = () => {
    captionControlButton?.remove();
    captionControlPlayer = null;
    captionControlNativeButton = null;
    captionControlButton = null;
    captionControlLabel = null;
  };

  const updateCaptionControl = () => {
    if (!captionControlButton || !captionControlLabel) return;
    const active = mode !== 'original';
    if (interfaceMessages) {
      captionControlButton.setAttribute('aria-label', active ? interfaceMessages.disableBilingualCaptions : interfaceMessages.enableBilingualCaptions);
      captionControlButton.setAttribute('title', active ? interfaceMessages.disableBilingualCaptions : interfaceMessages.enableBilingualCaptions);
      captionControlLabel.textContent = interfaceMessages.bilingual;
    }
    captionControlButton.setAttribute('aria-pressed', active ? 'true' : 'false');
    if (active) captionControlButton.classList.add('reader-bilingual-caption-toggle-active');
    else captionControlButton.classList.remove('reader-bilingual-caption-toggle-active');
  };

  const ensureCaptionControl = () => {
    const player = activePlayer();
    const subtitlesButton = nativeCaptionButton();
    if (!player || !subtitlesButton) {
      if (captionControlButton && !captionControlButton.isConnected) removeCaptionControl();
      return false;
    }
    if (
      captionControlPlayer === player &&
      captionControlNativeButton === subtitlesButton &&
      captionControlButton?.isConnected &&
      captionControlLabel?.isConnected
    ) {
      updateCaptionControl();
      return true;
    }
    removeCaptionControl();
    ensureOverlayStyle();
    const button = document.createElement('button');
    const label = document.createElement('span');
    const mobileControl = subtitlesButton.classList.contains('ytmClosedCaptioningButtonButton');
    const insertionAnchor = mobileControl
      ? subtitlesButton.closest('yt-closed-captions-toggle-button') ?? subtitlesButton
      : subtitlesButton;
    button.className = mobileControl
      ? 'reader-bilingual-caption-toggle reader-bilingual-caption-toggle-mobile'
      : 'ytp-button reader-bilingual-caption-toggle';
    button.type = 'button';
    button.setAttribute('data-reader-caption-control', navigationId);
    label.className = 'reader-bilingual-caption-toggle-label';
    button.appendChild(label);
    button.onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      post({
        mode: mode === 'original' ? 'bilingual' : 'original',
        type: 'reader_translation_mode_request',
      });
    };
    insertionAnchor.insertAdjacentElement('afterend', button);
    captionControlPlayer = player;
    captionControlNativeButton = subtitlesButton;
    captionControlButton = button;
    captionControlLabel = label;
    lastNativeCaptionRequestAt = 0;
    updateCaptionControl();
    post({ state: 'caption_control_mounted', type: 'reader_translation_status' });
    return true;
  };

  const enableNativeCaptions = () => {
    const subtitlesButton = nativeCaptionButton();
    if (!subtitlesButton || nativeCaptionsEnabled()) return;
    const now = Date.now();
    if (now - lastNativeCaptionRequestAt < 1_000) return;
    lastNativeCaptionRequestAt = now;
    try {
      subtitlesButton.click();
    } catch {
      // The bridge keeps waiting if this player variant does not expose a clickable CC button.
    }
  };

  const ensureOverlayStyle = () => {
    if (overlayStyle?.isConnected) return overlayStyle;
    const existingStyle = document.getElementById(overlayStyleId);
    if (existingStyle instanceof HTMLStyleElement) {
      overlayStyle = existingStyle;
      return overlayStyle;
    }
    const style = document.createElement('style');
    style.id = overlayStyleId;
    style.textContent = `
      .reader-bilingual-caption-host {
        align-items: center;
        bottom: max(13%, 54px);
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        gap: clamp(3px, .45vh, 7px);
        left: 50%;
        max-width: min(92%, 1040px);
        pointer-events: none;
        position: absolute;
        text-align: center;
        transform: translateX(-50%);
        width: min(92%, 1040px);
        z-index: 2147483000;
      }
      .html5-video-player.ytp-autohide .reader-bilingual-caption-host {
        bottom: max(6.5%, 24px);
      }
      .reader-bilingual-caption-line {
        background: rgba(0, 0, 0, .58);
        border-radius: clamp(2px, .35vw, 6px);
        box-decoration-break: clone;
        -webkit-box-decoration-break: clone;
        box-sizing: border-box;
        color: #fff;
        display: block;
        font-family: Arial, Helvetica, sans-serif;
        font-size: var(--reader-caption-source-size, 9px) !important;
        font-weight: 500;
        line-height: 1.28;
        max-width: 100%;
        overflow-wrap: anywhere;
        padding: .08em .34em .12em;
        text-shadow: 0 1px 2px rgba(0, 0, 0, .9);
        white-space: normal;
      }
      .reader-bilingual-caption-target {
        color: #fff7c2;
        font-size: var(--reader-caption-target-size, 8.5px) !important;
        font-weight: 650;
      }
      .html5-video-player.reader-bilingual-caption-active .caption-window,
      .html5-video-player.reader-bilingual-caption-active .ytp-caption-window-container {
        opacity: 0 !important;
        visibility: hidden !important;
      }
      .reader-bilingual-caption-toggle {
        align-items: center !important;
        box-sizing: border-box !important;
        display: inline-flex !important;
        justify-content: center !important;
        min-width: 46px !important;
        padding: 0 5px !important;
        vertical-align: top !important;
        width: auto !important;
      }
      .reader-bilingual-caption-toggle-label {
        border: 1px solid rgba(255, 255, 255, .78);
        border-radius: 3px;
        box-sizing: border-box;
        color: rgba(255, 255, 255, .92);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 10px;
        font-weight: 700;
        line-height: 17px;
        min-width: 29px;
        padding: 0 3px;
        text-align: center;
      }
      .reader-bilingual-caption-toggle-active .reader-bilingual-caption-toggle-label {
        background: #fff7c2;
        border-color: #fff7c2;
        color: #111;
      }
      .reader-bilingual-caption-toggle-mobile {
        align-self: center !important;
        background: transparent !important;
        border: 0 !important;
        height: 48px !important;
        margin: 0 !important;
        min-width: 48px !important;
        padding: 0 4px !important;
        width: 48px !important;
      }
    `;
    (document.head ?? document.documentElement).appendChild(style);
    overlayStyle = style;
    return style;
  };

  const updateOverlayMetrics = () => {
    if (!overlayPlayer || !overlayHost) return;
    const playerWidth = overlayPlayer.getBoundingClientRect().width;
    const isFullscreen = Boolean(document.fullscreenElement)
      || overlayPlayer.classList.contains('ytp-fullscreen');
    if (
      !Number.isFinite(playerWidth) ||
      playerWidth <= 0 ||
      (Math.abs(playerWidth - overlayPlayerWidth) < 1 && isFullscreen === overlayPlayerFullscreen)
    ) return;
    overlayPlayerWidth = playerWidth;
    overlayPlayerFullscreen = isFullscreen;
    const sourceSize = isFullscreen
      ? Math.round(Math.min(Math.max(playerWidth * 0.0215, 13), 24) * 10) / 10
      : Math.round(Math.min(Math.max(playerWidth * 0.018, 9), 18) * 10) / 10;
    const targetSize = isFullscreen
      ? Math.round(Math.min(Math.max(playerWidth * 0.0200, 13), 23) * 10) / 10
      : Math.round(Math.min(Math.max(playerWidth * 0.017, 8.5), 17) * 10) / 10;
    overlayHost.style.setProperty('--reader-caption-source-size', `${sourceSize}px`);
    overlayHost.style.setProperty('--reader-caption-target-size', `${targetSize}px`);
  };

  const restoreNativeCaptions = (removeHost = false) => {
    overlayPlayer?.classList.remove(overlayActiveClass);
    if (overlayHost) {
      overlayHost.hidden = true;
      overlayHost.replaceChildren();
    }
    overlaySource = null;
    overlayTarget = null;
    if (removeHost) {
      overlayHost?.remove();
      overlayHost = null;
      overlayPlayer = null;
      overlayPlayerWidth = 0;
      overlayPlayerFullscreen = false;
    }
  };

  const resetMediaState = (nextIdentity: string, state: 'media_changed' | 'track_changed') => {
    mediaEpoch += 1;
    mediaIdentity = nextIdentity;
    groups = [];
    trackKey = '';
    translations.clear();
    translationExpiresAt.clear();
    visibleTimelineSegmentIds = [];
    hasTimedTextTrack = false;
    liveCandidateText = '';
    liveCandidateSince = 0;
    liveCandidateStartMs = 0;
    lastCaptionSignature = '';
    lastTimelineSignature = '';
    lastWindowSignature = '';
    timelineStart = 0;
    timelineEnd = 0;
    lastTimelineCurrentId = null;
    mediaSettlingUntil = state === 'media_changed' ? Date.now() + 1_200 : 0;
    restoreNativeCaptions(true);
    try {
      resourceCursor = performance.getEntriesByType('resource').length;
    } catch {
      resourceCursor = 0;
    }
    if (unavailableTimeoutId !== null) window.clearTimeout(unavailableTimeoutId);
    unavailableTimeoutId = window.setTimeout(() => {
      if (!groups.length) post({ state: 'captions_unavailable', type: 'reader_translation_status' });
    }, 10_000);
    post({
      captions: [],
      current_segment_id: null,
      type: 'reader_translation_caption_timeline',
    });
    post({ count: 0, media_epoch: mediaEpoch, state, type: 'reader_translation_status' });
  };

  const syncMediaEpoch = () => {
    const nextIdentity = currentMediaIdentity();
    if (!mediaIdentity) {
      mediaIdentity = nextIdentity;
      mediaEpoch = 1;
    } else if (nextIdentity !== mediaIdentity) {
      resetMediaState(nextIdentity, 'media_changed');
    }
    return mediaEpoch;
  };

  const ensureOverlayHost = () => {
    const player = activePlayer();
    if (!player) return false;
    if (overlayPlayer !== player || !overlayHost?.isConnected) {
      restoreNativeCaptions(true);
      ensureOverlayStyle();
      const host = document.createElement('div');
      host.className = 'reader-bilingual-caption-host';
      host.id = overlayHostId;
      host.setAttribute('aria-hidden', 'true');
      player.appendChild(host);
      overlayPlayer = player;
      overlayHost = host;
      post({ state: 'overlay_mounted', type: 'reader_translation_status' });
    }
    return true;
  };

  const isAdvertisement = () => Boolean(activePlayer()?.classList.contains('ad-showing'));

  const renderOverlay = (group: CaptionGroup | null, translated: string) => {
    if (mode === 'original' || !group || isAdvertisement()) {
      restoreNativeCaptions();
      return;
    }
    try {
      if (!ensureOverlayHost() || !overlayHost || !overlayPlayer) {
        restoreNativeCaptions(true);
        return;
      }
      updateOverlayMetrics();
      const sourceText = mode === 'translated' && translated ? '' : group.text;
      const targetText = translated;
      if (!sourceText && !targetText) {
        restoreNativeCaptions();
        return;
      }
      if (!overlaySource) {
        overlaySource = document.createElement('div');
        overlaySource.className = 'reader-bilingual-caption-line reader-bilingual-caption-source';
        overlayHost.appendChild(overlaySource);
      }
      if (!overlayTarget) {
        overlayTarget = document.createElement('div');
        overlayTarget.className = 'reader-bilingual-caption-line reader-bilingual-caption-target';
        overlayHost.appendChild(overlayTarget);
      }
      overlaySource.textContent = sourceText;
      overlaySource.hidden = !sourceText;
      overlayTarget.textContent = targetText;
      overlayTarget.hidden = !targetText;
      overlayHost.hidden = false;
      // Native captions are hidden only after a valid source or translation
      // has been mounted into the application-owned player subtree.
      overlayPlayer.classList.add(overlayActiveClass);
    } catch {
      restoreNativeCaptions(true);
      overlayStyle?.remove();
      overlayStyle = null;
      post({ state: 'overlay_failed', type: 'reader_translation_status' });
    }
  };

  const postCaptionTimeline = (currentSegmentId: string | null) => {
    const captions: {
      end_ms: number;
      segment_id: string;
      start_ms: number;
      source_text: string;
      translated_text: string | null;
    }[] = [];
    const currentIndex = groups.findIndex(group => group.id === currentSegmentId);
    // Follow playback when it moves outside the loaded window; manual browsing
    // persists while the current cue stays unchanged. Start at the cue so the
    // character cap cannot discard it behind earlier long captions.
    if (currentSegmentId !== lastTimelineCurrentId && currentIndex >= 0
      && (currentIndex < timelineStart || currentIndex >= timelineEnd)) timelineStart = currentIndex;
    lastTimelineCurrentId = currentSegmentId;
    timelineStart = Math.min(timelineStart, Math.max(groups.length - 1, 0));
    let characterCount = 0;
    for (const group of groups.slice(timelineStart, timelineStart + 180)) {
      const translated = translations.get(group.id) ?? null;
      const nextCharacterCount = characterCount + group.text.length + (translated?.length ?? 0);
      if (nextCharacterCount > 40_000) break;
      characterCount = nextCharacterCount;
      captions.push({
        start_ms: Math.max(group.startMs, 0),
        end_ms: Math.max(group.endMs, group.startMs),
        segment_id: group.id,
        source_text: group.text,
        translated_text: translated,
      });
    }
    timelineEnd = timelineStart + captions.length;
    const signature = `${timelineStart}:${groups.length}:${currentSegmentId ?? ''}|${captions.map((caption) => (
      `${caption.segment_id}:${caption.translated_text ?? ''}`
    )).join('|')}`;
    if (signature === lastTimelineSignature) return;
    lastTimelineSignature = signature;
    post({
      captions,
      has_previous: timelineStart > 0,
      has_next: timelineEnd < groups.length,
      current_segment_id: currentSegmentId,
      type: 'reader_translation_caption_timeline',
    });
  };

  const requestWindow = (timeMs: number) => {
    if ((mode === 'original' && timelineMode === 'original') || !groups.length) {
      lastWindowSignature = '';
      return;
    }
    const current = Math.max(activeIndex(timeMs), groups.findIndex((group) => group.startMs >= timeMs), 0);
    if (lastRequestedIndex >= 0 && Math.abs(current - lastRequestedIndex) > 3) captionWindowEpoch += 1;
    lastRequestedIndex = current;
    const windowId = `${mediaEpoch}:${captionWindowEpoch}`;
    const makeBatch = (start: number, count: number, priority: 'urgent' | 'prefetch') => {
      const segments = groups.slice(Math.max(start, 0), Math.min(start + count, groups.length))
        .map((group) => ({ segment_id: group.id, text: group.text }));
      return segments.length ? {
        id: `caption:${windowId}:${priority}:${Math.max(start, 0)}`,
        priority,
        segments,
        window_id: windowId,
      } : null;
    };
    const urgent = makeBatch(current - 1, 3, 'urgent');
    const prefetch = makeBatch(current + 2, 5, 'prefetch');
    const hasVisibleTimeline = timelineMode !== 'original' && visibleTimelineSegmentIds.length > 0;
    const batches = mode !== 'original' || !hasVisibleTimeline ? [urgent, prefetch].filter(Boolean) : [];
    if (hasVisibleTimeline) {
      const visible = groups.filter(group => visibleTimelineSegmentIds.includes(group.id)).slice(0, 20);
      for (let offset = 0; offset < visible.length; offset += 5) {
        batches.unshift({
          id: `caption:${windowId}:timeline:${offset}`,
          priority: 'urgent' as const,
          segments: visible.slice(offset, offset + 5).map(group => ({ segment_id: group.id, text: group.text })),
          window_id: windowId,
        });
      }
    }
    const signature = JSON.stringify(batches);
    if (signature === lastWindowSignature) return;
    lastWindowSignature = signature;
    if (batches.length) post({ batches, type: 'reader_translation_batch_plan' });
  };

  const render = () => {
    ensureCaptionControl();
    if ((mode !== 'original' || timelineMode !== 'original') && !nativeCaptionsEnabled()) enableNativeCaptions();
    updateCaptionControl();
    const timeMs = currentTimeMs();
    requestWindow(timeMs);
    const index = activeIndex(timeMs);
    const group = index >= 0 ? groups[index] : null;
    postCaptionTimeline(group?.id ?? null);
    if (mode === 'original' || !nativeCaptionsEnabled() || !group) {
      renderOverlay(null, '');
      if (!lastCaptionSignature) return;
      lastCaptionSignature = '';
      post({
        start_ms: null,
        end_ms: null,
        segment_id: null,
        source_text: null,
        translated_text: null,
        type: 'reader_translation_caption',
      });
      return;
    }
    const translated = translations.get(group.id) ?? '';
    renderOverlay(group, translated);
    const signature = `${group.id}:${translated}`;
    if (signature === lastCaptionSignature) return;
    lastCaptionSignature = signature;
    post({
      start_ms: group.startMs,
      end_ms: group.endMs,
      segment_id: group.id,
      source_text: group.text,
      translated_text: translated || null,
      type: 'reader_translation_caption',
    });
  };

  const setGroups = (nextGroups: CaptionGroup[], nextTrackKey: string, fromTimedText: boolean) => {
    if (!nextGroups.length) return;
    if (fromTimedText) {
      hasTimedTextTrack = true;
      groups = nextGroups;
      trackKey = nextTrackKey;
    } else if (!hasTimedTextTrack) {
      groups = [...groups, ...nextGroups]
        .sort((left, right) => left.startMs - right.startMs)
        .slice(-80);
    }
    post({ count: groups.length, state: 'captions_detected', type: 'reader_translation_status' });
    requestWindow(currentTimeMs());
    render();
  };

  const timedTextUrl = (value: unknown) => {
    try {
      const raw = typeof value === 'string'
        ? value
        : value instanceof URL
          ? value.toString()
          : typeof Request !== 'undefined' && value instanceof Request
            ? value.url
            : '';
      if (!raw) return null;
      const parsed = new URL(raw, location.href);
      if (!parsed.pathname.includes('/api/timedtext') || parsed.searchParams.has('tlang')) return null;
      return parsed;
    } catch {
      return null;
    }
  };

  const consumeTimedText = (url: URL, body: string, requestEpoch: number) => {
    syncMediaEpoch();
    if (requestEpoch !== mediaEpoch) return;
    const responseVideoId = url.searchParams.get('v') ?? '';
    if (responseVideoId && mediaIdentity.startsWith('video:') && mediaIdentity !== `video:${responseVideoId}`) {
      let hasAuthoritativeWatchUrl = false;
      try {
        const pageUrl = new URL(location.href);
        hasAuthoritativeWatchUrl = pageUrl.pathname === '/watch'
          || /^\/(?:shorts|live)\//.test(pageUrl.pathname);
      } catch {
        // Embedded players can establish their active video from timed text.
      }
      if (hasAuthoritativeWatchUrl) return;
      observedTimedTextVideoId = responseVideoId;
      resetMediaState(`video:${responseVideoId}`, 'media_changed');
    }
    const cues = parseTimedText(body);
    if (!cues.length) return;
    const nextTrackKey = hashText([
      responseVideoId,
      url.searchParams.get('lang') ?? '',
      url.searchParams.get('vssId') ?? '',
      url.searchParams.get('name') ?? '',
    ].join(':'));
    if (trackKey && nextTrackKey !== trackKey) resetMediaState(mediaIdentity, 'track_changed');
    mediaSettlingUntil = 0;
    const nextGroups = groupCues(cues, nextTrackKey);
    if (nextTrackKey === trackKey && nextGroups.length < groups.length) return;
    setGroups(nextGroups, nextTrackKey, true);
  };

  const originalFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;
  if (originalFetch) {
    try {
      window.fetch = (async (...args: Parameters<typeof fetch>) => {
        const requestEpoch = syncMediaEpoch();
        const response = await originalFetch(...args);
        const url = timedTextUrl(args[0]);
        if (url) void response.clone().text().then((body) => consumeTimedText(url, body, requestEpoch)).catch(() => undefined);
        return response;
      }) as typeof window.fetch;
    } catch {
      // DOM caption observation remains available if the page freezes fetch.
    }
  }

  try {
    const xhrPrototype = XMLHttpRequest.prototype as unknown as {
      open: (...args: unknown[]) => unknown;
      send: (...args: unknown[]) => unknown;
    };
    const originalOpen = xhrPrototype.open;
    const originalSend = xhrPrototype.send;
    xhrPrototype.open = function (...args: unknown[]) {
      const url = timedTextUrl(args[1]);
      if (url) xhrRequests.set(this as unknown as XMLHttpRequest, {
        epoch: syncMediaEpoch(),
        url: url.toString(),
      });
      return Reflect.apply(originalOpen, this, args);
    };
    xhrPrototype.send = function (...args: unknown[]) {
      const xhr = this as unknown as XMLHttpRequest;
      const request = xhrRequests.get(xhr);
      if (request) {
        xhr.addEventListener('load', () => {
          try {
            const url = timedTextUrl(request.url);
            if (!url) return;
            if (!xhr.responseType || xhr.responseType === 'text') consumeTimedText(url, xhr.responseText, request.epoch);
            else if (xhr.responseType === 'json') consumeTimedText(url, JSON.stringify(xhr.response), request.epoch);
          } catch {
            // Some response types intentionally disallow responseText access.
          }
        }, { once: true });
      }
      return Reflect.apply(originalSend, this, args);
    };
  } catch {
    // YouTube playback is untouched if its networking primitives are immutable.
  }

  const inspectResources = () => {
    if (!originalFetch) return;
    const entries = performance.getEntriesByType('resource');
    if (entries.length < resourceCursor) resourceCursor = 0;
    for (const entry of entries.slice(resourceCursor)) {
      const url = timedTextUrl(entry.name);
      if (!url || inspectedResourceUrls.has(url.toString())) continue;
      inspectedResourceUrls.add(url.toString());
      const requestEpoch = syncMediaEpoch();
      void originalFetch(url.toString(), { credentials: 'include' })
        .then((response) => response.ok ? response.text() : '')
        .then((body) => { if (body) consumeTimedText(url, body, requestEpoch); })
        .catch(() => undefined);
    }
    resourceCursor = entries.length;
  };

  const captureLiveCaption = () => {
    if (hasTimedTextTrack || Date.now() < mediaSettlingUntil || !nativeCaptionsEnabled()) return;
    const lines = [...document.querySelectorAll(
      '.caption-window .ytp-caption-segment,.caption-window .caption-visual-line',
    )]
      .map((node) => normalizeText(node.textContent))
      .filter((text, index, values) => text && values.indexOf(text) === index);
    const text = normalizeText(lines.join(' '));
    if (!text) return;
    const now = Date.now();
    const timeMs = currentTimeMs();
    if (text !== liveCandidateText) {
      liveCandidateText = text;
      liveCandidateSince = now;
      liveCandidateStartMs = timeMs;
      if (!/[.!?。！？…]$/.test(text) && text.length < 90) return;
    }
    if (now - liveCandidateSince < 320 && !/[.!?。！？…]$/.test(text) && text.length < 90) return;
    const id = `caption:${navigationId}:${mediaEpoch.toString(36)}:live:${Math.round(liveCandidateStartMs / 500).toString(36)}:${hashText(text)}`;
    const group = { endMs: timeMs + 5_000, id, startMs: Math.max(liveCandidateStartMs - 250, 0), text };
    setGroups([group], 'live', false);
  };

  const setInterfaceMessages = (messages: YouTubeInterfaceMessages) => {
    interfaceMessages = messages;
    updateCaptionControl();
  };

  const setMode = (nextMode: TranslationDisplayMode) => {
    syncMediaEpoch();
    mode = ['original', 'bilingual', 'translated'].includes(nextMode) ? nextMode : 'original';
    ensureCaptionControl();
    if (mode === 'original') {
      restoreNativeCaptions(true);
    }
    if (mode !== 'original') {
      enableNativeCaptions();
      requestWindow(currentTimeMs());
    }
    updateCaptionControl();
    render();
  };

  const setTimelineMode = (nextMode: TranslationDisplayMode) => {
    timelineMode = nextMode === 'original' ? 'original' : 'bilingual';
    requestWindow(currentTimeMs());
    render();
  };

  const setTimelineVisibleSegments = (rawIds: string[], epoch: number) => {
    syncMediaEpoch();
    if (epoch !== mediaEpoch || !Array.isArray(rawIds)) return;
    const next = rawIds.filter(id => typeof id === 'string' && groups.some(group => group.id === id)).slice(0, 20);
    if (next.join('|') === visibleTimelineSegmentIds.join('|')) return;
    visibleTimelineSegmentIds = next;
    captionWindowEpoch += 1;
    requestWindow(currentTimeMs());
  };

  const applyTranslations = (rawResults: unknown[], rawMediaEpoch?: unknown) => {
    if (!Array.isArray(rawResults)) return;
    syncMediaEpoch();
    if (!Number.isInteger(rawMediaEpoch) || rawMediaEpoch !== mediaEpoch) return;
    const activeSegmentIds = new Set(groups.map((group) => group.id));
    let changed = false;
    for (const raw of rawResults) {
      if (!raw || typeof raw !== 'object') continue;
      const result = raw as { cache_expires_at?: unknown; segment_id?: unknown; translated_text?: unknown; translation_status?: unknown };
      if (
        typeof result.segment_id === 'string' &&
        activeSegmentIds.has(result.segment_id) &&
        result.translation_status === 'succeeded' &&
        typeof result.translated_text === 'string' &&
        result.translated_text.trim()
      ) {
        const expiresAt = typeof result.cache_expires_at === 'string' ? Date.parse(result.cache_expires_at) : Date.now() + 60 * 60_000;
        if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) continue;
        translationExpiresAt.set(result.segment_id, expiresAt);
        const text = result.translated_text.trim();
        if (translations.get(result.segment_id) !== text) {
          translations.set(result.segment_id, text);
          changed = true;
        }
      }
    }
    if (changed) render();
  };

  const resetTranslations = () => {
    translations.clear();
    translationExpiresAt.clear();
    captionWindowEpoch += 1;
    lastRequestedIndex = -1;
    lastCaptionSignature = '';
    requestWindow(currentTimeMs());
    render();
  };

  const setTimelineWindow = (direction: 'previous' | 'next', firstId: string, epoch: number) => {
    syncMediaEpoch();
    if (epoch !== mediaEpoch || firstId !== groups[timelineStart]?.id) return;
    if (direction === 'next' && timelineEnd < groups.length) timelineStart = timelineEnd;
    else if (direction === 'previous' && timelineStart > 0) {
      // Work backwards using the same character budget, ensuring the page
      // immediately before this one is reachable even with very long cues.
      let start = timelineStart;
      let characters = 0;
      while (start > 0 && timelineStart - start < 180) {
        const group = groups[start - 1];
        const next = characters + group.text.length + (translations.get(group.id)?.length ?? 0);
        if (next > 40_000) break;
        characters = next;
        start -= 1;
      }
      timelineStart = start;
    } else return;
    postCaptionTimeline(lastTimelineCurrentId);
  };

  const seekTo = (timeMs: number) => {
    if (!Number.isFinite(timeMs)) return;
    const video = document.querySelector('video');
    if (!(video instanceof HTMLVideoElement)) return;
    try {
      video.currentTime = Math.max(timeMs, 0) / 1_000;
      render();
    } catch {
      // A navigation or player replacement can make seeking unavailable.
    }
  };

  const refresh = () => {
    syncMediaEpoch();
    captionWindowEpoch += 1;
    lastCaptionSignature = '';
    inspectResources();
    captureLiveCaption();
    post({
      count: groups.length,
      state: groups.length ? 'captions_detected' : 'waiting_for_captions',
      type: 'reader_translation_status',
    });
    requestWindow(currentTimeMs());
    render();
  };

  const cleanup = () => {
    document.removeEventListener('visibilitychange', expireDueTranslations);
    if (intervalId !== null) window.clearInterval(intervalId);
    if (unavailableTimeoutId !== null) window.clearTimeout(unavailableTimeoutId);
    intervalId = null;
    unavailableTimeoutId = null;
    restoreNativeCaptions(true);
    removeCaptionControl();
    overlayStyle?.remove();
    overlayStyle = null;
  };

  bridgeSlots[bridgeName] = {
    applyTranslations,
    cleanup,
    kind: 'youtube',
    refresh,
    resetTranslations,
    retryFailed: refresh,
    seekTo,
    setMode,
    setInterfaceMessages,
    setTimelineMode,
    setTimelineWindow,
    setTimelineVisibleSegments,
  };
  setMode(mode);
  refresh();
  const expireDueTranslations = () => {
    let expired = false;
    for (const [id, expiresAt] of translationExpiresAt) {
      if (expiresAt > Date.now()) continue;
      translationExpiresAt.delete(id);
      translations.delete(id);
      expired = true;
    }
    if (expired) {
      captionWindowEpoch += 1;
      lastRequestedIndex = -1;
      lastCaptionSignature = '';
      requestWindow(currentTimeMs());
      render();
    }
  };
  document.addEventListener('visibilitychange', expireDueTranslations);
  intervalId = window.setInterval(() => {
    expireDueTranslations();
    syncMediaEpoch();
    timerTicks += 1;
    if (timerTicks % 3 === 0) inspectResources();
    captureLiveCaption();
    render();
  }, 300);
  if (unavailableTimeoutId !== null) window.clearTimeout(unavailableTimeoutId);
  unavailableTimeoutId = window.setTimeout(() => {
    if (!groups.length) post({ state: 'captions_unavailable', type: 'reader_translation_status' });
  }, 10_000);
  window.addEventListener('yt-navigate-finish', refresh);
  window.addEventListener('popstate', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('pagehide', (event) => {
    if (!(event as PageTransitionEvent).persisted) cleanup();
  });
}
