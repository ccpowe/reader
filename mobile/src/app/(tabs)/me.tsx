/** 我的 — placeholder until block 7 of the implementation plan (DECISIONS P28). */
import { EmptyState, PageHeader } from '@/ui';
import { View } from 'react-native';

export default function Screen() {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="我的" />
      <EmptyState description="第 7 块实现。" title="还没做" />
    </View>
  );
}
