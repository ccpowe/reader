/**
 * One board entry: rank, title (translated when available), description, the board's own
 * numbers as one meta line, optional image, bookmark. Same frame for every board; each board
 * fills the meta line with what it has (HN points / comments, GitHub language / stars, …).
 */
import { IconBookmark, IconBookmarkFilled } from '@tabler/icons-react-native';
import { Image, Pressable, View } from 'react-native';

import { IconButton, Text } from '@/ui';

export type BoardEntryItem = {
  rank: number;
  title: string;
  description?: string | null;
  /** e.g. ["github.com", "412 分", "128 评论"]; joined with " · " */
  meta: string[];
  tags?: string[];
  imageUrl?: string | null;
  isSaved: boolean;
};

export function BoardEntry({ entry, onOpen, onToggleSave }: { entry: BoardEntryItem; onOpen?: () => void; onToggleSave?: () => void }) {
  const meta = [...(entry.tags ?? []).map((tag) => `#${tag}`), ...entry.meta].join(' · ');
  return (
    <Pressable accessibilityRole="button" className="flex-row gap-3 px-4 py-3 active:bg-pressed" onPress={onOpen}>
      <Text className="w-5 pt-0.5" level="caption" style={{ fontVariant: ['tabular-nums'] }} tone="tertiary">
        {entry.rank}
      </Text>
      <View className="min-w-0 flex-1 gap-1">
        <Text level="body-strong" numberOfLines={3}>
          {entry.title}
        </Text>
        {entry.description ? (
          <Text level="desc" numberOfLines={2} tone="secondary">
            {entry.description}
          </Text>
        ) : null}
        {meta ? (
          <Text level="caption" numberOfLines={1} tone="tertiary">
            {meta}
          </Text>
        ) : null}
      </View>
      {entry.imageUrl ? (
        <Image className="rounded-md bg-sunken" source={{ uri: entry.imageUrl }} style={{ width: 56, height: 56 }} />
      ) : null}
      <IconButton
        icon={entry.isSaved ? IconBookmarkFilled : IconBookmark}
        label={entry.isSaved ? '取消收藏' : '收藏'}
        on={entry.isSaved}
        onPress={onToggleSave}
        size="sm"
      />
    </Pressable>
  );
}
