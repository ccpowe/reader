/** Story frames: a tab page with the bottom tab bar, and a page with a sheet over it. */
import { IconBookmark, IconChartBar, IconHome, IconRss, IconUser } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';

import { SheetBody, TabBar, type TabItem } from '@/ui';

type Tab = 'today' | 'boards' | 'subscriptions' | 'saved' | 'me';

export const tabs: TabItem<Tab>[] = [
  { key: 'today', label: '今天', icon: IconHome },
  { key: 'boards', label: '榜单', icon: IconChartBar },
  { key: 'subscriptions', label: '订阅', icon: IconRss },
  { key: 'saved', label: '收藏', icon: IconBookmark },
  { key: 'me', label: '我的', icon: IconUser },
];

export function TabFrame({ tab, children }: { tab: Tab; children: ReactNode }) {
  return (
    <View className="flex-1 bg-canvas">
      <View className="flex-1">{children}</View>
      <TabBar current={tab} tabs={tabs} />
    </View>
  );
}

/** A sheet drawn over the page (static, for review; the app uses <Sheet> in a Modal). */
export function WithSheet({ page, title, subtitle, children }: { page: ReactNode; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <View className="flex-1">
      {/* Behind an open sheet the page is out of reach, as under the app's Modal (and the alignment check skips it). */}
      <View className="flex-1" importantForAccessibility="no-hide-descendants">
        {page}
      </View>
      <Pressable accessibilityLabel="关闭" className="absolute inset-0 bg-overlay" />
      <View className="absolute bottom-0 left-0 right-0">
        <SheetBody onClose={() => undefined} subtitle={subtitle} title={title}>{children}</SheetBody>
      </View>
    </View>
  );
}
