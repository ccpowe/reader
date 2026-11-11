/**
 * A feed entry (layout P23 R1-A): source line on top, title (3 lines) with a thumbnail on the
 * right, a 2-line summary, bookmark top right (P19). X posts keep today's hierarchy (P16): author
 * line, text, quoted post as a callout (tinted, no border: no outlined cards), "共 N 条" for a self-reply thread (P17).
 */
import { IconBookmark, IconBookmarkFilled, IconRepeat } from '@tabler/icons-react-native';
import { Image, Pressable, View } from 'react-native';

import { Avatar, Badge, IconButton, Icon, Text } from '@/ui';

export type FeedCardItem = {
  id: string;
  source: { name: string; avatarUrl?: string | null };
  seenAgo: string;
  /** shown title: the translation when there is one (P15) */
  title: string;
  summary: string | null;
  imageUrl: string | null;
  isSaved: boolean;
  x?: {
    author: { name: string; handle: string; avatarUrl?: string | null };
    text: string;
    replyTo?: string;
    repostedBy?: string;
    quote?: { author: string; handle: string; text: string };
    threadCount?: number;
  };
};

const THUMB = 72;

function SaveToggle({ saved, onPress }: { saved: boolean; onPress?: () => void }) {
  return <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? '取消收藏' : '收藏'} on={saved} onPress={onPress} size="sm" />;
}

function XPost({ item, onToggleSave }: { item: FeedCardItem; onToggleSave?: () => void }) {
  const x = item.x!;
  return (
    <View className="gap-2">
      {x.repostedBy ? (
        <View className="flex-row items-center gap-1.5 pl-1">
          <Icon icon={IconRepeat} size="xs" />
          <Text level="caption" tone="tertiary">
            {x.repostedBy} 转发了
          </Text>
        </View>
      ) : null}
      <View className="flex-row items-center gap-2">
        <Avatar name={x.author.name} size={32} uri={x.author.avatarUrl} />
        <View className="min-w-0 flex-1">
          <Text level="ui-strong" numberOfLines={1}>
            {x.author.name}{' '}
            <Text level="ui" tone="tertiary">
              @{x.author.handle}
            </Text>
          </Text>
          <Text level="caption" tone="tertiary">
            {item.seenAgo}
            {x.replyTo ? ` · 回复 @${x.replyTo}` : ''}
          </Text>
        </View>
        <SaveToggle onPress={onToggleSave} saved={item.isSaved} />
      </View>
      <Text level="body" numberOfLines={6}>
        {x.text}
      </Text>
      {x.quote ? (
        <View className="gap-1 rounded-xl bg-tint p-3">
          <Text level="ui-strong" numberOfLines={1}>
            {x.quote.author}{' '}
            <Text level="ui" tone="tertiary">
              @{x.quote.handle}
            </Text>
          </Text>
          <Text numberOfLines={4}>{x.quote.text}</Text>
        </View>
      ) : null}
      {item.imageUrl ? (
        <Image className="h-44 w-full rounded-xl bg-sunken" source={{ uri: item.imageUrl }} />
      ) : null}
      {x.threadCount && x.threadCount > 1 ? <Badge>共 {x.threadCount} 条</Badge> : null}
    </View>
  );
}

function Article({ item, onToggleSave }: { item: FeedCardItem; onToggleSave?: () => void }) {
  return (
    <View className="gap-1.5">
      <View className="flex-row items-center gap-1.5">
        <Avatar name={item.source.name} uri={item.source.avatarUrl} />
        <Text className="shrink" level="caption" numberOfLines={1} tone="secondary">
          {item.source.name}
        </Text>
        <Text className="shrink-0 flex-1" level="caption" numberOfLines={1} tone="tertiary">
          · {item.seenAgo}
        </Text>
        <SaveToggle onPress={onToggleSave} saved={item.isSaved} />
      </View>
      <View className="flex-row gap-3">
        <Text className="flex-1" level="body-strong" numberOfLines={3}>
          {item.title}
        </Text>
        {item.imageUrl ? (
          <Image className="rounded-md bg-sunken" source={{ uri: item.imageUrl }} style={{ width: THUMB, height: THUMB }} />
        ) : null}
      </View>
      {item.summary ? (
        <Text level="desc" numberOfLines={2} tone="secondary">
          {item.summary}
        </Text>
      ) : null}
    </View>
  );
}

export function FeedCard({
  item,
  onOpen,
  onToggleSave,
}: {
  item: FeedCardItem;
  onOpen?: () => void;
  onToggleSave?: () => void;
}) {
  return (
    <Pressable accessibilityRole="button" className="px-4 py-3 active:bg-pressed" onPress={onOpen}>
      {item.x ? <XPost item={item} onToggleSave={onToggleSave} /> : <Article item={item} onToggleSave={onToggleSave} />}
    </Pressable>
  );
}
