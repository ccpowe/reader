/**
 * 订阅 (PRODUCT §3.3), old app layout (device screenshots) at P25 values: search, "我的分类" with
 * the total, a card per folder; opening a folder lists its subscriptions with health lines (P23
 * R4-B). The floating + adds a subscription. Searching lists matching subscriptions directly.
 * Unsubscribing acts at once with Undo (P27). Presentational: data and callbacks come from the route.
 */
import { IconArrowLeft, IconChevronLeft, IconFolder, IconLayoutGrid, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react-native';
import { useState, type ReactNode } from 'react';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Pressable, RefreshControl, ScrollView, View } from 'react-native';
import { useCSSVariable } from 'uniwind';

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
  IconButton,
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
  TopBar,
} from '@/ui';

function FolderBar({ name, count, onBack }: { name: string; count: number; onBack?: () => void }) {
  return (
    <View className="h-14 flex-row items-center px-6">
      <Pressable accessibilityRole="button" className="h-11 flex-row items-center gap-1 active:opacity-60" onPress={onBack}>
        <Icon icon={IconChevronLeft} />
        <Text level="ui-strong">全部分类</Text>
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
      <View className="items-center py-12">
        <Spinner />
      </View>
    );
  } else if (state.kind === 'failed') {
    body = <EmptyState action={<Button onPress={onRetry} title="重试" />} description={state.message} title="没能加载订阅" />;
  } else if (total === 0) {
    body = (
      <EmptyState
        action={<Button large onPress={onAdd} title="添加订阅" variant="primary" />}
        description="可以添加 RSS、网站（自动找 RSS）、YouTube 频道、X 账号或 Reddit 社区。"
        title="还没有订阅"
      />
    );
  } else {
    body = (
      <ScrollView
        contentContainerClassName="pb-24"
        keyboardShouldPersistTaps="handled"
        refreshControl={
          onRefresh ? (
            <RefreshControl
              colors={spinner ? [spinner] : undefined}
              onRefresh={onRefresh}
              refreshing={refreshing}
              tintColor={spinner}
            />
          ) : undefined
        }
      >
        <View className="pb-2 pt-2">
          <SearchField autoCapitalize="none" autoCorrect={false} onChangeText={onSearch} placeholder="搜索订阅源" value={query} />
        </View>
        {query.trim() ? (
          <>
            <SectionHeading aside={`${matches.length} 个`} title="搜索结果" />
            {matches.length === 0 ? <EmptyState title="没有匹配的订阅源" /> : rows(matches)}
          </>
        ) : current ? (
          <>
            <FolderBar count={current.items.length} name={current.name} onBack={onCloseFolder} />
            {rows(current.items)}
          </>
        ) : (
          <>
            <SectionHeading aside={`${total} 个订阅源`} title="我的分类" />
            <View className="gap-3">
              {folders.map((folder) => (
                <FolderCard items={folder.items} key={folder.key} name={folder.name} onPress={() => onOpenFolder?.(folder.key)} />
              ))}
            </View>
          </>
        )}
      </ScrollView>
    );
  }

  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <PageHeader title="订阅" user={user} />
      {body}
      {state.kind === 'ready' && total > 0 ? <Fab icon={IconPlus} label="添加订阅" onPress={onAdd} /> : null}
    </View>
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
        <SectionHeading inset="sheet" title="分类" />
        <Tabs
          inset="sheet"
          onSelect={onChoice}
          options={[
            { key: null, label: folderLabel('') },
            ...folders.map((name) => ({ key: name, label: name })),
            { key: NEW_FOLDER, label: '+ 新分类' },
          ]}
          selected={choice}
        />
      </View>
      {choice === NEW_FOLDER ? (
        <Input autoFocus label="新分类的名字" maxLength={120} onChangeText={onTyped} placeholder="比如：AI" value={typed} />
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
        hint={type === 'website' ? 'Reader 会在网站上找它的 RSS / Atom。' : undefined}
        keyboardType="url"
        label="地址"
        onChangeText={edit(setValue)}
        onSubmitEditing={() => ready && onSubmit?.({ type, value: value.trim(), group: chosenFolder(choice, typed) })}
        placeholder={current.placeholder}
        returnKeyType="done"
        value={value}
      />
      <FolderChoice choice={choice} folders={folders} onChoice={setChoice} onTyped={setTyped} typed={typed} />
      {submitting ? <Message>正在查找并添加……</Message> : null}
      <Button
        disabled={!ready || submitting}
        large
        loading={submitting}
        onPress={() => onSubmit?.({ type, value: value.trim(), group: chosenFolder(choice, typed) })}
        title="添加"
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
          label="名字"
          maxLength={300}
          onChangeText={setTypedName}
          onSubmitEditing={() => next && onRename?.(next)}
          value={typedName}
        />
        <Button disabled={!next} large onPress={() => onRename?.(next)} title="保存" variant="primary" />
        <Button onPress={() => onRename?.(null)} title="恢复来源自己的名字" variant="ghost" />
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
          title="移动"
          variant="primary"
        />
      </View>
    );
  }
  return (
    <View className="-mx-4">
      <Row inset="sheet" detail={name} label="改名" leading={<Icon icon={IconPencil} />} onPress={() => setMode('rename')} />
      <Row
        inset="sheet"
        detail={folderLabel(folder)}
        label="移到分类"
        leading={<Icon icon={IconFolder} />}
        onPress={() => setMode('move')}
      />
      <Row
        detail="关掉后只在它的分类和单独查看时出现"
        inset="sheet"
        label="在「全部」中显示"
        leading={<Icon icon={IconLayoutGrid} />}
        onPress={() => onToggleHome?.(!includeInHome)}
        trailing={<Switch label="在「全部」中显示" onChange={onToggleHome} value={includeInHome} />}
      />
      <Row inset="sheet" label="取消订阅" leading={<Icon icon={IconTrash} tone="error" />} onPress={onUnsubscribe} />
    </View>
  );
}

/** One subscription's own page (opened from 订阅): back, its name, then its feed. Back returns to 订阅. */
export function SubscriptionFeedView({
  title,
  banner,
  onBack,
  children,
}: {
  title: string;
  banner?: string | null;
  onBack?: () => void;
  children: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <TopBar leading={<IconButton icon={IconArrowLeft} label="返回" onPress={onBack} />} title={title} />
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <View className="flex-1" style={{ paddingBottom: insets.bottom }}>
        {children}
      </View>
    </View>
  );
}
