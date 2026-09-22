import { memo, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import {
  ActivityIndicator,
  type ListRenderItemInfo,
  type ViewStyle,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Feather, MaterialCommunityIcons } from '@expo/vector-icons';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';
import Reanimated, {
  type ScrollHandlerProcessed,
  type SharedValue,
} from 'react-native-reanimated';

import { AddSourceSheet } from '../components/AddSourceSheet';
import { BottomSheetModal } from '../components/BottomSheetModal';
import { ActionConfirm } from '../components/ActionConfirm';
import { EmptyState, ErrorState, listFeedbackStyles, LoadingBlock } from '../components/FeedbackState';
import { SearchField } from '../components/SearchField';
import { PageHeaderContent } from '../components/PageHeader';
import { SourceAvatar } from '../components/SourceAvatar';
import { SourceFolderChoices } from '../components/SourceFolderChoices';
import {
  removeSourceSubscription,
  type SourceListItem,
  updateSourceSubscriptionFolder,
} from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import type { ReaderRuntimeContext } from '../lib/connection';
import { useReaderRuntime } from '../lib/connection/react';
import { useCollapsingChrome, useChromeStyle } from '../hooks/useCollapsingChrome';
import { useSources } from '../hooks/useSources';
import { invalidateAfterSourceMutation } from '../state/invalidation';
import { readerQueryKeys } from '../state/queryClient';
import {
  UNCATEGORIZED_FOLDER,
  folderLabel,
  sourceFolder,
} from '../domain/folders';
import { useSourceFolders } from '../hooks/useSourceFolders';
import {
  sourceDisplayName,
  sourceHealthLabel,
  sourceSecondaryLabel,
} from '../domain/source';
import { PAGE_HEADER_HEIGHT, READER_TAB_BAR_HEIGHT, SCREEN_HORIZONTAL_PADDING, SCREEN_LIST_BOTTOM_PADDING } from '../ui/layout';
import { i18n, useTranslation } from '../i18n';
import type { ListPositionRegistry } from '../domain/listMemory';
import { useListPositionMemory } from '../hooks/useListPositionMemory';

const SOURCES_HEADER_HEIGHT = PAGE_HEADER_HEIGHT;
const SOURCES_CONTROLS_HEIGHT = 110;

type SourceRow =
  | { id: string; kind: 'category'; folder: string; items: SourceListItem[]; tone: number }
  | { groupEnd: boolean; id: string; item: SourceListItem; kind: 'source' };


type PendingRemove = {
  context: ReaderRuntimeContext;
  item: SourceListItem;
};

export function SourcesScreen({
  active,
  chromeProgress,
  onOpenSource,
  onProfile,
  session,
}: {
  active: boolean;
  chromeProgress: SharedValue<number>;
  onOpenSource: (source: SourceListItem) => void;
  onProfile?: () => void;
  session: Session;
}) {
  const [query, setQuery] = useState('');
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  return <SourcesScreenContent
    active={active}
    chromeProgress={chromeProgress}
    onChangeQuery={setQuery}
    onOpenSource={onOpenSource}
    onProfile={onProfile}
    onSelectFolder={setSelectedFolder}
    positionRegistry={positionRegistry}
    query={query}
    selectedFolder={selectedFolder}
    session={session}
  />;
}

function SourcesScreenContent({
  active,
  chromeProgress,
  onChangeQuery,
  onOpenSource,
  onProfile,
  onSelectFolder,
  positionRegistry,
  query,
  selectedFolder,
  session,
}: {
  active: boolean;
  chromeProgress: SharedValue<number>;
  onChangeQuery: (query: string) => void;
  onOpenSource: (source: SourceListItem) => void;
  onProfile?: () => void;
  onSelectFolder: (folder: string | null) => void;
  positionRegistry: ListPositionRegistry;
  query: string;
  selectedFolder: string | null;
  session: Session;
}) {
  const { t } = useTranslation('feed');
  const language = i18n.resolvedLanguage ?? i18n.language;
  const [showAddSheet, setShowAddSheet] = useState(false);
  const [addSheetKey, setAddSheetKey] = useState(0);
  const [deletingSubscriptionId, setDeletingSubscriptionId] = useState<string | null>(null);
  const [searchFocused, setSearchFocused] = useState(false);
  const [menuSource, setMenuSource] = useState<SourceListItem | null>(null);
  const [editingSource, setEditingSource] = useState<SourceListItem | null>(null);
  const [editFolder, setEditFolder] = useState(UNCATEGORIZED_FOLDER);
  const [newEditFolder, setNewEditFolder] = useState('');
  const [folderError, setFolderError] = useState<Error | true | null>(null);
  const [updatingFolder, setUpdatingFolder] = useState(false);
  const [removeConfirmation, setRemoveConfirmation] = useState<PendingRemove | null>(null);
  const [removeTriggerId, setRemoveTriggerId] = useState<string | null>(null);
  const removeTriggerRef = useRef<View | null>(null);
  const confirmationWasVisible = useRef(false);
  const menuWasVisible = useRef(false);

  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const sourcesQuery = useSources(session, active, true);
  const refetchSources = sourcesQuery.refetch;
  const items = sourcesQuery.items;
  const loading = sourcesQuery.loading;

  useEffect(() => {
    if (!removeConfirmation || isRuntimeContextCurrent(removeConfirmation.context)) return;
    // A runtime switch invalidates the confirmation and any local busy state;
    // no stale mutation result is allowed to keep a row disabled forever.
    setRemoveConfirmation(null);
    setDeletingSubscriptionId(null);
  }, [removeConfirmation, runtime, runtime?.generation, runtime?.identity.server_id]);

  useEffect(() => {
    const visible = removeConfirmation !== null;
    const wasVisible = confirmationWasVisible.current;
    confirmationWasVisible.current = visible;
    if (!wasVisible || visible) return;
    (removeTriggerRef.current as unknown as { focus?: () => void } | null)?.focus?.();
  }, [removeConfirmation]);

  useEffect(() => {
    const visible = menuSource !== null;
    const wasVisible = menuWasVisible.current;
    menuWasVisible.current = visible;
    if (!wasVisible || visible || removeConfirmation) return;
    (removeTriggerRef.current as unknown as { focus?: () => void } | null)?.focus?.();
  }, [menuSource, removeConfirmation]);

  const folders = useSourceFolders(items);
  const retrySources = useCallback(() => {
    return refetchSources();
  }, [refetchSources]);

  useEffect(() => {
    if (selectedFolder !== null && !folders.includes(selectedFolder)) {
      onSelectFolder(null);
    }
  }, [folders, onSelectFolder, selectedFolder]);

  const rows = useMemo<SourceRow[]>(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (selectedFolder === null && !normalizedQuery) {
      return folders.map((folder, tone) => ({
        id: `category-${folder}`, kind: 'category', folder, tone,
        items: items.filter((item) => sourceFolder(item) === folder),
      }));
    }
    // Search always spans every category, even when opened inside one category.
    const visible = items.filter((item) => normalizedQuery
      ? `${sourceDisplayName(item)} ${item.canonical_url} ${folderLabel(sourceFolder(item), language)}`.toLowerCase().includes(normalizedQuery)
      : sourceFolder(item) === selectedFolder);
    return visible.map((item) => ({ id: item.subscription_id, item, kind: 'source', groupEnd: false }));
  }, [folders, items, query, selectedFolder, language]);
  const visibleSourceCount = rows.reduce((count, row) => count + (row.kind === 'category' ? row.items.length : 1), 0);

  function openAddSheet() {
    setAddSheetKey((current) => current + 1);
    setShowAddSheet(true);
  }

  async function saveFolder() {
    if (!editingSource) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    setFolderError(null);
    setUpdatingFolder(true);
    try {
      const folderName = newEditFolder.trim() || (editFolder === UNCATEGORIZED_FOLDER ? null : editFolder);
      const updated = await updateSourceSubscriptionFolder(session, editingSource.subscription_id, folderName, context.runtime);
      if (!isRuntimeContextCurrent(context)) return;
      readerClient.setQueryData<SourceListItem[]>(
        readerQueryKeys.sources(session.user.id, context.serverId),
        (current) => current?.map((item) => item.subscription_id === updated.subscription_id ? updated : item),
      );
      await invalidateAfterSourceMutation(readerClient, session.user.id, context.serverId, context);
      if (!isRuntimeContextCurrent(context)) return;
      setEditingSource(null);
      setNewEditFolder('');
    } catch (error) {
      if (!isRuntimeContextCurrent(context)) return;
      setFolderError(error instanceof Error ? error : true);
    } finally {
      if (isRuntimeContextCurrent(context)) setUpdatingFolder(false);
    }
  }

  function confirmRemove(item: SourceListItem) {
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    setRemoveTriggerId(item.subscription_id);
    setRemoveConfirmation({ context, item });
  }

  const openSourceMenu = useCallback((item: SourceListItem) => {
    setRemoveTriggerId(item.subscription_id);
    setMenuSource(item);
  }, []);

  async function removeConfirmed(pending: PendingRemove | null): Promise<void> {
    if (!pending || !isRuntimeContextCurrent(pending.context)) return;
    const { context, item } = pending;
    setDeletingSubscriptionId(item.subscription_id);
    try {
      await removeSourceSubscription(session, item.subscription_id, context.runtime);
      if (!isRuntimeContextCurrent(context)) return;
      await invalidateAfterSourceMutation(readerClient, session.user.id, context.serverId, context);
      if (!isRuntimeContextCurrent(context)) return;
      setRemoveConfirmation(null);
    } catch (error) {
      if (isRuntimeContextCurrent(context)) throw error;
    } finally {
      if (isRuntimeContextCurrent(context)) setDeletingSubscriptionId(null);
    }
  }

  const { onScroll, revealChrome } = useCollapsingChrome(active, chromeProgress, selectedFolder, {
    height: SOURCES_HEADER_HEIGHT + SOURCES_CONTROLS_HEIGHT, locked: searchFocused,
  });
  const headerStyle = useChromeStyle(chromeProgress, SOURCES_HEADER_HEIGHT + SOURCES_CONTROLS_HEIGHT);

  return (
    <View style={styles.motionPage}>
      <Reanimated.View pointerEvents="box-none" style={[styles.sourcesHeaderLayer, headerStyle]}>
        <View style={styles.motionPageHeader}>
          <PageHeaderContent compact title={t('sources')} session={session} onProfile={onProfile} />
        </View>
        <View style={styles.sourcesControlsLayer}>
          <View style={styles.sourceControls}>
            <SearchField active={active} onFocusChange={setSearchFocused} label={t('searchSources')} placeholder={t('searchSources')} value={query} onChangeText={(value) => { revealChrome(); onChangeQuery(value); }} />
            <View style={styles.categoryHeading}>
              {selectedFolder !== null && !query.trim() ? <Pressable accessibilityRole="button" accessibilityLabel={t('backToFolders')} onPress={() => { revealChrome(); onSelectFolder(null); }} style={styles.categoryBack}><Feather name="chevron-left" size={16} color="#555555" /><Text style={styles.categoryBackText}>{t('allFolders')}</Text></Pressable> : <Text style={styles.categoryHeadingText}>{t(query.trim() ? 'searchResults' : 'myFolders')}</Text>}
              <Text style={styles.categoryHeadingCount}>{selectedFolder !== null && !query.trim() ? `${folderLabel(selectedFolder)} · ${visibleSourceCount}` : t('sourceCount', { count: visibleSourceCount })}</Text>
            </View>
          </View>
        </View>
      </Reanimated.View>
      <SourceListPage
        key={query.trim() ? 'search' : selectedFolder ?? 'categories'}
        avatarAccessToken={session.access_token}
        deletingSubscriptionId={deletingSubscriptionId}
        empty={!items.length}
        errorMessage={sourcesQuery.message}
        loading={loading}
        memoryKey={`sources:${query.trim()}\u0000${selectedFolder ?? ''}`}
        noMatches={Boolean(query.trim()) && !rows.length}
        onMore={openSourceMenu}
        onOpen={onOpenSource}
        onOpenFolder={(folder) => { revealChrome(); onSelectFolder(folder); }}
        onRetry={retrySources}
        onScroll={active ? onScroll : undefined}
        positionRegistry={positionRegistry}
        removeTriggerId={removeTriggerId}
        removeTriggerRef={removeTriggerRef}
        rows={rows}
      />
      <Pressable accessibilityLabel={t('addSource')} accessibilityRole="button" onPress={openAddSheet} style={styles.floatingAdd}>
        <Feather color="#FFFFFF" name="plus" size={25} />
      </Pressable>

      <AddSourceSheet
        folders={folders}
        initialFolder={selectedFolder ?? UNCATEGORIZED_FOLDER}
        key={addSheetKey}
        onClose={() => setShowAddSheet(false)}
        session={session}
        visible={showAddSheet}
      />

      <BottomSheetModal onClose={() => setMenuSource(null)} visible={menuSource !== null}>
            <View style={styles.modalHandle} />
            <Text style={styles.modalTitle}>{menuSource ? sourceDisplayName(menuSource) : t('source')}</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                if (!menuSource) return;
                setEditFolder(sourceFolder(menuSource));
                setNewEditFolder('');
                setFolderError(null);
                setEditingSource(menuSource);
                setMenuSource(null);
              }}
              style={styles.actionRow}
            >
              <MaterialCommunityIcons color="#1A1C1B" name="folder-edit-outline" size={21} />
              <Text style={styles.actionRowText}>{t('editFolder')}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={() => { if (menuSource) confirmRemove(menuSource); setMenuSource(null); }}
              style={styles.actionRow}
            >
              <MaterialCommunityIcons color="#BA1A1A" name="trash-can-outline" size={21} />
              <Text style={styles.actionRowDanger}>{t('unsubscribe')}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => setMenuSource(null)} style={styles.secondaryButton}>
              <Text style={styles.secondaryButtonText}>{t('cancel')}</Text>
            </Pressable>
      </BottomSheetModal>

      <BottomSheetModal onClose={() => setRemoveConfirmation(null)} visible={removeConfirmation !== null}>
        {removeConfirmation ? (
          <ActionConfirm
            confirmLabel={t('unsubscribe')}
            isCurrent={() => isRuntimeContextCurrent(removeConfirmation.context)}
            message={t('unsubscribeMessage', { name: sourceDisplayName(removeConfirmation.item) })}
            onCancel={() => setRemoveConfirmation(null)}
            onConfirm={() => removeConfirmed(removeConfirmation)}
            title={t('unsubscribeTitle')}
          />
        ) : null}
      </BottomSheetModal>

      <BottomSheetModal onClose={() => setEditingSource(null)} visible={editingSource !== null}>
            <View style={styles.modalHandle} />
            <Text style={styles.modalTitle}>{t('editFolder')}</Text>
            <Text style={styles.modalHint}>{editingSource ? sourceDisplayName(editingSource) : ''}</Text>
            <SourceFolderChoices
              folders={folders}
              selected={editFolder}
              newFolder={newEditFolder}
              onSelect={(folder) => { setEditFolder(folder); setNewEditFolder(''); }}
            />
            <TextInput
              accessibilityLabel={t('createOrEditFolder')}
              autoCapitalize="sentences"
              onChangeText={setNewEditFolder}
              placeholder={t('newFolderPlaceholder')}
              placeholderTextColor="#888888"
              style={styles.input}
              value={newEditFolder}
            />
            {folderError ? <Text accessibilityLiveRegion="assertive" accessibilityRole="alert" style={styles.formError}>{folderError === true ? t('folderUpdateFailed') : folderError.message}</Text> : null}
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: updatingFolder, busy: updatingFolder }} disabled={updatingFolder} onPress={() => void saveFolder()} style={styles.primaryButton}>
              <Text style={styles.primaryButtonText}>{t(updatingFolder ? 'savingFolder' : 'saveFolder')}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => setEditingSource(null)} style={styles.secondaryButton}>
              <Text style={styles.secondaryButtonText}>{t('cancel')}</Text>
            </Pressable>
      </BottomSheetModal>
    </View>
  );
}

const SourceListPage = memo(function SourceListPage({
  avatarAccessToken,
  deletingSubscriptionId,
  empty,
  errorMessage,
  loading,
  memoryKey,
  noMatches,
  onMore,
  onOpen,
  onRetry,
  onOpenFolder,
  onScroll,
  positionRegistry,
  removeTriggerId,
  removeTriggerRef,
  rows,
}: {
  avatarAccessToken: string;
  deletingSubscriptionId: string | null;
  empty: boolean;
  errorMessage: string;
  loading: boolean;
  memoryKey: string;
  noMatches: boolean;
  onMore: (item: SourceListItem) => void;
  onOpen: (item: SourceListItem) => void;
  onRetry: () => void | Promise<unknown>;
  onOpenFolder: (folder: string) => void;
  onScroll?: ScrollHandlerProcessed;
  positionRegistry: ListPositionRegistry;
  removeTriggerId: string | null;
  removeTriggerRef: RefObject<View | null>;
  rows: SourceRow[];
}) {
  const { t } = useTranslation('feed');
  const position = useListPositionMemory({
    active: Boolean(onScroll),
    itemId: sourceRowKey,
    items: rows,
    memoryKey,
    onScroll,
    registry: positionRegistry,
  });
  const renderSourceRow = useCallback(({ item: row }: ListRenderItemInfo<SourceRow>) => row.kind === 'category' ? (
    <SourceCategoryCard row={row} accessToken={avatarAccessToken} onOpen={onOpenFolder} />
  ) : (
    <SourceCard
      avatarAccessToken={avatarAccessToken}
      deleting={deletingSubscriptionId === row.item.subscription_id}
      groupEnd={row.groupEnd}
      item={row.item}
      moreRef={removeTriggerId === row.item.subscription_id ? removeTriggerRef : undefined}
      onMore={onMore}
      onOpen={onOpen}
    />
  ), [avatarAccessToken, deletingSubscriptionId, onMore, onOpen, onOpenFolder, removeTriggerId, removeTriggerRef]);

  return (
    <Reanimated.FlatList<SourceRow>
      contentContainerStyle={[styles.motionSourcesList, rows.length === 0 && listFeedbackStyles.content]}
      data={rows}
      initialNumToRender={8}
      keyExtractor={sourceRowKey}
      maxToRenderPerBatch={8}
      onMomentumScrollEnd={position.onMomentumScrollEnd}
      onScroll={position.onScroll}
      onScrollBeginDrag={position.onScrollBeginDrag}
      onScrollEndDrag={position.onScrollEndDrag}
      onScrollToIndexFailed={position.onScrollToIndexFailed}
      onViewableItemsChanged={position.onViewableItemsChanged}
      ref={position.listRef}
      renderItem={renderSourceRow}
      scrollEventThrottle={16}
      showsVerticalScrollIndicator={false}
      windowSize={5}
      ListHeaderComponent={(
        <>
          {rows.length > 0 && loading ? <LoadingBlock /> : null}
          {rows.length > 0 && !loading && errorMessage ? <ErrorState message={errorMessage} onRetry={onRetry} /> : null}
        </>
      )}
      ListEmptyComponent={(
        <View style={listFeedbackStyles.state}>
          {loading ? <LoadingBlock /> : errorMessage ? (
            <ErrorState message={errorMessage} onRetry={onRetry} />
          ) : empty ? (
            <EmptyState icon="rss" message={t('sourcesEmpty')} />
          ) : noMatches ? (
            <EmptyState icon="magnify" message={t('sourcesNoMatches')} />
          ) : null}
        </View>
      )}
    />
  );
});

const CATEGORY_TONES = ['#F7F5ED', '#F5F5F1', '#F5F3EE', '#F6F5F0'];
const AVATAR_POSITIONS: ViewStyle[] = [
  { left: 26, top: 15, zIndex: 3, transform: [{ rotate: '-7deg' }] },
  { left: 62, top: 0, zIndex: 2, transform: [{ rotate: '9deg' }] },
  { left: 0, top: 0, zIndex: 1, transform: [{ rotate: '-11deg' }] },
  { left: 69, bottom: -2, zIndex: 4 },
];
function SourceCategoryCard({ row, accessToken, onOpen }: {
  row: Extract<SourceRow, { kind: 'category' }>;
  accessToken: string;
  onOpen: (folder: string) => void;
}) {
  const { t } = useTranslation('feed');
  const previewNames = row.items.slice(0, 2).map(sourceDisplayName).join(t('sourceListSeparator'));
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={t('folderSourceCount', { name: folderLabel(row.folder), count: row.items.length })} onPress={() => onOpen(row.folder)} style={({ pressed }) => [styles.categoryCard, { backgroundColor: CATEGORY_TONES[row.tone % CATEGORY_TONES.length] }, pressed && styles.sourceOpenPressed]}>
      <View style={styles.categoryCopy}>
        <Text style={styles.categoryCount}>{t('sourceCount', { count: row.items.length })}</Text>
        <Text numberOfLines={1} style={styles.categoryName}>{folderLabel(row.folder)}</Text>
        <Text numberOfLines={2} style={styles.categoryPreview}>{row.items.length > 2 ? t('sourceListMore', { names: previewNames }) : previewNames}</Text>
      </View>
      <View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.avatarCloud}>
        {row.items.slice(0, 4).map((item, index) => <View key={item.subscription_id} style={[styles.cloudAvatar, AVATAR_POSITIONS[index]]}><SourceAvatar accessToken={accessToken} source={item} size={index === 0 ? 48 : index === 3 ? 28 : 38} /></View>)}
        {row.items.length > 4 ? <Text style={styles.cloudExtra}>+{row.items.length - 4}</Text> : null}
      </View>
    </Pressable>
  );
}

function sourceRowKey(row: SourceRow) {
  return row.id;
}

const SourceCard = memo(function SourceCard({
  avatarAccessToken,
  deleting,
  groupEnd = false,
  item,
  moreRef,
  onMore,
  onOpen,
}: {
  avatarAccessToken: string;
  deleting: boolean;
  groupEnd?: boolean;
  item: SourceListItem;
  moreRef?: RefObject<View | null>;
  onMore: (item: SourceListItem) => void;
  onOpen: (item: SourceListItem) => void;
}) {
  const { t } = useTranslation('feed');
  const displayName = sourceDisplayName(item);
  const secondaryLabel = sourceSecondaryLabel(item);
  return (
    <View style={[styles.sourceCard, groupEnd && styles.sourceCardGroupEnd]}>
      <Pressable
        accessibilityLabel={t('openChannel', { name: displayName })}
        accessibilityRole="button"
        disabled={deleting}
        onPress={() => onOpen(item)}
        style={({ pressed }) => [styles.sourceOpen, pressed && styles.sourceOpenPressed]}
      >
        <SourceAvatar accessToken={avatarAccessToken} source={item} />
        <View style={styles.sourceInfo}>
          <Text numberOfLines={1} style={styles.sourceTitle}>{displayName}</Text>
          {secondaryLabel ? <Text numberOfLines={1} style={styles.sourceUrl}>{secondaryLabel}</Text> : null}
          <Text style={styles.sourceStatus}>{sourceHealthLabel(item)}</Text>
        </View>
        <MaterialCommunityIcons color="#777777" name="chevron-right" size={21} />
      </Pressable>
      <Pressable ref={moreRef} accessibilityLabel={t('sourceMoreActions', { name: displayName })} accessibilityRole="button" disabled={deleting} hitSlop={10} onPress={() => onMore(item)} style={styles.removeButton}>
        {deleting ? <ActivityIndicator color="#666666" size="small" /> : <MaterialCommunityIcons color="#444748" name="dots-vertical" size={22} />}
      </Pressable>
    </View>
  );
});

const styles = StyleSheet.create({
  sourceControls: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING, paddingTop: 13 },
  categoryHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', height: 19, marginTop: 23, marginBottom: 13 },
  categoryHeadingText: { color: '#242924', fontSize: 12, lineHeight: 19, fontWeight: '600' },
  categoryHeadingCount: { color: '#99998E', fontSize: 10, lineHeight: 16 },
  categoryBackText: { color: '#555555', fontSize: 12 },
  categoryBack: { flexDirection: 'row', alignItems: 'center', gap: 3, minHeight: 32 },
  categoryCard: { minHeight: 104, borderRadius: 20, paddingVertical: 14, paddingHorizontal: 18, marginBottom: 10, flexDirection: 'row', alignItems: 'center', gap: 10 },
  categoryCopy: { flex: 1, minWidth: 0, paddingRight: 4 },
  categoryCount: { color: '#9A978B', fontSize: 10, lineHeight: 16 },
  categoryName: { color: '#242924', fontSize: 20, lineHeight: 28, letterSpacing: -0.3, fontWeight: '700', marginTop: 2, marginBottom: 4 },
  categoryPreview: { color: '#99968D', fontSize: 10, lineHeight: 16, maxWidth: 175 },
  avatarCloud: { width: 110, height: 72 },
  cloudAvatar: { position: 'absolute', borderWidth: 3, borderColor: '#FCFBF8', borderRadius: 999, overflow: 'hidden' },
  cloudExtra: { position: 'absolute', bottom: 0, left: 2, color: '#878B80', fontSize: 10 },
  motionPage: { backgroundColor: '#FCFBF8', flex: 1, overflow: 'hidden' },
  motionPageHeader: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  sourcesHeaderLayer: { backgroundColor: '#FCFBF8', height: SOURCES_HEADER_HEIGHT + SOURCES_CONTROLS_HEIGHT, left: 0, position: 'absolute', right: 0, top: 0, zIndex: 4 },
  sourcesControlsLayer: { backgroundColor: '#FCFBF8', height: SOURCES_CONTROLS_HEIGHT },
  motionSourcesList: { gap: 0, paddingBottom: SCREEN_LIST_BOTTOM_PADDING + 54, paddingHorizontal: 16, paddingTop: SOURCES_HEADER_HEIGHT + SOURCES_CONTROLS_HEIGHT },
  sourceCard: { alignItems: 'center', borderBottomColor: '#ECEDE7', borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', gap: 6, minHeight: 84, paddingVertical: 12 },
  sourceCardGroupEnd: { marginBottom: 26 },
  sourceOpen: { alignItems: 'center', flex: 1, flexDirection: 'row', gap: 12, minHeight: 50 },
  sourceOpenPressed: { opacity: 0.68 },
  sourceInfo: { flex: 1 },
  sourceTitle: { color: '#111111', fontSize: 16, fontWeight: '600' },
  sourceUrl: { color: '#666666', fontSize: 12, marginTop: 2 },
  sourceStatus: { color: '#666666', fontSize: 12, marginTop: 4 },
  removeButton: { alignItems: 'center', justifyContent: 'center', minHeight: 38, minWidth: 38 },
  floatingAdd: { alignItems: 'center', backgroundColor: '#242424', borderRadius: 27, bottom: READER_TAB_BAR_HEIGHT + 22, elevation: 3, height: 54, justifyContent: 'center', position: 'absolute', right: 20, shadowColor: '#000000', shadowOffset: { height: 3, width: 0 }, shadowOpacity: 0.12, shadowRadius: 6, width: 54 },
  modalHandle: { alignSelf: 'center', backgroundColor: '#C9C9C6', borderRadius: 3, height: 5, marginBottom: 18, width: 40 },
  modalTitle: { color: '#111111', fontSize: 24, fontWeight: '700' },
  modalHint: { color: '#666666', fontSize: 14, lineHeight: 20, marginBottom: 16, marginTop: 5 },
  input: { backgroundColor: '#FFFFFF', borderColor: '#E5E5E3', borderRadius: 10, borderWidth: 1, color: '#111111', fontSize: 15, marginBottom: 12, padding: 13 },
  formError: { color: '#BA1A1A', fontSize: 13, lineHeight: 19, marginBottom: 12 },
  primaryButton: { alignItems: 'center', backgroundColor: '#111111', borderRadius: 10, justifyContent: 'center', minHeight: 50, paddingHorizontal: 16 },
  primaryButtonText: { color: '#FFFFFF', fontSize: 16, fontWeight: '700' },
  secondaryButton: { alignItems: 'center', justifyContent: 'center', minHeight: 48, marginTop: 4 },
  secondaryButtonText: { color: '#444444', fontSize: 15, fontWeight: '600' },
  actionRow: { alignItems: 'center', borderBottomColor: '#E5E5E3', borderBottomWidth: 1, flexDirection: 'row', gap: 14, minHeight: 58 },
  actionRowText: { color: '#1A1C1B', fontSize: 16, fontWeight: '600' },
  actionRowDanger: { color: '#BA1A1A', fontSize: 16, fontWeight: '600' },
});
