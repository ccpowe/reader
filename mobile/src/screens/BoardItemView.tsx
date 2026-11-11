/** Board item preview (PRODUCT §3.7): decide whether to read the original. One primary action. */
import { IconArrowLeft, IconBookmark, IconBookmarkFilled, IconLanguage } from '@tabler/icons-react-native';
import { Image, ScrollView, View } from 'react-native';

import { Button, IconButton, Tag, Text, TopBar } from '@/ui';

export function BoardItemView({
  board,
  title,
  translatedTitle,
  description,
  meta,
  tags = [],
  imageUrl,
  saved,
  hasDiscussion,
}: {
  board: string;
  title: string;
  translatedTitle?: string | null;
  description?: string | null;
  meta: string[];
  tags?: string[];
  imageUrl?: string | null;
  saved: boolean;
  hasDiscussion: boolean;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <TopBar
        actions={
          <>
            <IconButton icon={IconLanguage} label="翻译" />
            <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? '取消收藏' : '收藏'} on={saved} />
          </>
        }
        leading={<IconButton icon={IconArrowLeft} label="返回" />}
        title={board}
      />
      <ScrollView contentContainerClassName="gap-3 px-5 py-4">
        <Text level="h3">{translatedTitle ?? title}</Text>
        {translatedTitle ? <Text tone="secondary">{title}</Text> : null}
        {imageUrl ? <Image className="h-48 w-full rounded-xl bg-sunken" source={{ uri: imageUrl }} /> : null}
        {description ? <Text level="body">{description}</Text> : null}
        {tags.length > 0 ? (
          <View className="flex-row flex-wrap gap-1.5">
            {tags.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </View>
        ) : null}
        <Text level="caption" tone="tertiary">
          {meta.join(' · ')}
        </Text>
        <View className="mt-2 gap-2">
          <Button large title="打开原文" variant="primary" />
          {hasDiscussion ? <Button large title="打开讨论区" /> : null}
        </View>
      </ScrollView>
    </View>
  );
}
