/**
 * The frame of a page pushed over the tabs (a subscription's feed, profile, password, about):
 * back and a title in the top bar, an optional notice bar, then the content. Presentational.
 */
import { IconArrowLeft } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { IconButton, Message, TopBar } from '@/ui';

export function PushedPage({
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
