/** 今日 — placeholder until block 2 of the implementation plan (DECISIONS P28). */
import { EmptyState, PageHeader } from '@/ui';
import { View } from 'react-native';

export default function Screen() {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="今日" />
      <EmptyState description="第 2 块实现。" title="还没做" />
    </View>
  );
}
