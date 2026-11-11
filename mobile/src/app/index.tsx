import { Text, View } from 'react-native';

/** Placeholder until the redesigned screens land; design work happens in Storybook. */
export default function Home() {
  return (
    <View className="flex-1 items-center justify-center bg-canvas p-6">
      <Text className="text-body text-primary">Reader 新版正在设计中</Text>
      <Text className="mt-2 text-ui text-secondary">用 pnpm run storybook 查看设计原型</Text>
    </View>
  );
}
