/**
 * Reader page shell (PRODUCT §3.6), old app layout (device screenshot), P23 R3-A: a top bar —
 * back, "文章", save, share, open in browser — above the content; a thin load progress line; one
 * in-place problem line; on the right edge a floating pill with the page's tools (translation
 * original ↔ bilingual P24, reader mode P13 / player ↔ web page). The content (a WebView) is the
 * `children`. Presentational: the route passes state and callbacks.
 */
import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import { IconArrowLeft, IconBookmark, IconBookmarkFilled, IconExternalLink, IconShare2 } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { cn } from '@/lib/cn';
import { Icon, IconButton, Message, TopBar, useShadow } from '@/ui';

export type ReaderTool = { key: string; icon: TablerIcon; label: string; on: boolean; onPress?: () => void };

function FloatingTools({ tools }: { tools: ReaderTool[] }) {
  const shadow = useShadow('md');
  if (tools.length === 0) return null;
  return (
    <View className="absolute right-2 top-1/2 rounded-full bg-raised p-1" style={shadow} testID="align-ignore">
      {tools.map((tool) => (
        <Pressable
          accessibilityLabel={tool.label}
          accessibilityRole="switch"
          accessibilityState={{ checked: tool.on }}
          className={cn('h-12 w-12 items-center justify-center rounded-full active:bg-pressed', tool.on ? 'bg-selected' : '')}
          key={tool.key}
          onPress={tool.onPress}
        >
          <Icon icon={tool.icon} size="toolbar" tone={tool.on ? 'strong' : 'neutral'} />
        </Pressable>
      ))}
    </View>
  );
}

export function ArticleView({
  title = '文章',
  saved,
  progress,
  problem,
  tools = [],
  children,
  onBack,
  onToggleSave,
  onShare,
  onOpenExternal,
}: {
  /** 文章, 视频 or 帖子 (old app) */
  title?: string;
  saved: boolean;
  /** page load progress 0–1; hidden at 1 */
  progress?: number;
  /** one line about what went wrong, with an action */
  problem?: { message: string; action?: ReactNode } | null;
  tools?: ReaderTool[];
  children: ReactNode;
  onBack?: () => void;
  onToggleSave?: () => void;
  onShare?: () => void;
  onOpenExternal?: () => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <TopBar
        actions={
          <>
            <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? '取消收藏' : '收藏'} on={saved} onPress={onToggleSave} plain />
            <IconButton icon={IconShare2} label="分享" onPress={onShare} />
            <IconButton icon={IconExternalLink} label="在浏览器打开" onPress={onOpenExternal} />
          </>
        }
        leading={<IconButton icon={IconArrowLeft} label="返回" onPress={onBack} />}
        title={title}
      />
      <View className="h-0.5 bg-sidebar">
        {progress !== undefined && progress < 1 ? <View className="h-full bg-accent" style={{ width: `${Math.max(5, progress * 100)}%` }} /> : null}
      </View>
      {problem ? (
        <Message action={problem.action} bar kind="warning">
          {problem.message}
        </Message>
      ) : null}
      <View className="flex-1" style={{ paddingBottom: insets.bottom }}>
        {children}
      </View>
      <FloatingTools tools={tools} />
    </View>
  );
}
