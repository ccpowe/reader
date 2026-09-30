import { useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import type { RankingItem } from '../lib/api';
import { rankingBodyText } from '../domain/rankingTranslationProjection';
import { formatCount, relativeTime } from '../domain/formatting';
import { useTranslation } from '../i18n';
import { colors, radii } from '../ui/tokens';

export function XRankingContent({ item, translated, onRetryTranslation, translationBusy, translationRetryNeeded }: {
  item: RankingItem;
  translated: boolean;
  onRetryTranslation?: () => void;
  translationBusy?: boolean;
  translationRetryNeeded?: boolean;
}) {
  const { t } = useTranslation('feed');
  const [expanded, setExpanded] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const body = rankingBodyText(item, translated && !showOriginal);
  const translationReady = translated && rankingBodyText(item, true) !== (item.body_text ?? '');
  const failed = (item.body_segments ?? []).some((segment) => segment.translation_status === 'failed');
  const image = item.image_urls?.[0] || item.video_thumbnail_url;
  const metrics = [
    { icon: 'heart' as const, value: item.score, label: t('xLikes') },
    { icon: 'repeat' as const, value: item.retweet_count, label: t('xReposts') },
    { icon: 'message-circle' as const, value: item.comments, label: t('xComments') },
    { icon: 'bookmark' as const, value: item.bookmark_count, label: t('xBookmarks') },
  ].filter((metric) => metric.value != null);
  return <View style={styles.content}>
    <View style={styles.authorRow}>
      <Text numberOfLines={1} style={styles.author}>{item.author || item.author_handle || 'X'}</Text>
      {item.author_handle ? <Text numberOfLines={1} style={styles.handle}>@{item.author_handle.replace(/^@/, '')}</Text> : null}
      {item.published_at ? <Text style={styles.time}>· {relativeTime(item.published_at)}</Text> : null}
    </View>
    {item.body_text ? <>
      <Text numberOfLines={expanded ? undefined : 5} style={styles.body}>{body}</Text>
      <View style={styles.actions}>
        <Pressable accessibilityRole="button" onPress={(event) => { event.stopPropagation(); setExpanded((value) => !value); }} style={styles.action}><Text style={styles.actionText}>{t(expanded ? 'xCollapse' : 'xExpand')}</Text></Pressable>
        {translated && (failed || translationRetryNeeded) && onRetryTranslation ? <Pressable accessibilityRole="button" disabled={translationBusy} onPress={(event) => { event.stopPropagation(); onRetryTranslation(); }} style={styles.action}><Text style={styles.actionText}>{t('xRetryTranslation')}</Text></Pressable> : null}
        {translationReady ? <Pressable accessibilityRole="button" onPress={(event) => { event.stopPropagation(); setShowOriginal((value) => !value); }} style={styles.action}><Text style={styles.actionText}>{t(showOriginal ? 'xShowTranslation' : 'xShowOriginal')}</Text></Pressable> : null}
      </View>
    </> : null}
    {image ? <View style={styles.media}><Image resizeMode="cover" source={{ uri: image }} style={styles.image} />{item.video_thumbnail_url && image === item.video_thumbnail_url ? <View style={styles.videoBadge}><Feather color="#fff" name="video" size={14} /><Text style={styles.videoText}>{t('xVideoPreview')}</Text></View> : null}</View> : null}
    {metrics.length ? <View style={styles.metrics}>{metrics.map((metric) => <View key={metric.label} style={styles.metric}><Feather color={colors.textMuted} name={metric.icon} size={13} /><Text style={styles.metricText}>{metric.label} {formatCount(metric.value ?? null)}</Text></View>)}</View> : null}
  </View>;
}

const styles = StyleSheet.create({
  content: { flex: 1, minWidth: 0 },
  authorRow: { alignItems: 'baseline', flexDirection: 'row', gap: 5 },
  author: { color: colors.textStrong, flexShrink: 1, fontSize: 14, fontWeight: '700' },
  handle: { color: colors.textMuted, flexShrink: 1, fontSize: 12 },
  time: { color: colors.textMuted, fontSize: 11 },
  body: { color: colors.textPrimary, fontSize: 15, lineHeight: 23, marginTop: 9 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 16, marginTop: 3 },
  action: { justifyContent: 'center', minHeight: 32 },
  actionText: { color: colors.textMuted, fontSize: 12 },
  media: { borderRadius: radii.sm, marginTop: 10, overflow: 'hidden' },
  image: { backgroundColor: colors.imagePlaceholder, height: 190, width: '100%' },
  videoBadge: { alignItems: 'center', backgroundColor: '#000a', borderRadius: 5, bottom: 8, flexDirection: 'row', gap: 4, left: 8, padding: 5, position: 'absolute' },
  videoText: { color: '#fff', fontSize: 11 },
  metrics: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 12 },
  metric: { alignItems: 'center', flexDirection: 'row', gap: 4 },
  metricText: { color: colors.textMuted, fontSize: 11 },
});
