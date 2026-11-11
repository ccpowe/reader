/** 登录 — placeholder until block 1 of the implementation plan (DECISIONS P28). */
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { EmptyState, Text } from '@/ui';

export default function Screen() {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 justify-center bg-canvas px-6" style={{ paddingTop: insets.top }}>
      <Text level="page">登录</Text>
      <EmptyState description="第 1 块实现。" title="还没做" />
    </View>
  );
}
