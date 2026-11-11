/**
 * Reader page (PRODUCT §3.6, layout P23 R3-A): top bar with back, host, save and open-link;
 * floating tools on the right (translation original ↔ bilingual, P24; switch original web page
 * ↔ reader mode). The web content is a placeholder here; the real page hosts a WebView.
 */
import { IconArrowLeft, IconBook, IconBookmark, IconBookmarkFilled, IconExternalLink, IconLanguage, IconWorld } from '@tabler/icons-react-native';
import { Pressable, ScrollView, View } from 'react-native';

import { Icon, IconButton, Message, Text, TopBar } from '@/ui';

export type ArticleBusy = 'none' | 'translating' | 'quota';

function Floating({ bilingual, reader }: { bilingual: boolean; reader: boolean }) {
  return (
    <View className="absolute right-3 top-1/3 gap-2">
      <Pressable
        accessibilityLabel={bilingual ? '显示原文' : '显示双语'}
        className={`h-11 w-11 items-center justify-center rounded-full shadow-md ${bilingual ? 'bg-selected' : 'bg-raised'} active:bg-pressed`}
      >
        <Icon icon={IconLanguage} size="toolbar" tone={bilingual ? 'strong' : 'neutral'} />
      </Pressable>
      <Pressable
        accessibilityLabel={reader ? '原网页' : '阅读模式'}
        className="h-11 w-11 items-center justify-center rounded-full bg-raised shadow-md active:bg-pressed"
      >
        <Icon icon={reader ? IconWorld : IconBook} size="toolbar" />
      </Pressable>
    </View>
  );
}

export function ArticleView({
  host,
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
            <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? '取消收藏' : '收藏'} on={saved} />
            <IconButton icon={IconExternalLink} label="打开原链接" />
          </>
        }
        leading={<IconButton icon={IconArrowLeft} label="返回" />}
        title={host}
      />
      {busy === 'none' ? null : (
        <Message bar kind={busy === 'quota' ? 'warning' : 'info'}>
          {busy === 'translating' ? '正在翻译，已完成 3 / 12 段' : '翻译额度用完，约 1 分钟后可再试'}
        </Message>
      )}
      <ScrollView contentContainerClassName="gap-4 px-5 pb-12 pt-4">
        {!reader ? (
          <Text level="caption" tone="tertiary">
            原网页（原型占位，实际是网页本身）
          </Text>
        ) : null}
        <Text level="h2">{title}</Text>
        {paragraphs.map((paragraph, index) => (
          <View className="gap-1" key={index}>
            <Text level="body">{paragraph.original}</Text>
            {bilingual ? (
              <Text level="body" tone="secondary">
                {paragraph.translated}
              </Text>
            ) : null}
          </View>
        ))}
      </ScrollView>
      <Floating bilingual={bilingual} reader={reader} />
    </View>
  );
}
