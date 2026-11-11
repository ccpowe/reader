/**
 * Calibration (design flow step 6): the Today page in four styles, same content, so the user can
 * rank them. Each variant is fully specified below; values for 1 come from the old app's code
 * (ui/tokens.ts, components/PageHeader.tsx, FeedCards.tsx, Chip.tsx, ReaderTabBar.tsx).
 * Colors are written out here on purpose (this page compares palettes), so the qp lint skips it.
 */
import type { Meta, StoryObj } from '@storybook/react-native';
import {
  IconAdjustmentsHorizontal,
  IconBookmark,
  IconBookmarkFilled,
  IconBrandReddit,
  IconChartBar,
  IconHome,
  IconRss,
  IconUser,
} from '@tabler/icons-react-native';
import { Image, ScrollView, Text, View, type TextStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

type Spec = {
  name: string;
  colors: { bg: string; primary: string; secondary: string; muted: string; border: string; mark: string; selectedTab: string };
  side: number;
  cardTop: number;
  cardBottom: number;
  title: TextStyle;
  summary: TextStyle;
  source: TextStyle;
  meta: 'bottom' | 'top';
  pageTitle: TextStyle;
  brandRow: boolean;
  tabs: 'underline' | 'pill';
  tabText: TextStyle;
  tabBarLabels: boolean;
  markRadius: number;
};

const OLD = { bg: '#FCFBF8', primary: '#242924', secondary: '#687062', muted: '#858B7F', border: '#E7E7E7', mark: '#E2E3E1', selectedTab: '#242424' };
const QP = { bg: '#FFFFFF', primary: '#2c2c2b', secondary: '#7d7a75', muted: '#a19e99', border: 'rgba(42, 28, 0, 0.07)', mark: '#f0efed', selectedTab: 'rgba(33, 27, 23, 0.05)' };
const QP_WARM = { ...QP, bg: '#FCFBF8' };

const specs: Spec[] = [
  {
    name: '1 旧版原样',
    colors: OLD,
    side: 24,
    cardTop: 22,
    cardBottom: 15,
    title: { fontSize: 18, lineHeight: 28, fontWeight: '700', letterSpacing: -0.35 },
    summary: { fontSize: 13, lineHeight: 22 },
    source: { fontSize: 11 },
    meta: 'bottom',
    pageTitle: { fontSize: 34, fontWeight: '700', letterSpacing: -1.2 },
    brandRow: true,
    tabs: 'underline',
    tabText: { fontSize: 14, fontWeight: '600' },
    tabBarLabels: false,
    markRadius: 6,
  },
  {
    name: '2 quietpaper 原样',
    colors: QP,
    side: 16,
    cardTop: 12,
    cardBottom: 12,
    title: { fontSize: 16, lineHeight: 24, fontWeight: '600' },
    summary: { fontSize: 13, lineHeight: 18 },
    source: { fontSize: 12 },
    meta: 'top',
    pageTitle: { fontSize: 24, fontWeight: '600' },
    brandRow: false,
    tabs: 'pill',
    tabText: { fontSize: 14 },
    tabBarLabels: true,
    markRadius: 10,
  },
  {
    name: '3 quietpaper + 旧版尺度',
    colors: QP_WARM,
    side: 24,
    cardTop: 22,
    cardBottom: 15,
    title: { fontSize: 18, lineHeight: 28, fontWeight: '600', letterSpacing: -0.35 },
    summary: { fontSize: 14, lineHeight: 22 },
    source: { fontSize: 12 },
    meta: 'bottom',
    pageTitle: { fontSize: 34, fontWeight: '700', letterSpacing: -1.2 },
    brandRow: true,
    tabs: 'pill',
    tabText: { fontSize: 14 },
    tabBarLabels: false,
    markRadius: 6,
  },
  {
    name: '4 介于两者之间',
    colors: QP_WARM,
    side: 20,
    cardTop: 16,
    cardBottom: 12,
    title: { fontSize: 17, lineHeight: 26, fontWeight: '600', letterSpacing: -0.2 },
    summary: { fontSize: 13, lineHeight: 20 },
    source: { fontSize: 12 },
    meta: 'bottom',
    pageTitle: { fontSize: 28, fontWeight: '700', letterSpacing: -0.6 },
    brandRow: true,
    tabs: 'pill',
    tabText: { fontSize: 14 },
    tabBarLabels: false,
    markRadius: 6,
  },
];

type Item = { source: string; kind: 'rss' | 'reddit'; title: string; summary: string; ago: string; saved: boolean; image?: string };

const items: Item[] = [
  { source: 'OpenAI News', kind: 'rss', title: 'Gilbert + Tobin 借助 AI 重新思考法律行业的经营方式', summary: 'A conversation with Sam Nickless, Partner and Chief Executive Officer at Gilbert + Tobin.', ago: '11 小时前', saved: false },
  { source: '少数派 · 程天冲', kind: 'rss', title: '城市漫步指南 | 安特卫普，时尚就在生活中', summary: '原本今年夏天的旅行是要在佛罗伦萨结束的，但突发状况导致我不用上班了，就顺势使用了我的国际机票退改券，增加了一段比利时之旅。', ago: '3 小时前', saved: true },
  { source: 'r/MachineLearning · /u/council_of_desires', kind: 'reddit', title: '寻求研究合作伙伴与资金支持：探索生物智能与神经计算 [D]', summary: 'Can living neural systems provide new insights into computation, adaptation, and intelligence? We are developing a platform for this.', ago: '5 小时前', saved: false },
  { source: 'Simon Willison', kind: 'rss', title: '把模型评测做成每天跑的测试', summary: '与其在发布前一次性评测，不如把关键用例写成每天都跑的测试：失败时能立刻知道是哪个改动引起的。', ago: '6 小时前', saved: false, image: 'https://picsum.photos/seed/evals/400/400' },
];

const folders = ['全部', 'Claude', 'code', 'OpenAI', 'Tool', '未分类'];

function Header({ spec }: { spec: Spec }) {
  const c = spec.colors;
  return (
    <View style={{ paddingHorizontal: spec.side, paddingTop: 8 }}>
      {spec.brandRow ? (
        <View style={{ height: 36, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <View style={{ width: 32, height: 32, borderRadius: 8, backgroundColor: c.mark }} />
            <Text style={{ color: c.primary, fontSize: 12, fontWeight: '700', letterSpacing: 2 }}>READER</Text>
          </View>
          <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: c.mark }} />
        </View>
      ) : null}
      <View style={{ height: 52, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spec.brandRow ? 8 : 0 }}>
        <Text style={[{ color: c.primary }, spec.pageTitle]}>今日</Text>
        <Text style={{ color: c.secondary, fontSize: 13 }}>11 月 11 日 · 星期三</Text>
      </View>
    </View>
  );
}

function Folders({ spec }: { spec: Spec }) {
  const c = spec.colors;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', borderBottomWidth: 1, borderBottomColor: c.border, paddingLeft: spec.side, paddingRight: 8 }}>
      <ScrollView contentContainerStyle={{ alignItems: 'center', gap: spec.tabs === 'pill' ? 2 : 21 }} horizontal showsHorizontalScrollIndicator={false} style={{ flex: 1 }}>
        {folders.map((name, index) => {
          const active = index === 0;
          if (spec.tabs === 'underline') {
            return (
              <View key={name} style={{ height: 44, justifyContent: 'center' }}>
                <Text style={[{ color: active ? c.primary : c.muted }, spec.tabText]}>{name}</Text>
                <View style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 3, borderRadius: 2, backgroundColor: active ? c.selectedTab : 'transparent' }} />
              </View>
            );
          }
          return (
            <View key={name} style={{ height: 44, justifyContent: 'center' }}>
              <View style={{ height: 30, paddingHorizontal: 12, borderRadius: 15, justifyContent: 'center', backgroundColor: active ? c.selectedTab : 'transparent' }}>
                <Text style={[{ color: active ? c.primary : c.secondary }, spec.tabText]}>{name}</Text>
              </View>
            </View>
          );
        })}
      </ScrollView>
      <View style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center' }}>
        <IconAdjustmentsHorizontal color={c.secondary} size={20} strokeWidth={2.1} />
      </View>
    </View>
  );
}

function Card({ spec, item }: { spec: Spec; item: Item }) {
  const c = spec.colors;
  const Mark = item.kind === 'reddit' ? IconBrandReddit : IconRss;
  const Bookmark = item.saved ? IconBookmarkFilled : IconBookmark;
  const bookmark = <Bookmark color={item.saved ? c.primary : c.muted} size={20} strokeWidth={2.1} />;
  return (
    <View style={{ marginHorizontal: spec.side, paddingTop: spec.cardTop, paddingBottom: spec.cardBottom, borderBottomWidth: 1, borderBottomColor: c.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: spec.meta === 'bottom' ? 11 : 6 }}>
        <View style={{ width: 22, height: 22, borderRadius: spec.markRadius, backgroundColor: c.mark, alignItems: 'center', justifyContent: 'center' }}>
          <Mark color={c.primary} size={12} strokeWidth={2.1} />
        </View>
        <Text numberOfLines={1} style={[{ color: c.secondary, flexShrink: 1 }, spec.source]}>
          {item.source}
        </Text>
        {spec.meta === 'top' ? <Text style={[{ color: c.muted, flex: 1 }, spec.source]}> · {item.ago}</Text> : <View style={{ flex: 1 }} />}
        {spec.meta === 'top' ? bookmark : null}
      </View>
      <View style={{ flexDirection: 'row', gap: 17 }}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={3} style={[{ color: c.primary }, spec.title]}>
            {item.title}
          </Text>
          <Text numberOfLines={2} style={[{ color: c.secondary, marginTop: 7 }, spec.summary]}>
            {item.summary}
          </Text>
        </View>
        {item.image ? <Image source={{ uri: item.image }} style={{ width: 86, height: 94, borderRadius: 8, marginTop: 3, backgroundColor: c.mark }} /> : null}
      </View>
      {spec.meta === 'bottom' ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10, minHeight: 32 }}>
          <Text style={{ color: c.secondary, fontSize: 11, fontWeight: '500' }}>{item.ago}</Text>
          {bookmark}
        </View>
      ) : null}
    </View>
  );
}

function TabBar({ spec }: { spec: Spec }) {
  const c = spec.colors;
  const insets = useSafeAreaInsets();
  const tabs = [
    ['今天', IconHome],
    ['榜单', IconChartBar],
    ['订阅', IconRss],
    ['收藏', IconBookmark],
    ['我的', IconUser],
  ] as const;
  return (
    <View style={{ flexDirection: 'row', borderTopWidth: 1, borderTopColor: c.border, backgroundColor: c.bg, paddingBottom: insets.bottom }}>
      {tabs.map(([label, Glyph], index) => (
        <View key={label} style={{ flex: 1, height: spec.tabBarLabels ? 56 : 52, alignItems: 'center', justifyContent: 'center', gap: 2 }}>
          <Glyph color={index === 0 ? c.primary : c.muted} size={22} strokeWidth={2.1} />
          {spec.tabBarLabels ? <Text style={{ fontSize: 12, color: index === 0 ? c.primary : c.muted }}>{label}</Text> : null}
        </View>
      ))}
    </View>
  );
}

function Page({ spec }: { spec: Spec }) {
  return (
    <View style={{ flex: 1, backgroundColor: spec.colors.bg }}>
      <ScrollView>
        <Header spec={spec} />
        <Folders spec={spec} />
        {items.map((item) => (
          <Card item={item} key={item.title} spec={spec} />
        ))}
      </ScrollView>
      <TabBar spec={spec} />
    </View>
  );
}

const meta: Meta = { title: '校准 · 今天页', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const Old: Story = { name: specs[0].name, render: () => <Page spec={specs[0]} /> };
export const Quietpaper: Story = { name: specs[1].name, render: () => <Page spec={specs[1]} /> };
export const QuietpaperOldScale: Story = { name: specs[2].name, render: () => <Page spec={specs[2]} /> };
export const Between: Story = { name: specs[3].name, render: () => <Page spec={specs[3]} /> };
