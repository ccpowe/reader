import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentImage } from '../components/FeedCards';
import { ReaderLogo } from '../components/PageHeader';
import type { FeedItem } from '../lib/api';
import { relativeTime } from '../domain/formatting';

export const homePreviewItems: FeedItem[] = [
  {
    author_name: 'Junra',
    content_id: 'draft-article-1',
    excerpt: '从产品设计到实现，怎样让一套界面在不同尺寸的手机上依然保持清晰、安静和可靠。',
    external_url: 'https://example.com/design-systems',
    fetched_at: new Date(Date.now() - 42 * 60 * 1000).toISOString(),
    is_saved: false,
    published_at: new Date(Date.now() - 58 * 60 * 1000).toISOString(),
    source_avatar_url: null,
    source_id: 'draft-source-1',
    source_kind: 'rss',
    source_name: 'Design Notes',
    thumbnail_url: 'https://images.unsplash.com/photo-1558655146-9f40138edfeb?auto=format&fit=crop&w=480&q=82',
    title: '好的阅读界面，应该先让内容找到自己的位置',
    translated_title: null,
    translation_locale: null,
    translation_status: null,
  },
  {
    author_name: 'Oliver Burkeman',
    content_id: 'draft-article-2',
    excerpt: '当信息越来越多，真正稀缺的不是内容，而是决定把注意力放在哪里的能力。',
    external_url: 'https://example.com/attention',
    fetched_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    is_saved: true,
    published_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    source_avatar_url: null,
    source_id: 'draft-source-2',
    source_kind: 'rss',
    source_name: 'The Imperfectionist',
    thumbnail_url: null,
    title: '把待读列表当作一条河，而不是必须清空的收件箱',
    translated_title: null,
    translation_locale: null,
    translation_status: null,
  },
  {
    author_name: 'MKBHD',
    content_id: 'draft-video-1',
    excerpt: null,
    external_url: 'https://example.com/video',
    fetched_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    is_saved: false,
    published_at: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
    source_avatar_url: null,
    source_id: 'draft-source-3',
    source_kind: 'youtube',
    source_name: 'MKBHD',
    thumbnail_url: 'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=960&q=82',
    title: '一周科技产品观察：哪些细节真正改善了日常体验',
    translated_title: null,
    translation_locale: null,
    translation_status: null,
  },
];

export function HomeUiDraftPreview() {
  const [selectedCategory, setSelectedCategory] = useState('全部');
  const [items, setItems] = useState(homePreviewItems);
  const categories = ['全部', '设计', 'AI', '科技'];
  const visibleItems = items;

  function toggleSaved(contentId: string) {
    setItems((current) => current.map((item) => (
      item.content_id === contentId ? { ...item, is_saved: !item.is_saved } : item
    )));
  }

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safeArea}>
      <View style={styles.phone}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <View style={styles.header}>
            <View style={styles.logo}><ReaderLogo /></View>
            <View>
              <Text style={styles.eyebrow}>READER</Text>
              <Text style={styles.title}>今日</Text>
            </View>
          </View>
          <View style={styles.filters}>
            <ScrollView contentContainerStyle={styles.filterRow} horizontal showsHorizontalScrollIndicator={false}>
              {categories.map((category) => (
                <DraftChip
                  active={selectedCategory === category}
                  key={category}
                  label={category}
                  onPress={() => setSelectedCategory(category)}
                />
              ))}
            </ScrollView>
            <Pressable accessibilityRole="button" style={styles.channelButton}>
              <MaterialCommunityIcons color="#34342F" name="tune-variant" size={18} />
              <Text style={styles.channelText}>频道</Text>
            </Pressable>
          </View>
          <View style={styles.sectionHeader}>
            <View>
              <Text style={styles.sectionTitle}>最新内容</Text>
              <Text style={styles.sectionSubtitle}>来自你订阅的频道</Text>
            </View>
            <Pressable accessibilityRole="button" style={styles.refreshButton}>
              <MaterialCommunityIcons color="#6D6A63" name="refresh" size={16} />
              <Text style={styles.refreshText}>刷新</Text>
            </Pressable>
          </View>
          {visibleItems.map((item) => (
            <DraftArticleCard
              item={item}
              key={item.content_id}
              onPress={() => undefined}
              onToggleSave={() => toggleSaved(item.content_id)}
            />
          ))}
        </ScrollView>
        <DraftTabBar />
      </View>
    </SafeAreaView>
  );
}

function DraftChip({ active, label, onPress }: { active: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={[styles.chip, active && styles.chipActive]}
    >
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </Pressable>
  );
}

function DraftArticleCard({ item, onPress, onToggleSave }: { item: FeedItem; onPress: () => void; onToggleSave: () => void }) {
  const isVideo = item.source_kind === 'youtube';
  if (isVideo) {
    return (
      <Pressable onPress={onPress} style={styles.videoCard}>
        {item.thumbnail_url ? <ContentImage uri={item.thumbnail_url} style={styles.videoImage} /> : null}
        <Text style={styles.sourceText}>{item.source_name ?? '视频'}</Text>
        <Text style={styles.videoTitle}>{item.title}</Text>
        <View style={styles.cardFooter}>
          <Text style={styles.timeText}>{relativeTime(item.published_at ?? item.fetched_at)}</Text>
          <DraftSaveButton isSaved={item.is_saved} onPress={onToggleSave} />
        </View>
      </Pressable>
    );
  }
  return (
    <Pressable onPress={onPress} style={styles.articleCard}>
      <View style={styles.articleBody}>
        <View style={styles.articleCopy}>
          <View style={styles.sourceRow}>
            <View style={styles.sourceMark}>
              <MaterialCommunityIcons color="#34342F" name="rss" size={12} />
            </View>
            <Text numberOfLines={1} style={styles.sourceText}>
              {[item.source_name, item.author_name].filter(Boolean).join(' · ')}
            </Text>
          </View>
          <Text numberOfLines={2} style={styles.cardTitle}>{item.title}</Text>
          {item.excerpt ? <Text numberOfLines={2} style={styles.cardExcerpt}>{item.excerpt}</Text> : null}
        </View>
        {item.thumbnail_url ? <ContentImage uri={item.thumbnail_url} style={styles.articleThumb} /> : null}
      </View>
      <View style={styles.cardFooter}>
        <Text style={styles.timeText}>{relativeTime(item.published_at ?? item.fetched_at)}</Text>
        <DraftSaveButton isSaved={item.is_saved} onPress={onToggleSave} />
      </View>
    </Pressable>
  );
}

function DraftSaveButton({ isSaved, onPress }: { isSaved: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityLabel={isSaved ? '取消收藏' : '收藏'}
      accessibilityRole="button"
      accessibilityState={{ selected: isSaved }}
      hitSlop={6}
      onPress={onPress}
      style={({ pressed }) => [styles.saveButton, isSaved && styles.saveButtonActive, pressed && styles.pressed]}
    >
      <MaterialCommunityIcons color={isSaved ? '#FFFEFA' : '#6D6A63'} name={isSaved ? 'bookmark' : 'bookmark-outline'} size={19} />
    </Pressable>
  );
}

function DraftTabBar() {
  const tabs: [keyof typeof MaterialCommunityIcons.glyphMap, string][] = [
    ['home-variant-outline', '今日'],
    ['chart-box-outline', '榜单'],
    ['rss', '订阅'],
    ['bookmark-outline', '收藏'],
    ['account-outline', '我的'],
  ];
  return (
    <View style={styles.tabBar}>
      {tabs.map(([icon, label], index) => (
        <View key={label} style={[styles.tabItem, index === 0 && styles.tabItemActive]}>
          <MaterialCommunityIcons color={index === 0 ? '#20211E' : '#8B877D'} name={icon} size={23} />
          <Text style={[styles.tabLabel, index === 0 && styles.tabLabelActive]}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { alignItems: 'center', backgroundColor: '#E9E5DC', flex: 1 },
  phone: { backgroundColor: '#FBF9F4', flex: 1, maxWidth: 430, width: '100%' },
  content: { paddingBottom: 28 },
  header: { alignItems: 'center', flexDirection: 'row', gap: 12, paddingBottom: 14, paddingHorizontal: 20, paddingTop: 18 },
  logo: { alignItems: 'center', backgroundColor: '#FFFEFA', borderColor: '#E8E3D9', borderRadius: 14, borderWidth: 1, height: 42, justifyContent: 'center', overflow: 'hidden', width: 42 },
  eyebrow: { color: '#8B877D', fontSize: 10, fontWeight: '700', letterSpacing: 1.5, lineHeight: 13 },
  title: { color: '#20211E', fontSize: 28, fontWeight: '700', letterSpacing: -0.8, lineHeight: 32 },
  filters: { alignItems: 'center', flexDirection: 'row', gap: 8, paddingHorizontal: 20 },
  filterRow: { gap: 8, paddingRight: 8 },
  chip: { alignItems: 'center', backgroundColor: '#F1EEE7', borderRadius: 999, justifyContent: 'center', minHeight: 44, paddingHorizontal: 15 },
  chipActive: { backgroundColor: '#22231F' },
  chipText: { color: '#6D6A63', fontSize: 13, fontWeight: '600' },
  chipTextActive: { color: '#FFFEFA' },
  channelButton: { alignItems: 'center', backgroundColor: '#FFFEFA', borderColor: '#DFDBD1', borderRadius: 999, borderWidth: 1, flexDirection: 'row', gap: 5, minHeight: 44, paddingHorizontal: 13 },
  channelText: { color: '#34342F', fontSize: 13, fontWeight: '700' },
  sectionHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', paddingBottom: 8, paddingHorizontal: 20, paddingTop: 28 },
  sectionTitle: { color: '#20211E', fontSize: 23, fontWeight: '700', letterSpacing: -0.65, lineHeight: 29 },
  sectionSubtitle: { color: '#8B877D', fontSize: 12, marginTop: 2 },
  refreshButton: { alignItems: 'center', backgroundColor: '#F1EEE7', borderRadius: 999, flexDirection: 'row', gap: 4, minHeight: 36, paddingHorizontal: 11 },
  refreshText: { color: '#6D6A63', fontSize: 12, fontWeight: '600' },
  articleCard: { borderBottomColor: '#E8E3D9', borderBottomWidth: 1, marginHorizontal: 20, paddingVertical: 18 },
  articleBody: { flexDirection: 'row', gap: 14 },
  articleCopy: { flex: 1, minWidth: 0 },
  articleThumb: { backgroundColor: '#EEEAE1', borderRadius: 14, height: 84, marginTop: 1, width: 84 },
  sourceRow: { alignItems: 'center', flexDirection: 'row', gap: 6, marginBottom: 7 },
  sourceMark: { alignItems: 'center', backgroundColor: '#E3DED3', borderRadius: 7, height: 20, justifyContent: 'center', width: 20 },
  sourceText: { color: '#6D6A63', flexShrink: 1, fontSize: 11, fontWeight: '600' },
  cardTitle: { color: '#20211E', fontSize: 17, fontWeight: '700', letterSpacing: -0.32, lineHeight: 23 },
  cardExcerpt: { color: '#6D6A63', fontSize: 13, lineHeight: 19, marginTop: 5 },
  cardFooter: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 },
  timeText: { color: '#8B877D', fontSize: 11, fontWeight: '600' },
  saveButton: { alignItems: 'center', backgroundColor: '#F1EEE7', borderRadius: 18, height: 36, justifyContent: 'center', width: 36 },
  saveButtonActive: { backgroundColor: '#34342F' },
  pressed: { opacity: 0.68, transform: [{ scale: 0.96 }] },
  videoCard: { backgroundColor: '#FFFEFA', borderRadius: 22, marginHorizontal: 20, marginVertical: 10, padding: 10 },
  videoImage: { backgroundColor: '#EEEAE1', borderRadius: 16, height: 192, width: '100%' },
  videoTitle: { color: '#20211E', fontSize: 18, fontWeight: '700', letterSpacing: -0.3, lineHeight: 24, marginTop: 8 },
  tabBar: { backgroundColor: '#FFFEFA', borderTopColor: '#E8E3D9', borderTopWidth: 1, flexDirection: 'row', height: 64, paddingBottom: 8, paddingHorizontal: 8, paddingTop: 8 },
  tabItem: { alignItems: 'center', borderRadius: 18, flex: 1, gap: 3, justifyContent: 'center' },
  tabItemActive: { backgroundColor: '#F1EEE7' },
  tabLabel: { color: '#8B877D', fontSize: 11, fontWeight: '500' },
  tabLabelActive: { color: '#20211E', fontWeight: '700' },
});
