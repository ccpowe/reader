import { Image, View } from 'react-native';

import { Text } from './Text';

/** qp-avatar: neutral sunken circle with an initial (never a solid colored disc). */
export function Avatar({ name, uri, size = 20 }: { name: string; uri?: string | null; size?: number }) {
  if (uri) {
    return <Image className="rounded-full bg-sunken" source={{ uri }} style={{ width: size, height: size }} />;
  }
  return (
    <View className="items-center justify-center rounded-full bg-sunken" style={{ width: size, height: size }}>
      <Text level={size >= 28 ? 'ui' : 'caption'} tone="secondary">
        {name.trim().slice(0, 1).toUpperCase()}
      </Text>
    </View>
  );
}
