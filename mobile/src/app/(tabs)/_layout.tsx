/** The five tabs (P14), with the quietpaper tab bar (icons only, P25). */
import { IconBookmark, IconChartBar, IconHome, IconRss, IconUser } from '@tabler/icons-react-native';
import { Tabs } from 'expo-router';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { TabBar, type TabItem } from '@/ui';

type Tab = 'index' | 'boards' | 'subscriptions' | 'saved' | 'me';

const tabs: TabItem<Tab>[] = [
  { key: 'index', label: '今日', icon: IconHome },
  { key: 'boards', label: '榜单', icon: IconChartBar },
  { key: 'subscriptions', label: '订阅', icon: IconRss },
  { key: 'saved', label: '收藏', icon: IconBookmark },
  { key: 'me', label: '我的', icon: IconUser },
];

export default function TabsLayout() {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <Tabs
        screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: 'transparent' } }}
        tabBar={({ state, navigateToTab }) => (
          <TabBar
            current={state.routes[state.index].name as Tab}
            onSelect={(name) => {
              const route = state.routes.find((r) => r.name === name);
              if (route) navigateToTab(route.key);
            }}
            tabs={tabs}
          />
        )}
      >
        {tabs.map((tab) => (
          <Tabs.Screen key={tab.key} name={tab.key} />
        ))}
      </Tabs>
    </View>
  );
}
