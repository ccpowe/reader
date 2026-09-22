import { useEffect, useMemo, useState } from 'react';
import {
  Modal,
  Pressable,
  SectionList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { SafeAreaView } from 'react-native-safe-area-context';

import { SourceAvatar } from './SourceAvatar';
import type { SourceListItem } from '../lib/api';
import { folderLabel, sourceFolder, sourceFolders } from '../domain/folders';
import { sourceDisplayName, sourceLocationLabel, sourceSecondaryLabel } from '../domain/source';
import { colors, radii, spacing, touchTarget } from '../ui/tokens';
import { i18n, useTranslation } from '../i18n';

type ChannelSection = {
  data: SourceListItem[];
  title: string;
};

export function ChannelPickerModal({
  accessToken,
  onClose,
  onSelect,
  onToggleHome,
  pendingHomeSubscriptionIds,
  preferredFolder,
  selectedSourceId,
  sources,
  visible,
}: {
  accessToken: string;
  onClose: () => void;
  onSelect: (source: SourceListItem | null) => void;
  onToggleHome: (source: SourceListItem, includeInHome: boolean) => void;
  pendingHomeSubscriptionIds: ReadonlySet<string>;
  preferredFolder: string | null;
  selectedSourceId: string | null;
  sources: SourceListItem[];
  visible: boolean;
}) {
  const { t } = useTranslation('feed');
  const language = i18n.resolvedLanguage ?? i18n.language;
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (visible) setQuery('');
  }, [visible]);

  const sections = useMemo<ChannelSection[]>(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase(language);
    const visibleSources = sources.filter((source) => {
      const searchable = [
        sourceDisplayName(source),
        sourceLocationLabel(source.canonical_url),
        folderLabel(sourceFolder(source), language),
      ].join(' ').toLocaleLowerCase(language);
      return searchable.includes(normalizedQuery);
    });
    const folders = sourceFolders(visibleSources);
    if (preferredFolder && folders.includes(preferredFolder)) {
      folders.splice(folders.indexOf(preferredFolder), 1);
      folders.unshift(preferredFolder);
    }
    return folders.map((folder) => ({
      data: visibleSources
        .filter((source) => sourceFolder(source) === folder)
        .sort((left, right) => sourceDisplayName(left).localeCompare(sourceDisplayName(right), language)),
      title: folderLabel(folder, language),
    }));
  }, [preferredFolder, query, sources, language]);

  function select(source: SourceListItem | null) {
    onSelect(source);
    onClose();
  }

  return (
    <Modal
      animationType="slide"
      onRequestClose={onClose}
      statusBarTranslucent
      transparent
      visible={visible}
    >
      <View style={styles.backdrop}>
        <Pressable
          accessibilityLabel={t('closeChannelPicker')}
          accessibilityRole="button"
          onPress={onClose}
          style={styles.dismissArea}
        />
        <SafeAreaView edges={['bottom']} style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View>
              <Text style={styles.title}>{t('chooseChannel')}</Text>
              <Text style={styles.subtitle}>{t('chooseChannelHint')}</Text>
            </View>
            <Pressable
              accessibilityLabel={t('close')}
              accessibilityRole="button"
              hitSlop={8}
              onPress={onClose}
              style={styles.closeButton}
            >
              <MaterialCommunityIcons color={colors.textSecondary} name="close" size={22} />
            </Pressable>
          </View>
          <View style={styles.searchBox}>
            <MaterialCommunityIcons color={colors.textTertiary} name="magnify" size={20} />
            <TextInput
              accessibilityLabel={t('searchChannel')}
              autoCapitalize="none"
              autoCorrect={false}
              onChangeText={setQuery}
              placeholder={t('searchChannelPlaceholder')}
              placeholderTextColor={colors.textMuted}
              returnKeyType="search"
              style={styles.searchInput}
              value={query}
            />
            {query ? (
              <Pressable
                accessibilityLabel={t('clearSearch')}
                accessibilityRole="button"
                hitSlop={8}
                onPress={() => setQuery('')}
              >
                <MaterialCommunityIcons color={colors.textTertiary} name="close-circle" size={19} />
              </Pressable>
            ) : null}
          </View>
          <SectionList
            contentContainerStyle={styles.listContent}
            keyboardShouldPersistTaps="handled"
            keyExtractor={(item) => item.source_id}
            renderItem={({ item }) => {
              const selected = selectedSourceId === item.source_id;
              const includeInHome = item.include_in_home !== false;
              const newCount = typeof item.new_count === 'number' && item.new_count > 0 ? item.new_count : 0;
              const updatingHome = pendingHomeSubscriptionIds.has(item.subscription_id);
              const secondaryLabel = sourceSecondaryLabel(item);
              return (
                <View
                  style={[
                    styles.channelRow,
                    selected && styles.channelRowSelected,
                  ]}
                >
                  <Pressable
                    accessibilityLabel={t(newCount > 0 ? 'channelNameWithUpdates' : 'channelName', { count: newCount, name: sourceDisplayName(item) })}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    onPress={() => select(item)}
                    style={({ pressed }) => [styles.channelMain, pressed && styles.channelRowPressed]}
                  >
                    <SourceAvatar accessToken={accessToken} source={item} />
                    <View style={styles.channelCopy}>
                      <Text numberOfLines={1} style={styles.channelName}>{sourceDisplayName(item)}</Text>
                      {secondaryLabel ? <Text numberOfLines={1} style={styles.channelHost}>{secondaryLabel}</Text> : null}
                    </View>
                    {newCount > 0 ? (
                      <View style={styles.updateBadge}>
                        <Text numberOfLines={1} style={styles.updateBadgeText}>{newCount > 999 ? '999+' : newCount}</Text>
                      </View>
                    ) : null}
                  </Pressable>
                  <Pressable
                    accessibilityLabel={t(includeInHome ? 'hideChannelFromHome' : 'showChannelOnHome', { name: sourceDisplayName(item) })}
                    accessibilityRole="checkbox"
                    accessibilityState={{ busy: updatingHome, checked: includeInHome, disabled: updatingHome }}
                    disabled={updatingHome}
                    hitSlop={4}
                    onPress={() => onToggleHome(item, !includeInHome)}
                    style={({ pressed }) => [styles.homeCheckbox, pressed && styles.channelRowPressed]}
                  >
                    <MaterialCommunityIcons
                      color={includeInHome ? colors.textStrong : colors.textMuted}
                      name={includeInHome ? 'checkbox-marked' : 'checkbox-blank-outline'}
                      size={24}
                    />
                  </Pressable>
                </View>
              );
            }}
            renderSectionHeader={({ section }) => (
              <Text style={styles.sectionTitle}>{section.title}</Text>
            )}
            sections={sections}
            showsVerticalScrollIndicator={false}
            style={styles.list}
            stickySectionHeadersEnabled={false}
            ListEmptyComponent={(
              <View style={styles.emptyState}>
                <MaterialCommunityIcons color={colors.textMuted} name="rss" size={28} />
                <Text style={styles.emptyText}>
                  {t(sources.length ? 'channelsNoMatches' : 'channelsEmpty')}
                </Text>
              </View>
            )}
            ListHeaderComponent={query ? null : (
              <Pressable
                accessibilityLabel={t('returnToFolderContent')}
                accessibilityRole="button"
                accessibilityState={{ selected: selectedSourceId === null }}
                onPress={() => select(null)}
                style={({ pressed }) => [
                  styles.allChannelsRow,
                  selectedSourceId === null && styles.channelRowSelected,
                  pressed && styles.channelRowPressed,
                ]}
              >
                <View style={styles.allChannelsIcon}>
                  <MaterialCommunityIcons color={colors.textStrong} name="view-grid-outline" size={21} />
                </View>
                <View style={styles.channelCopy}>
                  <Text style={styles.channelName}>{t('returnToFolderContent')}</Text>
                  <Text style={styles.channelHost}>{t('clearSingleChannelFilter')}</Text>
                </View>
                {selectedSourceId === null ? (
                  <MaterialCommunityIcons color={colors.textStrong} name="check-circle" size={21} />
                ) : null}
              </Pressable>
            )}
          />
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { backgroundColor: 'rgba(17,17,17,0.32)', flex: 1, justifyContent: 'flex-end' },
  dismissArea: { flex: 1 },
  sheet: {
    backgroundColor: colors.background,
    borderTopLeftRadius: radii.sheet,
    borderTopRightRadius: radii.sheet,
    height: '78%',
    paddingHorizontal: 20,
  },
  handle: { alignSelf: 'center', backgroundColor: colors.borderStrong, borderRadius: 3, height: 5, marginBottom: 16, marginTop: 9, width: 40 },
  header: { alignItems: 'flex-start', flexDirection: 'row', justifyContent: 'space-between' },
  title: { color: colors.textStrong, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  subtitle: { color: colors.textTertiary, fontSize: 13, marginTop: 4 },
  closeButton: { alignItems: 'center', height: touchTarget, justifyContent: 'center', width: touchTarget },
  searchBox: { alignItems: 'center', backgroundColor: colors.surfaceSubtle, borderRadius: radii.md, flexDirection: 'row', gap: spacing.sm, marginTop: 18, paddingHorizontal: 13 },
  searchInput: { color: colors.textStrong, flex: 1, fontSize: 15, minHeight: 48, paddingVertical: 11 },
  list: { flex: 1 },
  listContent: { paddingBottom: spacing.xl, paddingTop: spacing.md },
  sectionTitle: { color: colors.textTertiary, fontSize: 12, fontWeight: '700', letterSpacing: 0.7, paddingBottom: 7, paddingLeft: 4, paddingTop: 16 },
  allChannelsRow: { alignItems: 'center', backgroundColor: colors.surface, borderColor: colors.border, borderRadius: radii.md, borderWidth: 1, flexDirection: 'row', gap: spacing.md, marginBottom: 4, minHeight: 70, paddingHorizontal: 14, paddingVertical: 12 },
  allChannelsIcon: { alignItems: 'center', backgroundColor: colors.surfaceMuted, borderRadius: 10, height: 42, justifyContent: 'center', width: 42 },
  channelRow: { alignItems: 'center', borderRadius: radii.md, flexDirection: 'row', gap: spacing.md, minHeight: 66, paddingHorizontal: 12, paddingVertical: 10 },
  channelMain: { alignItems: 'center', flex: 1, flexDirection: 'row', gap: spacing.md, minWidth: 0 },
  channelRowSelected: { backgroundColor: colors.surfaceSubtle },
  channelRowPressed: { opacity: 0.7 },
  channelCopy: { flex: 1 },
  channelName: { color: colors.textStrong, fontSize: 15, fontWeight: '600' },
  channelHost: { color: colors.textTertiary, fontSize: 12, marginTop: 3 },
  updateBadge: { alignItems: 'center', backgroundColor: colors.textStrong, borderRadius: radii.pill, justifyContent: 'center', maxWidth: 54, minHeight: 22, minWidth: 22, paddingHorizontal: 6 },
  updateBadgeText: { color: colors.surface, fontSize: 11, fontWeight: '700' },
  homeCheckbox: { alignItems: 'center', height: touchTarget, justifyContent: 'center', marginRight: -8, width: touchTarget },
  emptyState: { alignItems: 'center', gap: spacing.md, justifyContent: 'center', paddingHorizontal: spacing.xl, paddingVertical: 48 },
  emptyText: { color: colors.textTertiary, fontSize: 14, lineHeight: 21, textAlign: 'center' },
});
