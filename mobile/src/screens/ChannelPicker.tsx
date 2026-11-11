/** Content of the 频道选择 sheet (PRODUCT §3.1): pick one subscription to show alone. */
import { ScrollView, View } from 'react-native';

import type { SubscriptionItem } from '@/components/SubscriptionRow';
import { Avatar, Count, Row, SearchField, SectionHeading } from '@/ui';

export function ChannelPicker({ folders }: { folders: { name: string; items: SubscriptionItem[] }[] }) {
  return (
    <View className="-mx-4" style={{ maxHeight: 480 }}>
      <SearchField placeholder="搜索订阅" />
      <ScrollView>
        {folders.map((folder) => (
          <View key={folder.name}>
            <SectionHeading title={folder.name} />
            {folder.items.map((item) => (
              <Row
                key={item.id}
                label={item.name}
                leading={<Avatar name={item.name} size={24} />}
                onPress={() => undefined}
                trailing={item.newCount > 0 ? <Count value={item.newCount} /> : null}
              />
            ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
