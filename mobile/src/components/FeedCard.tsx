/**
 * A feed entry, old app frame at P25 values (P23 R1-A as corrected by P26): source line on top
 * (mark + name + optional author), title 18/28 (3 lines) with a thumbnail on the right, a 2-line
 * summary, then a bottom row with the time and the bookmark (P19). 24 from the screen edge; a
 * hairline under each card. X posts keep their hierarchy (P16): author line, text, quoted post as
 * a callout (tinted, no border), image, "共 N 条" for a self-reply thread (P17).
 */
import {
  IconBookmark,
  IconBookmarkFilled,
  IconBrandGithub,
  IconBrandReddit,
  IconBrandX,
  IconBrandYoutube,
  IconRepeat,
  IconRss,
  IconWorld,
} from '@tabler/icons-react-native';
import { Image, Pressable, View } from 'react-native';

import { Avatar, Badge, Icon, IconButton, Text } from '@/ui';

export type SourceKind = 'rss' | 'web' | 'youtube' | 'x' | 'reddit' | 'github';

export type FeedCardItem = {
  id: string;
  source: { name: string; kind: SourceKind; avatarUrl?: string | null; byline?: string | null };
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

const kindIcon = { rss: IconRss, web: IconWorld, youtube: IconBrandYoutube, x: IconBrandX, reddit: IconBrandReddit, github: IconBrandGithub };

/** The small square in front of a source name: its icon image, else the type's icon. */
export function SourceMark({
  kind,
  avatarUrl,
  size = 22,
  raised = false,
}: {
  kind: SourceKind;
  avatarUrl?: string | null;
  size?: number;
  /** on a tinted panel (folder cards): a raised tile instead of a sunken one */
  raised?: boolean;
}) {
  const radius = size >= 36 ? 'rounded-xl' : 'rounded-md';
  const surface = raised ? 'bg-raised' : 'bg-sunken';
  if (avatarUrl) {
    return <Image className={`${radius} ${surface}`} source={{ uri: avatarUrl }} style={{ width: size, height: size }} />;
  }
  return (
    <View className={`${radius} ${surface} items-center justify-center`} style={{ width: size, height: size }}>
      <Icon icon={kindIcon[kind]} size={size >= 36 ? 'toolbar' : 'xs'} tone="strong" />
    </View>
  );
}

function BottomRow({ item, onToggleSave }: { item: FeedCardItem; onToggleSave?: () => void }) {
  const thread = item.x?.threadCount && item.x.threadCount > 1 ? item.x.threadCount : null;
  return (
    <View className="mt-3 min-h-8 flex-row items-center gap-2">
      <Text level="caption-strong" tone="secondary">
        {item.seenAgo}
      </Text>
      {thread ? <Badge>共 {thread} 条</Badge> : null}
      <View className="flex-1" />
      <IconButton
        icon={item.isSaved ? IconBookmarkFilled : IconBookmark}
        label={item.isSaved ? '取消收藏' : '收藏'}
        on={item.isSaved}
        onPress={onToggleSave}
        plain
      />
    </View>
  );
}

function XPost({ item }: { item: FeedCardItem }) {
  const x = item.x!;
  return (
    <View className="gap-3">
      {x.repostedBy ? (
        <View className="flex-row items-center gap-1.5">
          <Icon icon={IconRepeat} size="xs" />
          <Text level="caption" tone="tertiary">
            {x.repostedBy} 转发了
          </Text>
        </View>
      ) : null}
      <View className="flex-row items-center gap-2">
        <Avatar name={x.author.name} size={36} uri={x.author.avatarUrl} />
        <View className="min-w-0 flex-1">
          <Text level="ui-strong" numberOfLines={1}>
            {x.author.name}{' '}
            <Text level="ui" tone="tertiary">
              @{x.author.handle}
            </Text>
          </Text>
          {x.replyTo ? (
            <Text level="caption" numberOfLines={1} tone="tertiary">
              回复 @{x.replyTo}
            </Text>
          ) : null}
        </View>
      </View>
      <Text level="body" numberOfLines={8}>
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
          <Text level="read" numberOfLines={4}>
            {x.quote.text}
          </Text>
        </View>
      ) : null}
      {item.imageUrl ? <Image className="h-48 w-full rounded-xl bg-sunken" source={{ uri: item.imageUrl }} /> : null}
    </View>
  );
}

function Article({ item }: { item: FeedCardItem }) {
  return (
    <View>
      <View className="mb-3 flex-row items-center gap-1.5">
        <SourceMark avatarUrl={item.source.avatarUrl} kind={item.source.kind} />
        <Text className="shrink" level="caption" numberOfLines={1} tone="secondary">
          {item.source.name}
          {item.source.byline ? ` · ${item.source.byline}` : ''}
        </Text>
      </View>
      <View className="flex-row gap-4">
        <View className="min-w-0 flex-1 gap-2">
          <Text level="card" numberOfLines={3}>
            {item.title}
          </Text>
          {item.summary ? (
            <Text level="read" numberOfLines={2} tone="secondary">
              {item.summary}
            </Text>
          ) : null}
        </View>
        {item.imageUrl ? (
          <Image className="mt-1 rounded-lg bg-sunken" source={{ uri: item.imageUrl }} style={{ width: 88, height: 96 }} />
        ) : null}
      </View>
    </View>
  );
}

export function FeedCard({ item, onOpen, onToggleSave }: { item: FeedCardItem; onOpen?: () => void; onToggleSave?: () => void }) {
  return (
    <Pressable accessibilityRole="button" className="active:bg-pressed" onPress={onOpen}>
      <View className="mx-6 border-b border-subtle pb-3 pt-6">
        {item.x ? <XPost item={item} /> : <Article item={item} />}
        <BottomRow item={item} onToggleSave={onToggleSave} />
      </View>
    </Pressable>
  );
}
