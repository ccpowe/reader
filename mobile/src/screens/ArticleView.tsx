/**
 * Reader page (PRODUCT §3.6), old app layout (device screenshot), P23 R3-A: a top bar — back,
 * "文章", save, share, open in browser — above the page; on the right edge a floating pill with
 * translation (original ↔ bilingual, P24) and reader mode (original page ↔ reader view, P13).
 * The web content is a placeholder here; the real page hosts a WebView.
 */
import {
  IconArrowLeft,
  IconBook,
  IconBookmark,
  IconBookmarkFilled,
  IconExternalLink,
  IconLanguage,
  IconShare2,
  IconWorld,
} from '@tabler/icons-react-native';
import { Pressable, ScrollView, View } from 'react-native';

import { cn } from '@/lib/cn';
import { Icon, IconButton, Message, Text, TopBar, useShadow } from '@/ui';

export type ArticleBusy = 'none' | 'translating' | 'quota';

function FloatingTools({ bilingual, reader }: { bilingual: boolean; reader: boolean }) {
  const shadow = useShadow('md');
  const tool = (on: boolean) => cn('h-12 w-12 items-center justify-center rounded-full active:bg-pressed', on ? 'bg-selected' : '');
  return (
    <View className="absolute right-2 top-1/2 rounded-full bg-raised p-1" style={shadow}>
      <Pressable
        accessibilityLabel="双语对照"
        accessibilityRole="switch"
        accessibilityState={{ checked: bilingual }}
        className={tool(bilingual)}
      >
        <Icon icon={IconLanguage} size="toolbar" tone={bilingual ? 'strong' : 'neutral'} />
      </Pressable>
      <Pressable
        accessibilityLabel="阅读模式"
        accessibilityRole="switch"
        accessibilityState={{ checked: reader }}
        className={tool(reader)}
      >
        <Icon icon={reader ? IconBook : IconWorld} size="toolbar" tone={reader ? 'strong' : 'neutral'} />
      </Pressable>
    </View>
  );
}

export function ArticleView({
  title,
  paragraphs,
  saved,
  bilingual,
  reader,
  busy = 'none',
}: {
  host: string;
  title: string;
  paragraphs: { original: string; translated: string }[];
  saved: boolean;
  bilingual: boolean;
  reader: boolean;
  busy?: ArticleBusy;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <TopBar
        actions={
          <>
            <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? '取消收藏' : '收藏'} on={saved} plain />
            <IconButton icon={IconShare2} label="分享" />
            <IconButton icon={IconExternalLink} label="在浏览器打开" />
          </>
        }
        leading={<IconButton icon={IconArrowLeft} label="返回" />}
        title="文章"
      />
      {busy === 'none' ? null : (
        <Message bar kind={busy === 'quota' ? 'warning' : 'info'}>
          {busy === 'translating' ? '正在翻译，已完成 3 / 12 段' : '翻译额度用完，约 1 分钟后可再试'}
        </Message>
      )}
      <ScrollView contentContainerClassName="gap-4 px-6 pb-12 pt-6">
        {!reader ? (
          <Text level="caption" tone="tertiary">
            原网页（原型占位，实际是网页本身）
          </Text>
        ) : null}
        <Text level="h1">{title}</Text>
        {paragraphs.map((paragraph, index) => (
          <View className="gap-2" key={index}>
            <Text level="body">{paragraph.original}</Text>
            {bilingual ? (
              <Text level="body" tone="secondary">
                {paragraph.translated}
              </Text>
            ) : null}
          </View>
        ))}
      </ScrollView>
      <FloatingTools bilingual={bilingual} reader={reader} />
    </View>
  );
}
