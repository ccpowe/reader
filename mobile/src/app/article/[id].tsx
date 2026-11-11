/** Reader page — placeholder until block 3 (DECISIONS P28). */
import { IconArrowLeft } from '@tabler/icons-react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { EmptyState, IconButton, TopBar } from '@/ui';

export default function Article() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <TopBar leading={<IconButton icon={IconArrowLeft} label="返回" onPress={() => router.back()} />} title="文章" />
      <EmptyState description={`第 3 块实现（${id}）。`} title="阅读页还没做" />
    </View>
  );
}
