/** The five tabs (P14), with the quietpaper tab bar (icons only, P25). */
import { IconBookmark, IconChartBar, IconHome, IconRss, IconUser } from '@tabler/icons-react-native';
import { Tabs } from 'expo-router';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { TabBar, type TabItem } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

type Tab = 'index' | 'boards' | 'subscriptions' | 'saved' | 'me';

const icons = { index: IconHome, boards: IconChartBar, subscriptions: IconRss, saved: IconBookmark, me: IconUser };
const keys: Tab[] = ['index', 'boards', 'subscriptions', 'saved', 'me'];
const tabLabel = (key: Tab) => (key === 'index' ? t().tabs.today : t().tabs[key]);

export default function TabsLayout() {
  useLanguage(); // texts follow the interface language
  const insets = useSafeAreaInsets();
  const tabs: TabItem<Tab>[] = keys.map((key) => ({ key, label: tabLabel(key), icon: icons[key] }));
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
