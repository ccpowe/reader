/**
 * Neutral building blocks for the layout exploration. Deliberately plain (gray, system font,
 * 8dp grid): look and feel come from the style pack in design step 5, not from here.
 */
import { Bookmark, BookmarkCheck, type LucideIcon } from 'lucide-react-native';
import type { ReactNode } from 'react';
import { Image, Pressable, ScrollView, Text, View } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';

export function useColor(name: string): string {
  return (useCSSVariable(`--color-${name}`) as string | undefined) ?? '#888';
}

export function Avatar({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <View
      className="items-center justify-center rounded-full bg-muted"
      style={{ width: size, height: size }}
    >
      <Text className="text-muted-foreground" style={{ fontSize: size * 0.5 }}>
        {name.slice(0, 1).toUpperCase()}
      </Text>
    </View>
  );
}

export function Thumb({ uri, size }: { uri: string; size: number }) {
  return (
    <Image
      className="rounded-md bg-muted"
      source={{ uri }}
      style={{ width: size, height: size }}
    />
  );
}

export function IconButton({
  icon: Icon,
  label,
  onPress,
  size = 20,
  active = false,
  className,
}: {
  icon: LucideIcon;
  label: string;
  onPress?: () => void;
  size?: number;
  active?: boolean;
  className?: string;
}) {
  const foreground = useColor('foreground');
  const muted = useColor('muted-foreground');
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      className={cn('items-center justify-center rounded-md active:bg-pressed', className)}
      hitSlop={8}
      onPress={onPress}
      style={{ minWidth: 40, minHeight: 40 }}
    >
      <Icon color={active ? foreground : muted} size={size} strokeWidth={2} />
    </Pressable>
  );
}

export function SaveButton({ saved }: { saved: boolean }) {
  return <IconButton active={saved} icon={saved ? BookmarkCheck : Bookmark} label={saved ? '取消收藏' : '收藏'} />;
}

export function Chip({ label, active = false }: { label: string; active?: boolean }) {
  return (
    <Pressable
      className={cn(
        'rounded-full border px-3 py-1.5 active:bg-pressed',
        active ? 'border-foreground bg-foreground' : 'border-border',
      )}
    >
      <Text className={cn('text-sm', active ? 'text-background' : 'text-foreground')}>{label}</Text>
    </Pressable>
  );
}

export function Divider() {
  return <View className="h-px bg-border" />;
}

/** A design note above a prototype; part of the story, not of the product layout. */
export function DesignNote({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <View className="border-b border-dashed border-border bg-muted px-4 py-2">
      <Text className="text-xs font-semibold text-muted-foreground">设计说明 · {title}</Text>
      {children ? <Text className="mt-0.5 text-xs text-muted-foreground">{children}</Text> : null}
    </View>
  );
}

/** A phone-width screen with a design note on top and scrollable content. */
export function Screen({ note, title, children }: { note: string; title: string; children: ReactNode }) {
  return (
    <View className="flex-1 bg-background">
      <DesignNote title={title}>{note}</DesignNote>
      <ScrollView>{children}</ScrollView>
    </View>
  );
}
