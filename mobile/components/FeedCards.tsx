import { useAvatarImageSource } from '../hooks/useAvatarImageSource';
import { useEffect, useState } from 'react';
import {
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
  type ImageStyle,
  type StyleProp,
} from 'react-native';
import { MaterialCommunityIcons, Feather } from '@expo/vector-icons';

import {
  displayTitle,
  type FeedItem,
} from '../lib/api';
import { colors } from '../ui/tokens';
import { xCardSnippet } from '../domain/xCardPreview';
import { relativeTime } from '../domain/formatting';
import { useTranslation } from '../i18n';
import type { XFeedCardTranslation } from '../domain/xFeedTranslation';
import {
  displaySourceLabel,
  sourceHost,
  sourceIcon,
} from '../domain/source';

export function ArticleCard({
  avatarAccessToken,
  item,
  onPress,
  onRetryXTranslation,
  onToggleSave,
  xTranslation,
}: {
  avatarAccessToken: string;
  item: FeedItem;
  onPress: () => void;
  onRetryXTranslation?: () => void;
  onToggleSave: () => void;
  xTranslation?: XFeedCardTranslation;
}) {
  const { t } = useTranslation('common');
  const isVideo = item.source_kind === 'youtube';
  const isPost = item.source_kind === 'x';
  const title = displayTitle(item);
  if (isVideo) {
    return (
      <Pressable onPress={onPress} style={styles.videoCard}>
        {item.thumbnail_url ? <ContentImage uri={item.thumbnail_url} resizeMode="contain" style={styles.videoImage} /> : null}
        <View style={styles.videoSource}>
          <SourceIdentity accessToken={avatarAccessToken} avatarUrl={item.source_avatar_url} kind={item.source_kind} label={item.source_name ?? 'YouTube'} />
        </View>
        <Text numberOfLines={3} style={styles.videoTitle}>{title}</Text>
        <View style={styles.cardFooter}>
          <Text style={styles.timeText}>{relativeTime(item.published_at ?? item.fetched_at)}</Text>
          <SaveButton isSaved={item.is_saved} onPress={onToggleSave} />
        </View>
      </Pressable>
    );
  }
  if (isPost) {
    const preview = item.x_preview;
    const author = preview?.author.name || item.author_name || item.source_name || 'X';
    const handle = preview?.author.handle ? `@${preview.author.handle.replace(/^@/, '')}` : 'X';
    const reference = preview?.repost || preview?.quote;
    const threadCount = preview?.thread?.loaded_count ?? 0;
    return (
      <Pressable accessibilityRole="button" onPress={onPress} style={styles.postCard}>
        <View style={styles.postHeader}>
          <PostAvatar accessToken={avatarAccessToken} avatarUrl={preview?.author.avatar_url || item.source_avatar_url} fallbackLabel={author} />
          <View style={styles.postCopy}>
            <Text numberOfLines={1} style={styles.postAuthor}>{author}</Text>
            <Text numberOfLines={1} style={styles.postHandle}>{handle} · {relativeTime(item.published_at ?? item.fetched_at)}{preview?.is_repost ? ` · ${t('repost')}` : ''}</Text>
          </View>
          <SaveButton isSaved={item.is_saved} onPress={onToggleSave} />
        </View>
        {threadCount > 1 ? <View style={styles.threadBadge}><Text style={styles.threadText}>{t('threadCollected', { count: threadCount })}</Text></View> : null}
        {preview?.reply_to ? <Text style={styles.replyContext}>{preview.reply_to.handle ? t('replyToHandle', { handle: preview.reply_to.handle.replace(/^@/, '') }) : t('repliedToPost')}</Text> : null}
        {!(preview?.is_repost && preview.repost) ? <Text numberOfLines={4} style={styles.postText}>{xCardSnippet(xTranslation?.bodyText || preview?.text || item.excerpt || item.title, 180)}</Text> : null}
        {reference ? <View style={styles.quote}>
          <Text style={styles.quoteLabel}>{t(preview?.is_repost ? 'repostedPost' : 'quotedPost')}</Text>
          {reference.availability === 'available' ? <>
            <Text numberOfLines={1} style={styles.quoteAuthor}>{reference.author.name || reference.author.handle || t('xUser')}</Text>
            <Text numberOfLines={2} style={styles.quoteText}>{xCardSnippet(xTranslation?.referenceText || reference.text, 80)}</Text>
          </> : <Text style={styles.quoteText}>{t('quoteUnavailable')}</Text>}
        </View> : null}
        {xTranslation?.retryable && onRetryXTranslation ? (
          <View accessibilityLiveRegion="polite" style={styles.translationFailure}>
            <Text style={styles.translationFailureText}>{t('translationFailed')}</Text>
            <Pressable
              accessibilityLabel={t('retryTranslation')}
              accessibilityRole="button"
              onPress={(event) => {
                event.stopPropagation();
                onRetryXTranslation();
              }}
              style={styles.translationRetry}
            >
              <Text style={styles.translationRetryText}>{t('retryTranslation')}</Text>
            </Pressable>
          </View>
        ) : null}
        {item.thumbnail_url ? <ContentImage uri={item.thumbnail_url} resizeMode="contain" style={styles.postImage} /> : null}
        <View style={styles.postLink}><Text style={styles.postLinkText}>{t(threadCount > 1 ? 'readThread' : 'viewOriginalPost')}</Text><MaterialCommunityIcons color={colors.textMuted} name="open-in-new" size={13} /></View>
      </Pressable>
    );
  }
  return (
    <Pressable onPress={onPress} style={styles.articleCard}>
          <View style={styles.cardMeta}>
            <SourceIdentity
              accessToken={avatarAccessToken}
              avatarUrl={item.source_avatar_url}
              kind={item.source_kind}
              label={[item.source_name, item.author_name].filter(Boolean).join(' · ') || sourceHost(item.external_url)}
            />
          </View>
      <View style={styles.articleBody}>
        <View style={styles.articleCopy}>
          <Text numberOfLines={2} style={styles.cardTitle}>{title}</Text>
          {item.excerpt ? <Text numberOfLines={2} style={styles.cardExcerpt}>{item.excerpt}</Text> : null}
        </View>
        {item.thumbnail_url ? <ContentImage uri={item.thumbnail_url} style={styles.articleThumb} /> : null}
      </View>
      <View style={styles.cardFooter}>
        <Text style={styles.timeText}>{relativeTime(item.published_at ?? item.fetched_at)}</Text>
        <SaveButton isSaved={item.is_saved} onPress={onToggleSave} />
      </View>
    </Pressable>
  );
}

export function SavedCard({
  avatarAccessToken,
  item,
  onOpen,
  onRemove,
}: {
  avatarAccessToken: string;
  item: FeedItem;
  onOpen: () => void;
  onRemove: () => void;
}) {
  return <ArticleCard avatarAccessToken={avatarAccessToken} item={item} onPress={onOpen} onToggleSave={onRemove} />;
}

export function ContentImage({ uri, style, resizeMode = 'cover' }: { uri: string; style: StyleProp<ImageStyle>; resizeMode?: 'cover' | 'contain' }) {
  const [candidate, setCandidate] = useState(0);
  const urls = highResolutionCandidates(uri);
  useEffect(() => setCandidate(0), [uri]);
  return (
    <Image
      onError={() => setCandidate((current) => Math.min(current + 1, urls.length - 1))}
      resizeMode={resizeMode}
      source={{ uri: urls[candidate] }}
      style={style}
    />
  );
}

function highResolutionCandidates(uri: string): string[] {
  if (/\.ytimg\.com\/vi\/[^/]+\/hqdefault\.jpg/.test(uri)) {
    return [uri.replace('hqdefault.jpg', 'maxresdefault.jpg'), uri.replace('hqdefault.jpg', 'sddefault.jpg'), uri];
  }
  if (uri.includes('pbs.twimg.com/') && /[?&]name=small(?:&|$)/.test(uri)) {
    return [uri.replace(/([?&]name=)small(?=&|$)/, '$1large'), uri];
  }
  return [uri];
}

function SaveButton({ isSaved, onPress }: { isSaved: boolean; onPress: () => void }) {
  const { t } = useTranslation('common');
  return (
    <Pressable accessibilityLabel={t(isSaved ? 'unsave' : 'save')} accessibilityRole="button" accessibilityState={{ selected: isSaved }} hitSlop={10} onPress={(event) => { event.stopPropagation(); onPress(); }} style={styles.saveButton}>
      {isSaved ? <MaterialCommunityIcons color={colors.textStrong} name="bookmark" size={20} /> : <Feather color={colors.textMuted} name="bookmark" size={20} />}
    </Pressable>
  );
}

function PostAvatar({ accessToken, avatarUrl, fallbackLabel }: { accessToken: string; avatarUrl?: string | null; fallbackLabel: string }) {
  const imageSource = useAvatarImageSource(avatarUrl, accessToken);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [accessToken, avatarUrl]);
  return (
    <View style={styles.postAvatar}>
      {imageSource && !failed ? (
        <Image
          fadeDuration={0}
          onError={() => setFailed(true)}
          resizeMethod="resize"
          resizeMode="cover"
          source={imageSource}
          style={styles.postAvatarImage}
        />
      ) : (
        <Text style={styles.postAvatarText}>{fallbackLabel.slice(0, 1).toUpperCase()}</Text>
      )}
    </View>
  );
}

function SourceIdentity({ accessToken, avatarUrl, kind, label }: { accessToken: string; avatarUrl?: string | null; kind: string; label: string }) {
  const imageSource = useAvatarImageSource(avatarUrl, accessToken);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [accessToken, avatarUrl]);
  return (
    <View style={styles.sourceIdentity}>
      <View style={styles.sourceMark}>
        {imageSource && !failed ? (
          <Image
            fadeDuration={0}
            onError={() => setFailed(true)}
            resizeMethod="resize"
            resizeMode="cover"
            source={imageSource}
            style={styles.sourceMarkImage}
          />
        ) : (
          <MaterialCommunityIcons color="#1A1C1B" name={sourceIcon(kind)} size={11} />
        )}
      </View>
      <Text numberOfLines={1} style={styles.sourceIdentityText}>{displaySourceLabel(kind, label)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  articleCard: { borderBottomColor: colors.border, borderBottomWidth: 1, marginHorizontal: 24, paddingTop: 22, paddingBottom: 15 },
  articleBody: { flexDirection: 'row', gap: 17 },
  articleCopy: { flex: 1, minWidth: 0 },
  articleThumb: { backgroundColor: '#EEEEEC', borderColor: '#E5E5E3', borderRadius: 8, borderWidth: 1, height: 94, marginTop: 3, width: 86 },
  cardMeta: { alignItems: 'center', flexDirection: 'row', gap: 8, marginBottom: 11 },
  cardTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: '700', letterSpacing: -0.35, lineHeight: 28 },
  cardExcerpt: { color: colors.textSecondary, fontSize: 13, lineHeight: 22, marginTop: 7 },
  cardFooter: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 },
  timeText: { color: colors.textSecondary, fontSize: 11, fontWeight: '500' },
  saveButton: { alignItems: 'center', height: 40, justifyContent: 'center', width: 44, marginRight: -10 },
  videoCard: { borderBottomColor: colors.border, borderBottomWidth: 1, marginHorizontal: 24, paddingTop: 22, paddingBottom: 15 },
  videoImage: { backgroundColor: '#E8E8E6', borderColor: '#E5E5E3', borderRadius: 12, borderWidth: 1, aspectRatio: 16 / 9, width: '100%' },
  videoSource: { marginTop: 12 },
  videoTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: '600', lineHeight: 28, marginTop: 8 },
  postCard: { borderBottomColor: colors.border, borderBottomWidth: 1, marginHorizontal: 24, paddingTop: 22, paddingBottom: 15 },
  postHeader: { alignItems: 'center', flexDirection: 'row', gap: 10 },
  postAvatar: { alignItems: 'center', backgroundColor: '#E8E8E6', borderRadius: 18, height: 36, justifyContent: 'center', overflow: 'hidden', width: 36 },
  postAvatarImage: { height: 36, width: 36 },
  postAvatarText: { color: '#333333', fontSize: 15, fontWeight: '700' },
  postCopy: { flex: 1 },
  postAuthor: { color: colors.textPrimary, fontSize: 13, fontWeight: '600' },
  postHandle: { color: colors.textMuted, fontSize: 10, marginTop: 1 },
  postText: { color: colors.textPrimary, fontSize: 14, lineHeight: 26, marginTop: 12 },
  postImage: { alignSelf: 'stretch', backgroundColor: '#E8E8E6', borderColor: '#E5E5E3', borderRadius: 12, borderWidth: 1, height: 190, marginTop: 12 },
  sourceIdentity: { alignItems: 'center', flex: 1, flexDirection: 'row', gap: 6 },
  sourceMark: { alignItems: 'center', backgroundColor: '#E2E3E1', borderRadius: 6, height: 22, justifyContent: 'center', overflow: 'hidden', width: 22 },
  sourceMarkImage: { height: 22, width: 22 },
  sourceIdentityText: { color: '#666e63', flex: 1, fontSize: 11, fontWeight: '400' },
  threadBadge: { alignSelf: 'flex-start', backgroundColor: colors.surfaceSubtle, borderRadius: 6, marginTop: 12, paddingHorizontal: 7, paddingVertical: 4 },
  threadText: { color: colors.textSecondary, fontSize: 10 },
  replyContext: { color: colors.textMuted, fontSize: 11, marginTop: 9 },
  quote: { borderColor: colors.border, borderRadius: 12, borderWidth: 1, paddingHorizontal: 13, paddingVertical: 11, marginTop: 12 },
  quoteLabel: { color: colors.textMuted, fontSize: 10, marginBottom: 5 },
  quoteAuthor: { color: colors.textPrimary, fontSize: 12, fontWeight: '600' },
  quoteText: { color: colors.textSecondary, fontSize: 12, lineHeight: 21, marginTop: 5 },
  postLink: { alignItems: 'center', flexDirection: 'row', gap: 5, minHeight: 36, marginTop: 6 },
  postLinkText: { color: colors.textMuted, fontSize: 11 },
  translationFailure: { alignItems: 'center', flexDirection: 'row', gap: 8, marginTop: 8 },
  translationFailureText: { color: '#B42318', fontSize: 12, fontWeight: '600' },
  translationRetry: { borderColor: '#B42318', borderRadius: 6, borderWidth: 1, minHeight: 30, paddingHorizontal: 9, paddingVertical: 5 },
  translationRetryText: { color: '#8A1C13', fontSize: 12, fontWeight: '700' },
});
