/**
 * The five tabs (P14), with the quietpaper tab bar (icons only, P25). The tab bar lies over the
 * pages and slides away with their collapsing chrome (components/CollapsingChrome).
 */
import { IconBookmark, IconChartBar, IconHome, IconRss, IconUser } from '@tabler/icons-react-native';
import { Tabs } from 'expo-router';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ChromeProvider, CollapsingTabBar, useRevealTab, type ChromeTab } from '@/components/CollapsingChrome';
import { TabBar, type TabItem } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

type Tab = ChromeTab;

const icons = { index: IconHome, boards: IconChartBar, subscriptions: IconRss, saved: IconBookmark, me: IconUser };
const keys: Tab[] = ['index', 'boards', 'subscriptions', 'saved', 'me'];
const tabLabel = (key: Tab) => (key === 'index' ? t().tabs.today : t().tabs[key]);

export default function TabsLayout() {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <ChromeProvider>
        <TabsWithChrome />
      </ChromeProvider>
    </View>
  );
}

function TabsWithChrome() {
  useLanguage(); // texts follow the interface language
  const revealTab = useRevealTab();
  const tabs: TabItem<Tab>[] = keys.map((key) => ({ key, label: tabLabel(key), icon: icons[key] }));
  return (
    <Tabs
      screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: 'transparent' } }}
      tabBar={({ state, navigateToTab }) => {
        const current = state.routes[state.index].name as Tab;
        return (
          <CollapsingTabBar current={current}>
            <TabBar
              current={current}
              onSelect={(name) => {
                // A tab pressed shows its page's chrome again (old app).
                revealTab(current);
                revealTab(name);
                const route = state.routes.find((r) => r.name === name);
                if (route) navigateToTab(route.key);
              }}
              tabs={tabs}
            />
          </CollapsingTabBar>
        );
      }}
    >
      {tabs.map((tab) => (
        <Tabs.Screen key={tab.key} name={tab.key} />
      ))}
    </Tabs>
  );
}
