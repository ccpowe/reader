/**
 * Design step 5: every main view with quietpaper applied, layouts from P23 (R1-A R2-A R3-A R4-B),
 * light theme (dark is a draft until step 6). Story names: "<area> · <state>".
 */
import type { Meta, StoryObj } from '@storybook/react-native';

import { IconArticle, IconLanguage, IconWorld } from '@tabler/icons-react-native';
import { ScrollView, View } from 'react-native';

import { ArticleView, type ReaderTool } from '@/screens/ArticleView';
import { ConnectView, SignInView } from '@/screens/AuthViews';
import type { BoardEntryItem } from '@/components/BoardEntry';
import { BoardList, BoardOptions, BoardsView, type BoardKey, type Freshness } from '@/screens/BoardsView';
import { FeedList, type FeedListState } from '@/components/FeedList';
import { ChannelPicker } from '@/screens/ChannelPicker';
import { Button, EmptyState, Text } from '@/ui';
import { MeView } from '@/screens/MeView';
import { SavedView } from '@/screens/SavedView';
import { AddSubscriptionForm, SubscriptionMenu, SubscriptionsView } from '@/screens/SubscriptionsView';
import { TodayView } from '@/screens/TodayView';

import { article, boardEntries, feed, folders, subscriptions, user } from './fixtures';
import { TabFrame, WithSheet } from './frame';

const meta: Meta = { title: '页面', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

const story = (name: string, render: () => React.ReactElement): Story => ({ name, render });

// --- 今天 -----------------------------------------------------------------------

const todayFolders = folders.map((name) => ({ key: name, label: name }));
const ready = (items: typeof feed, extra: Partial<Extract<FeedListState, { kind: 'ready' }>> = {}): FeedListState => ({
  kind: 'ready',
  items,
  hasMore: true,
  loadingMore: false,
  ...extra,
});
const emptyFeed = <EmptyState description="订阅有新内容时会出现在这里。" title="还没有内容" />;
const today = (props: Partial<React.ComponentProps<typeof TodayView>>, list: FeedListState) => (
  <TabFrame tab="today">
    <TodayView date="11 月 11 日 · 星期三" folder={null} folders={todayFolders} newCount={1520} user={user} {...props}>
      <FeedList empty={emptyFeed} state={list} />
    </TodayView>
  </TabFrame>
);
const channelRows = subscriptions.map((group) => ({
  key: group.name,
  label: group.name,
  rows: group.items.map((item) => ({ id: item.id, name: item.name, kind: item.kind, address: item.address, newCount: item.newCount, includeInHome: item.id !== 's3' })),
}));

export const Today = story('今天 · 正常', () => today({}, ready(feed)));
export const TodayFolder = story('今天 · 选了文件夹', () => today({ folder: 'AI' }, ready(feed.slice(0, 4))));
export const TodayChannel = story('今天 · 只看一个订阅', () => today({ channel: 'Simon Willison' }, ready([feed[0], feed[3]])));
export const TodayLoading = story('今天 · 加载中', () => today({}, { kind: 'loading' }));
export const TodayFailed = story('今天 · 刷新失败（保留旧内容）', () => today({}, ready(feed, { refreshFailed: '刷新没有成功，显示的是之前的内容。' })));
export const TodayLoadFailed = story('今天 · 加载失败（没有内容）', () => today({}, { kind: 'failed', message: '连不上服务器。检查网络后再试。' }));
export const TodayMore = story('今天 · 加载更早的内容', () => today({}, ready(feed.slice(0, 2), { loadingMore: true })));
export const TodayEmpty = story('今天 · 没有新内容', () => today({ folder: '编程' }, ready([], { hasMore: false })));
export const TodayBanner = story('今天 · 没有网络横幅', () => today({ banner: '没有网络，显示的是之前的内容。' }, ready(feed)));
export const TodayChannelPicker = story('今天 · 频道选择弹层', () => (
  <WithSheet page={today({}, ready(feed))} subtitle="勾选的频道显示在全部中" title="选择频道">
    <ChannelPicker groups={channelRows} />
  </WithSheet>
));

// --- 榜单 -----------------------------------------------------------------------

type StoryBoardState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'no-community' }
  | { kind: 'ready'; entries: BoardEntryItem[]; freshness: Freshness };
const boards = (board: BoardKey, state: StoryBoardState, label?: string, options = false) => (
  <TabFrame tab="boards">
    <BoardsView board={board} user={user}>
      <BoardList
        label={label}
        onOpenOptions={options ? () => undefined : undefined}
        state={state.kind === 'ready' ? { kind: 'ready', freshness: state.freshness, rows: state.entries.map((entry) => ({ ...entry, url: `https://example.com/${entry.rank}` })) } : state}
      />
    </BoardsView>
  </TabFrame>
);

export const BoardsGithub = story('榜单 · GitHub 周榜', () => boards('github', { kind: 'ready', entries: boardEntries.github, freshness: { kind: 'fresh', age: '2 分钟前' } }, '周榜', true));
export const BoardsHn = story('榜单 · Hacker News', () => boards('hn', { kind: 'ready', entries: boardEntries.hn, freshness: { kind: 'fresh', age: '刚刚' } }));
export const BoardsPh = story('榜单 · Product Hunt 今天', () => boards('product_hunt', { kind: 'ready', entries: boardEntries.product_hunt, freshness: { kind: 'fresh', age: '12 分钟前' } }, '今天', true));
export const BoardsRedditPaused = story('榜单 · Reddit 暂停中', () =>
  boards('reddit', { kind: 'ready', entries: boardEntries.reddit, freshness: { kind: 'paused', reason: '被对方限流', resumesIn: '约 8 分钟' } }, 'r/codex · 热门', true),
);
export const BoardsStale = story('榜单 · 已过期（刷新失败）', () => boards('hn', { kind: 'ready', entries: boardEntries.hn, freshness: { kind: 'stale', age: '48 分钟前' } }));
export const BoardsTechTwitter = story('榜单 · TechTwitter 暂不可用', () => boards('techtwitter', { kind: 'unavailable', reason: '对方开启了人机验证，服务器暂时抓不到' }));
export const BoardsRedditNoCommunity = story('榜单 · Reddit 还没选社区', () => boards('reddit', { kind: 'no-community' }));
export const BoardsLoading = story('榜单 · 加载中', () => boards('github', { kind: 'loading' }, '日榜', true));
export const BoardsOptionsGithub = story('榜单 · 调整弹层（GitHub）', () => (
  <WithSheet page={boards('github', { kind: 'loading' }, '周榜', true)} title="调整 GitHub">
    <BoardOptions view={{ board: 'github', variant: 'weekly', subreddit: null }} />
  </WithSheet>
));
export const BoardsOptionsPh = story('榜单 · 调整弹层（Product Hunt）', () => (
  <WithSheet page={boards('product_hunt', { kind: 'loading' }, '今天', true)} title="调整 Product Hunt">
    <BoardOptions view={{ board: 'product_hunt', variant: 'today', subreddit: null }} />
  </WithSheet>
));
export const BoardsOptionsReddit = story('榜单 · 调整弹层（Reddit）', () => (
  <WithSheet page={boards('reddit', { kind: 'loading' }, 'r/python · 热门', true)} title="调整 Reddit">
    <BoardOptions subscribedCommunities={['python', 'rust', 'MachineLearning']} view={{ board: 'reddit', variant: 'hot', subreddit: 'python' }} />
  </WithSheet>
));

// --- 订阅 -----------------------------------------------------------------------

const subFolders = subscriptions.map((folder) => ({ key: folder.name, ...folder }));
const subs = (props: Partial<Parameters<typeof SubscriptionsView>[0]> = {}) => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView state={{ kind: 'ready', folders: subFolders }} user={user} {...props} />
  </TabFrame>
);
export const Subscriptions = story('订阅 · 正常（含有问题的订阅）', () => subs());
export const SubscriptionsFolder = story('订阅 · 打开分类', () => subs({ openFolder: 'AI' }));
export const SubscriptionsExpanded = story('订阅 · 展开问题说明', () => subs({ expandedId: 's2', openFolder: 'AI' }));
export const SubscriptionsSearch = story('订阅 · 搜索', () => subs({ query: 'reddit' }));
export const SubscriptionsNoMatch = story('订阅 · 搜索无结果', () => subs({ query: 'zzz' }));
export const SubscriptionsEmpty = story('订阅 · 还没有订阅', () => subs({ state: { kind: 'ready', folders: [] } }));
export const SubscriptionsAdd = story('订阅 · 添加弹层（网站）', () => (
  <WithSheet page={subs()} title="添加订阅">
    <AddSubscriptionForm folders={folders} initial={{ folder: 'AI', type: 'website', value: 'https://simonwillison.net' }} />
  </WithSheet>
));
export const SubscriptionsAddError = story('订阅 · 添加失败', () => (
  <WithSheet page={subs()} title="添加订阅">
    <AddSubscriptionForm error="这个网站没有找到 RSS / Atom。可以试试直接填它的 RSS 地址。" folders={folders} initial={{ type: 'website', value: 'https://example.com' }} />
  </WithSheet>
));
export const SubscriptionsAdding = story('订阅 · 添加中', () => (
  <WithSheet page={subs()} title="添加订阅">
    <AddSubscriptionForm folders={folders} initial={{ type: 'youtube', value: '@TwoMinutePapers' }} submitting />
  </WithSheet>
));
export const SubscriptionsMenu = story('订阅 · 更多操作', () => (
  <WithSheet page={subs({ openFolder: 'AI' })} title="Simon Willison">
    <SubscriptionMenu folder="AI" folders={folders} includeInHome name="Simon Willison" />
  </WithSheet>
));

// --- 收藏 -----------------------------------------------------------------------

const savedFolders = folders.map((name) => ({ key: name, label: name }));
const savedList = (items: typeof feed, heading = '最近收藏', empty = <EmptyState description="点卡片右下角的书签即可收藏。" title="还没有收藏" />) => (
  <FeedList empty={empty} heading={heading} state={{ kind: 'ready', items, hasMore: false, loadingMore: false }} />
);
export const Saved = story('收藏 · 正常', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={savedFolders}>
      {savedList(feed.filter((item) => item.isSaved).concat(feed.slice(0, 2).map((item) => ({ ...item, id: `${item.id}-s`, isSaved: true }))))}
    </SavedView>
  </TabFrame>
));
export const SavedFolder = story('收藏 · 选了分类', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder="AI" folders={savedFolders}>
      {savedList(feed.filter((item) => item.isSaved))}
    </SavedView>
  </TabFrame>
));
export const SavedEmpty = story('收藏 · 还没有收藏', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={savedFolders}>
      {savedList([])}
    </SavedView>
  </TabFrame>
));
export const SavedNoMatch = story('收藏 · 搜索无结果', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={savedFolders} query="量子">
      {savedList([], '搜索结果', <EmptyState title="没有匹配的收藏" />)}
    </SavedView>
  </TabFrame>
));

// --- 我的 -----------------------------------------------------------------------

export const Me = story('我的', () => (
  <TabFrame tab="me">
    <MeView email="me@example.com" interfaceLanguage="简体中文" server="reader.example.com" translation={{ kind: 'ready', enabled: true, language: '简体中文', engine: 'DeepSeek · deepseek-flash' }} user={user} />
  </TabFrame>
));

// --- 阅读页 ---------------------------------------------------------------------

const articleBody = (bilingual: boolean) => (
  <ScrollView contentContainerClassName="gap-4 px-6 pb-12 pt-6">
    <Text level="h1">{article.title}</Text>
    {article.paragraphs.map((paragraph, index) => (
      <View className="gap-2" key={index}>
        <Text level="body">{paragraph.original}</Text>
        {bilingual ? (
          <Text level="body" tone="secondary">
            {paragraph.translated}
          </Text>
        ) : null}
      </View>
    ))}
  </ScrollView>
);
const readerTools = (reader: boolean, bilingual: boolean): ReaderTool[] => [
  { key: 'translate', icon: IconLanguage, label: '双语对照', on: bilingual },
  { key: 'reader', icon: IconArticle, label: '阅读模式', on: reader },
];

export const ArticleWeb = story('阅读页 · 原网页（加载中）', () => (
  <ArticleView progress={0.4} saved={false} tools={readerTools(false, false)}>
    {articleBody(false)}
  </ArticleView>
));
export const ArticleBilingual = story('阅读页 · 阅读模式 + 双语', () => (
  <ArticleView saved tools={readerTools(true, true)}>
    {articleBody(true)}
  </ArticleView>
));
export const ArticleFailed = story('阅读页 · 网页没打开', () => (
  <ArticleView problem={{ message: '页面没打开：网络问题', action: <Button title="重试" variant="ghost" /> }} saved={false} tools={readerTools(false, false)}>
    <View />
  </ArticleView>
));
export const ArticleVideo = story('阅读页 · 视频', () => (
  <ArticleView saved={false} title="视频" tools={[{ key: 'player', icon: IconWorld, label: '打开视频网页', on: false }]}>
    <View className="aspect-video w-full bg-sunken" />
  </ArticleView>
));

// --- 连接与登录 -----------------------------------------------------------------

const connectBase = { address: '', token: '', status: 'editing' as const };
const signInBase = { mode: 'sign-in' as const, email: 'me@example.com', password: '', displayName: '', submitting: false, serverLabel: 'reader.example.com' };

export const Connect = story('连接 · 输入地址', () => <ConnectView {...connectBase} />);
export const ConnectChecking = story('连接 · 连接中', () => <ConnectView {...connectBase} address="reader.example.com" status="checking" token="secret" />);
export const ConnectConfirm = story('连接 · 确认服务器', () => (
  <ConnectView {...connectBase} address="reader.example.com" server={{ serverId: 'home', base: 'https://reader.example.com', apiBaseUrl: 'https://reader.example.com' }} status="confirming" token="secret" />
));
export const ConnectFailed = story('连接 · 连不上', () => (
  <ConnectView {...connectBase} address="https://reader.example" addressError="连不上这个地址。检查地址是否正确、服务器是否在运行，以及手机能否访问它。" token="secret" />
));
export const ConnectTokenRejected = story('连接 · 口令已更换', () => (
  <ConnectView {...connectBase} address="https://reader.example.com" notice="服务器口令已更换，请输入新的口令。" />
));
export const SignIn = story('登录', () => <SignInView {...signInBase} />);
export const SignInWrong = story('登录 · 密码错误', () => <SignInView {...signInBase} passwordError="邮箱或密码不对。" />);
export const SignInLimited = story('登录 · 尝试过多', () => <SignInView {...signInBase} formError="操作太频繁，约 42 秒后再试。" password="secret" />);
export const SignInExpired = story('登录 · 登录已过期', () => <SignInView {...signInBase} expired />);
export const Register = story('注册', () => <SignInView {...signInBase} mode="register" />);
