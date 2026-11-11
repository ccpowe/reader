/**
 * R3 阅读页的工具条（SLICE.md）。A = 现状（右侧可拖动的悬浮按钮，收藏 / 外链在标题栏）；
 * B = 悬浮按钮 + 长按选三种翻译；C = 底部固定工具栏（滚动时收起）。
 * 网页内容用静态占位代替 WebView；"翻译中""额度用完"两种状态可在顶部切换。
 */
import type { Meta, StoryObj } from '@storybook/react-native';
import {
  ArrowLeft,
  BookOpen,
  Bookmark,
  ExternalLink,
  Globe,
  Languages,
  Share2,
} from 'lucide-react-native';
import { useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Pressable,
  ScrollView,
  Text,
  View,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
} from 'react-native';

import { cn } from '@/lib/cn';

import { DesignNote, IconButton, useColor } from './primitives';

type Variant = 'A' | 'B' | 'C';
type TranslationMode = 'original' | 'bilingual' | 'translated';
type Busy = 'none' | 'translating' | 'quota';

const paragraphs = [
  {
    original:
      'Evaluations are most useful when they run every day. A failing case then points at the change that caused it, and the history shows how capabilities move over time.',
    translated: '评测每天都跑的时候最有用：失败的用例能直接指向引起它的改动，历史记录也能看出能力随时间的变化。',
  },
  {
    original:
      'Keep each case small and attach a one-line reason explaining why it matters. Large suites without reasons decay: nobody remembers which failures are real.',
    translated: '让每个用例保持小巧，并附一句话说明它为什么重要。没有理由的大型评测集会慢慢失效：没人记得哪些失败是真的。',
  },
  {
    original:
      'Finally, treat flaky cases as bugs in the evaluation, not in the model. Fix or delete them before they teach everyone to ignore red.',
    translated: '最后，把不稳定的用例当作评测本身的缺陷，而不是模型的。在大家学会忽略红色之前，修好或删掉它们。',
  },
];

function Article({ mode, reader }: { mode: TranslationMode; reader: boolean }) {
  return (
    <View className="gap-4 px-5 py-4">
      <Text className="text-xs text-muted-foreground">{reader ? '阅读模式（从原网页提取的正文）' : '原网页（占位，实际是 WebView）'}</Text>
      <Text className="text-xl font-semibold text-foreground">Run your evals every day</Text>
      {[...paragraphs, ...paragraphs].map((paragraph, index) => (
        <View className="gap-1" key={index}>
          {mode !== 'translated' ? <Text className="text-base text-foreground">{paragraph.original}</Text> : null}
          {mode !== 'original' ? (
            <Text className={cn('text-base', mode === 'bilingual' ? 'text-muted-foreground' : 'text-foreground')}>
              {paragraph.translated}
            </Text>
          ) : null}
        </View>
      ))}
    </View>
  );
}

function TitleBar({ withActions }: { withActions: boolean }) {
  return (
    <View className="flex-row items-center border-b border-border px-1 py-1">
      <IconButton icon={ArrowLeft} label="返回" />
      <Text className="flex-1 text-sm text-muted-foreground" numberOfLines={1}>
        simonwillison.net
      </Text>
      {withActions ? (
        <>
          <IconButton icon={Bookmark} label="收藏" />
          <IconButton icon={ExternalLink} label="打开原链接" />
        </>
      ) : null}
    </View>
  );
}

function BusyBanner({ busy }: { busy: Busy }) {
  if (busy === 'none') return null;
  return (
    <View className="mx-4 mt-2 flex-row items-center gap-2 rounded-md bg-muted px-3 py-2">
      {busy === 'translating' ? <ActivityIndicator size="small" /> : null}
      <Text className="text-sm text-muted-foreground">
        {busy === 'translating' ? '正在翻译，已完成 3 / 12 段' : '翻译额度用完，约 1 分钟后可再试'}
      </Text>
    </View>
  );
}

function FloatingTools({ mode, onTranslate, onLongPress, reader, onSwitch }: {
  mode: TranslationMode;
  onTranslate: () => void;
  onLongPress?: () => void;
  reader: boolean;
  onSwitch: () => void;
}) {
  const foreground = useColor('foreground');
  const background = useColor('background');
  const active = mode !== 'original';
  return (
    <View className="absolute right-3 top-1/3 gap-2">
      <Pressable
        className={cn('h-11 w-11 items-center justify-center rounded-full border border-border shadow', active ? 'bg-foreground' : 'bg-background')}
        delayLongPress={350}
        onLongPress={onLongPress}
        onPress={onTranslate}
      >
        <Languages color={active ? background : foreground} size={20} />
      </Pressable>
      <Pressable
        className="h-11 w-11 items-center justify-center rounded-full border border-border bg-background shadow"
        onPress={onSwitch}
      >
        {reader ? <Globe color={foreground} size={20} /> : <BookOpen color={foreground} size={20} />}
      </Pressable>
    </View>
  );
}

const modeLabels: Record<TranslationMode, string> = { original: '原文', bilingual: '双语', translated: '只看译文' };

function ModeMenu({ mode, onChoose }: { mode: TranslationMode; onChoose: (mode: TranslationMode) => void }) {
  return (
    <View className="absolute right-16 top-1/3 overflow-hidden rounded-md border border-border bg-background shadow">
      {(Object.keys(modeLabels) as TranslationMode[]).map((key) => (
        <Pressable className="px-4 py-3 active:bg-pressed" key={key} onPress={() => onChoose(key)}>
          <Text className={cn('text-sm', key === mode ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
            {modeLabels[key]}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

function BottomBar({ mode, onMode, reader, onSwitch, offset }: {
  mode: TranslationMode;
  onMode: (mode: TranslationMode) => void;
  reader: boolean;
  onSwitch: () => void;
  offset: Animated.Value;
}) {
  return (
    <Animated.View style={{ position: 'absolute', left: 0, right: 0, bottom: 0, transform: [{ translateY: offset }] }}>
      <View className="flex-row items-center gap-1 border-t border-border bg-background px-1 pb-2 pt-1">
      <IconButton icon={ArrowLeft} label="返回" />
      <View className="flex-1 flex-row rounded-md bg-muted p-0.5">
        {(Object.keys(modeLabels) as TranslationMode[]).map((key) => (
          <Pressable
            className={cn('flex-1 items-center rounded py-1.5', key === mode ? 'bg-background' : '')}
            key={key}
            onPress={() => onMode(key)}
          >
            <Text className={cn('text-xs', key === mode ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
              {modeLabels[key]}
            </Text>
          </Pressable>
        ))}
      </View>
      <IconButton icon={reader ? Globe : BookOpen} label={reader ? '原网页' : '阅读模式'} onPress={onSwitch} />
      <IconButton icon={Bookmark} label="收藏" />
      <IconButton icon={Share2} label="分享" />
      </View>
    </Animated.View>
  );
}

function ReaderScreen({ variant, title, note }: { variant: Variant; title: string; note: string }) {
  const [mode, setMode] = useState<TranslationMode>('original');
  const [reader, setReader] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState<Busy>('none');
  const offset = useRef(new Animated.Value(0)).current;
  const lastY = useRef(0);

  function onScroll(event: NativeSyntheticEvent<NativeScrollEvent>) {
    if (variant !== 'C') return;
    const y = event.nativeEvent.contentOffset.y;
    const hidden = y > lastY.current && y > 40;
    lastY.current = y;
    Animated.timing(offset, { toValue: hidden ? 72 : 0, duration: 180, useNativeDriver: true }).start();
  }

  return (
    <View className="flex-1 bg-background">
      <DesignNote title={title}>{note}</DesignNote>
      <View className="flex-row gap-3 border-b border-dashed border-border bg-muted px-4 py-2">
        <Text className="text-xs text-muted-foreground">切换状态：</Text>
        {(['none', 'translating', 'quota'] as Busy[]).map((key) => (
          <Pressable key={key} onPress={() => setBusy(key)}>
            <Text className={cn('text-xs', key === busy ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
              {{ none: '正常', translating: '翻译中', quota: '额度用完' }[key]}
            </Text>
          </Pressable>
        ))}
      </View>
      {variant === 'C' ? null : <TitleBar withActions />}
      <BusyBanner busy={busy} />
      <ScrollView contentContainerStyle={{ paddingBottom: variant === 'C' ? 80 : 24 }} onScroll={onScroll} scrollEventThrottle={16}>
        <Article mode={mode} reader={reader} />
      </ScrollView>
      {variant === 'C' ? (
        <BottomBar mode={mode} offset={offset} onMode={setMode} onSwitch={() => setReader(!reader)} reader={reader} />
      ) : (
        <FloatingTools
          mode={mode}
          onLongPress={variant === 'B' ? () => setMenuOpen(true) : undefined}
          onSwitch={() => setReader(!reader)}
          onTranslate={() => {
            setMenuOpen(false);
            setMode(mode === 'original' ? 'bilingual' : 'original');
          }}
          reader={reader}
        />
      )}
      {variant === 'B' && menuOpen ? (
        <ModeMenu
          mode={mode}
          onChoose={(next) => {
            setMode(next);
            setMenuOpen(false);
          }}
        />
      ) : null}
    </View>
  );
}

const meta: Meta = { title: 'R3 阅读页的工具条', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const A: Story = {
  name: 'R3-A 现状',
  render: () => (
    <ReaderScreen
      note="右侧悬浮按钮：上面是翻译（原文 ↔ 双语），下面是切换原网页 / 阅读模式；收藏和打开原链接在顶部标题栏。现状可上下拖动，原型里固定。"
      title="R3-A 现状"
      variant="A"
    />
  ),
};

export const B: Story = {
  name: 'R3-B 悬浮按钮 + 长按菜单',
  render: () => (
    <ReaderScreen
      note="同 A；单击翻译仍是原文 ↔ 双语，长按翻译按钮弹出「原文 / 双语 / 只看译文」。"
      title="R3-B 悬浮按钮 + 长按菜单"
      variant="B"
    />
  ),
};

export const C: Story = {
  name: 'R3-C 底部工具栏',
  render: () => (
    <ReaderScreen
      note="底部固定工具栏：返回、翻译三段切换、原网页 / 阅读模式、收藏、分享；往下滚动收起，往上滚动出现。"
      title="R3-C 底部工具栏"
      variant="C"
    />
  ),
};
