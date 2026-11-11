import { Text, View } from 'react-native';

/** Placeholder until the redesigned screens land; design work happens in Storybook. */
export default function Home() {
  return (
    <View className="flex-1 items-center justify-center bg-background p-6">
      <Text className="text-base text-foreground">Reader 新版正在设计中</Text>
      <Text className="mt-2 text-sm text-muted-foreground">用 pnpm run storybook 查看设计原型</Text>
    </View>
  );
}
