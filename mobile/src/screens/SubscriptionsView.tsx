/**
 * 订阅 (PRODUCT §3.3), old app layout (device screenshots) at P25 values: search, "我的分类" with
 * the total, a card per folder; opening a folder lists its subscriptions with health lines (P23
 * R4-B). The floating + adds a subscription. Searching lists matching subscriptions directly.
 * Unsubscribing acts at once with Undo (P27). Presentational: data and callbacks come from the route.
 */
import { IconChevronLeft, IconFolder, IconLayoutGrid, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react-native';
import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { ChromePadded, ChromeScrollView, CollapsingScreen, useTabBarInset } from '@/components/CollapsingChrome';
import { FolderCard } from '@/components/FolderCard';
import { SubscriptionRow } from '@/components/SubscriptionRow';
import { folderLabel } from '@/lib/feed';
import {
  folderInput,
  searchFolders,
  SUBSCRIPTION_TYPES,
  type Folder,
  type SubscriptionItem,
  type SubscriptionType,
} from '@/lib/subscriptions';
import {
  Button,
  EmptyState,
  Fab,
  Icon,
  Input,
  Message,
  PageHeader,
  Row,
  SearchField,
  SectionHeading,
  Spinner,
  Switch,
  Tabs,
  Text,
} from '@/ui';
import { t } from '@/lib/i18n';

function FolderBar({ name, count, onBack }: { name: string; count: number; onBack?: () => void }) {
  return (
    <View className="h-14 flex-row items-center px-6">
      <Pressable accessibilityRole="button" className="h-11 flex-row items-center gap-1 active:opacity-60" onPress={onBack}>
        <Icon icon={IconChevronLeft} />
        <Text level="ui-strong">{t().subs.allFolders}</Text>
      </Pressable>
      <View className="flex-1" />
      <Text level="caption" numberOfLines={1} tone="tertiary">
        {name} · {count}
      </Text>
    </View>
  );
}

export type SubscriptionsState = { kind: 'loading' } | { kind: 'failed'; message: string } | { kind: 'ready'; folders: Folder[] };

export function SubscriptionsView({
  user,
  onPressUser,
  banner,
  state,
  openFolder,
  expandedId,
  query = '',
  refreshing = false,
  onSearch,
  onAdd,
  onRetry,
  onRefresh,
  onOpenFolder,
  onCloseFolder,
  onOpen,
  onToggleDetail,
  onMenu,
}: {
  user?: { name: string; avatarUrl?: string | null };
  /** the header's avatar: opens 我的 */
  onPressUser?: () => void;
  banner?: string | null;
  state: SubscriptionsState;
  /** the folder being shown (its key); none = the folder cards */
  openFolder?: string | null;
  expandedId?: string | null;
  query?: string;
  refreshing?: boolean;
  onSearch?: (query: string) => void;
  onAdd?: () => void;
  onRetry?: () => void;
  onRefresh?: () => void;
  onOpenFolder?: (key: string) => void;
  onCloseFolder?: () => void;
  onOpen?: (id: string) => void;
  onToggleDetail?: (id: string) => void;
  onMenu?: (id: string) => void;
}) {
  const spinner = useCSSVariable('--color-icon-neutral') as string | undefined;
  const tabBar = useTabBarInset();
  const folders = state.kind === 'ready' ? state.folders : [];
  const total = folders.reduce((sum, folder) => sum + folder.items.length, 0);
  const matches = searchFolders(folders, query);
  const current =
    openFolder !== null && openFolder !== undefined ? folders.find((folder) => folder.key === openFolder) : undefined;
  const rows = (items: SubscriptionItem[]) =>
    items.map((item) => (
      <SubscriptionRow
        expanded={expandedId === item.id}
        item={item}
        key={item.id}
        onMenu={() => onMenu?.(item.id)}
        onPress={() => onOpen?.(item.id)}
        onToggleDetail={() => onToggleDetail?.(item.id)}
      />
    ));

  let body;
  if (state.kind === 'loading') {
    body = (
      <ChromePadded>
        <View className="items-center py-12">
          <Spinner />
        </View>
      </ChromePadded>
    );
  } else if (state.kind === 'failed') {
    body = (
      <ChromePadded>
        <EmptyState action={<Button onPress={onRetry} title={t().common.retry} />} description={state.message} title={t().subs.loadFailed} />
      </ChromePadded>
    );
  } else if (total === 0) {
    body = (
      <ChromePadded>
        <EmptyState
          action={<Button large onPress={onAdd} title={t().subs.add} variant="primary" />}
          description={t().subs.empty}
          title={t().subs.emptyTitle}
        />
      </ChromePadded>
    );
  } else {
    // The header and tab bar slide away while the list scrolls (old app, PRODUCT §2).
    body = (
      <ChromeScrollView
        extraBottom={72}
        keyboardShouldPersistTaps="handled"
        onRefresh={onRefresh}
        refreshColor={spinner}
        refreshing={refreshing}
      >
        <View className="pb-2 pt-2">
          <SearchField autoCapitalize="none" autoCorrect={false} onChangeText={onSearch} placeholder={t().subs.search} value={query} />
        </View>
        {query.trim() ? (
          <>
            <SectionHeading aside={t().subs.matches(matches.length)} title={t().common.searchResults} />
            {matches.length === 0 ? <EmptyState title={t().subs.noMatch} /> : rows(matches)}
          </>
        ) : current ? (
          <>
            <FolderBar count={current.items.length} name={current.name} onBack={onCloseFolder} />
            {rows(current.items)}
          </>
        ) : (
          <>
            <SectionHeading aside={t().subs.count(total)} title={t().subs.myFolders} />
            <View className="gap-3">
              {folders.map((folder) => (
                <FolderCard items={folder.items} key={folder.key} name={folder.name} onPress={() => onOpenFolder?.(folder.key)} />
              ))}
            </View>
          </>
        )}
      </ChromeScrollView>
    );
  }

  return (
    <CollapsingScreen
      header={
        <>
          {banner ? (
            <Message bar kind="warning">
              {banner}
            </Message>
          ) : null}
          <PageHeader title={t().subs.title} onPressUser={onPressUser} user={user} />
        </>
      }
      tab="subscriptions"
    >
      {body}
      {state.kind === 'ready' && total > 0 ? <Fab bottom={tabBar + 24} icon={IconPlus} label={t().subs.add} onPress={onAdd} /> : null}
    </CollapsingScreen>
  );
}

const NEW_FOLDER = '\u0000new';

/** Folder choice: 未分类, the existing folders, or a new one typed in. */
function FolderChoice({
  folders,
  choice,
  typed,
  onChoice,
  onTyped,
}: {
  folders: string[];
  choice: string | null;
  typed: string;
  onChoice: (key: string | null) => void;
  onTyped: (text: string) => void;
}) {
  return (
    <>
      <View className="-mx-4">
        <SectionHeading inset="sheet" title={t().subs.folder} />
        <Tabs
          inset="sheet"
          onSelect={onChoice}
          options={[
            { key: null, label: folderLabel('') },
            ...folders.map((name) => ({ key: name, label: name })),
            { key: NEW_FOLDER, label: t().subs.newFolder },
          ]}
          selected={choice}
        />
      </View>
      {choice === NEW_FOLDER ? (
        <Input autoFocus label={t().subs.newFolderName} maxLength={120} onChangeText={onTyped} placeholder={t().subs.newFolderPlaceholder} value={typed} />
      ) : null}
    </>
  );
}

function chosenFolder(choice: string | null, typed: string): string | null {
  return choice === NEW_FOLDER ? folderInput(typed) : choice;
}

/** Content of the "添加订阅" sheet: type, address, folder; one primary action. The input stays on failure. */
export function AddSubscriptionForm({
  folders,
  initial,
  error,
  submitting = false,
  onEdit,
  onSubmit,
}: {
  /** existing folder names (未分类 not included) */
  folders: string[];
  initial?: { type?: SubscriptionType; value?: string; folder?: string | null };
  error?: string | null;
  submitting?: boolean;
  /** the input changed: an old error no longer applies */
  onEdit?: () => void;
  onSubmit?: (request: { type: SubscriptionType; value: string; group: string | null }) => void;
}) {
  const [type, setType] = useState<SubscriptionType>(initial?.type ?? 'rss');
  const [value, setValue] = useState(initial?.value ?? '');
  const [choice, setChoice] = useState<string | null>(initial?.folder ?? null);
  const [typed, setTyped] = useState('');
  const current = SUBSCRIPTION_TYPES.find((item) => item.key === type)!;
  const ready = value.trim().length > 0 && (choice !== NEW_FOLDER || folderInput(typed) !== null);
  const edit =
    <T,>(set: (next: T) => void) =>
    (next: T) => {
      set(next);
      onEdit?.();
    };
  return (
    <View className="gap-3">
      <View className="-mx-4">
        <Tabs
          inset="sheet"
          onSelect={edit(setType)}
          options={SUBSCRIPTION_TYPES.map(({ key, label }) => ({ key, label }))}
          selected={type}
        />
      </View>
      <Input
        autoCapitalize="none"
        autoCorrect={false}
        editable={!submitting}
        error={error}
        hint={type === 'website' ? t().subs.websiteHint : undefined}
        keyboardType="url"
        label={t().subs.address}
        onChangeText={edit(setValue)}
        onSubmitEditing={() => ready && onSubmit?.({ type, value: value.trim(), group: chosenFolder(choice, typed) })}
        placeholder={current.placeholder}
        returnKeyType="done"
        value={value}
      />
      <FolderChoice choice={choice} folders={folders} onChoice={setChoice} onTyped={setTyped} typed={typed} />
      {submitting ? <Message>{t().subs.adding}</Message> : null}
      <Button
        disabled={!ready || submitting}
        large
        loading={submitting}
        onPress={() => onSubmit?.({ type, value: value.trim(), group: chosenFolder(choice, typed) })}
        title={t().subs.addButton}
        variant="primary"
      />
    </View>
  );
}

/** Content of a subscription's ⋮ sheet: rename, move, show in 全部, unsubscribe (at once, Undo after). */
export function SubscriptionMenu({
  name,
  folder,
  folders,
  includeInHome,
  onRename,
  onMove,
  onToggleHome,
  onUnsubscribe,
}: {
  name: string;
  /** the folder it is in ('' = 未分类) */
  folder: string;
  folders: string[];
  includeInHome: boolean;
  onRename?: (name: string | null) => void;
  onMove?: (group: string | null) => void;
  onToggleHome?: (include: boolean) => void;
  onUnsubscribe?: () => void;
}) {
  const [mode, setMode] = useState<'menu' | 'rename' | 'move'>('menu');
  const [typedName, setTypedName] = useState(name);
  const [choice, setChoice] = useState<string | null>(folder || null);
  const [typedFolder, setTypedFolder] = useState('');

  if (mode === 'rename') {
    const next = typedName.trim().slice(0, 300);
    return (
      <View className="gap-3">
        <Input
          autoFocus
          label={t().subs.name}
          maxLength={300}
          onChangeText={setTypedName}
          onSubmitEditing={() => next && onRename?.(next)}
          value={typedName}
        />
        <Button disabled={!next} large onPress={() => onRename?.(next)} title={t().common.save} variant="primary" />
        <Button onPress={() => onRename?.(null)} title={t().subs.restoreName} variant="ghost" />
      </View>
    );
  }
  if (mode === 'move') {
    const target = chosenFolder(choice, typedFolder);
    const valid = choice !== NEW_FOLDER || target !== null;
    return (
      <View className="gap-3">
        <FolderChoice choice={choice} folders={folders} onChoice={setChoice} onTyped={setTypedFolder} typed={typedFolder} />
        <Button
          disabled={!valid || (target ?? '') === folder}
          large
          onPress={() => onMove?.(target)}
          title={t().subs.moveButton}
          variant="primary"
        />
      </View>
    );
  }
  return (
    <View className="-mx-4">
      <Row inset="sheet" detail={name} label={t().subs.rename} leading={<Icon icon={IconPencil} />} onPress={() => setMode('rename')} />
      <Row
        inset="sheet"
        detail={folderLabel(folder)}
        label={t().subs.move}
        leading={<Icon icon={IconFolder} />}
        onPress={() => setMode('move')}
      />
      <Row
        detail={t().subs.showInAllHint}
        inset="sheet"
        label={t().subs.showInAll}
        leading={<Icon icon={IconLayoutGrid} />}
        onPress={() => onToggleHome?.(!includeInHome)}
        trailing={<Switch label={t().subs.showInAll} onChange={onToggleHome} value={includeInHome} />}
      />
      <Row inset="sheet" label={t().subs.unsubscribe} leading={<Icon icon={IconTrash} tone="error" />} onPress={onUnsubscribe} />
    </View>
  );
}

/** One subscription's own page (opened from 订阅): back, its name, then its feed. Back returns to 订阅. */
export { PushedPage as SubscriptionFeedView } from './PushedPage';
