/**
 * Design step 5: every main view with quietpaper applied, layouts from P23 (R1-A R2-A R3-A R4-B),
 * light theme (dark is a draft until step 6). Story names: "<area> · <state>".
 */
import type { Meta, StoryObj } from '@storybook/react-native';

import { ArticleView } from '@/screens/ArticleView';
import { ConnectView, SignInView } from '@/screens/AuthViews';
import { BoardItemView } from '@/screens/BoardItemView';
import { BoardOptions, BoardsView } from '@/screens/BoardsView';
import { FeedList, type FeedListState } from '@/components/FeedList';
import { ChannelPicker } from '@/screens/ChannelPicker';
import { EmptyState } from '@/ui';
import { MeView } from '@/screens/MeView';
import { SavedView } from '@/screens/SavedView';
import { AddSubscriptionForm, SubscriptionsView } from '@/screens/SubscriptionsView';
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

export const BoardsGithub = story('榜单 · GitHub 周榜', () => (
  <TabFrame tab="boards">
    <BoardsView board="github" state={{ kind: 'ready', entries: boardEntries.github, freshness: { kind: 'fresh', age: '2 分钟前' } }} user={user} variantLabel="周榜" />
  </TabFrame>
));
export const BoardsHn = story('榜单 · Hacker News', () => (
  <TabFrame tab="boards">
    <BoardsView user={user} board="hn" state={{ kind: 'ready', entries: boardEntries.hn, freshness: { kind: 'fresh', age: '刚刚' } }} />
  </TabFrame>
));
export const BoardsPh = story('榜单 · Product Hunt 今天', () => (
  <TabFrame tab="boards">
    <BoardsView board="product_hunt" state={{ kind: 'ready', entries: boardEntries.product_hunt, freshness: { kind: 'fresh', age: '12 分钟前' } }} user={user} variantLabel="今天" />
  </TabFrame>
));
export const BoardsRedditPaused = story('榜单 · Reddit 暂停中', () => (
  <TabFrame tab="boards">
    <BoardsView board="reddit" state={{ kind: 'ready', entries: boardEntries.reddit, freshness: { kind: 'paused', reason: '被限流', resumesIn: '约 8 分钟' } }} user={user} variantLabel="r / codex · 热门" />
  </TabFrame>
));
export const BoardsStale = story('榜单 · 已过期（刷新失败）', () => (
  <TabFrame tab="boards">
    <BoardsView user={user} board="hn" state={{ kind: 'ready', entries: boardEntries.hn, freshness: { kind: 'stale', age: '48 分钟前' } }} />
  </TabFrame>
));
export const BoardsTechTwitter = story('榜单 · TechTwitter 暂不可用', () => (
  <TabFrame tab="boards">
    <BoardsView user={user} board="techtwitter" state={{ kind: 'unavailable', reason: '对方开启了浏览器验证，服务器暂时抓不到' }} />
  </TabFrame>
));
export const BoardsLoading = story('榜单 · 加载中', () => (
  <TabFrame tab="boards">
    <BoardsView user={user} board="github" state={{ kind: 'loading' }} />
  </TabFrame>
));
export const BoardsOptionsGithub = story('榜单 · 调整弹层（GitHub）', () => (
  <WithSheet page={<TabFrame tab="boards"><BoardsView user={user} board="github" state={{ kind: 'loading' }} /></TabFrame>} title="调整 GitHub">
    <BoardOptions board="github" variant="weekly" />
  </WithSheet>
));
export const BoardsOptionsPh = story('榜单 · 调整弹层（Product Hunt）', () => (
  <WithSheet page={<TabFrame tab="boards"><BoardsView user={user} board="product_hunt" state={{ kind: 'loading' }} /></TabFrame>} title="调整 Product Hunt">
    <BoardOptions board="product_hunt" variant="today" />
  </WithSheet>
));
export const BoardsOptionsReddit = story('榜单 · 调整弹层（Reddit）', () => (
  <WithSheet page={<TabFrame tab="boards"><BoardsView user={user} board="reddit" state={{ kind: 'loading' }} /></TabFrame>} title="调整 Reddit">
    <BoardOptions board="reddit" subreddit="python" subscribedCommunities={['python', 'rust', 'MachineLearning']} variant="hot" />
  </WithSheet>
));

// --- 订阅 -----------------------------------------------------------------------

export const Subscriptions = story('订阅 · 正常（含有问题的订阅）', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} folders={subscriptions} />
  </TabFrame>
));
export const SubscriptionsFolder = story('订阅 · 打开分类', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} folders={subscriptions} openFolder="AI" />
  </TabFrame>
));
export const SubscriptionsExpanded = story('订阅 · 展开问题说明', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} expandedId="s2" folders={subscriptions} openFolder="AI" />
  </TabFrame>
));
export const SubscriptionsSearch = story('订阅 · 搜索', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} folders={subscriptions} query="reddit" />
  </TabFrame>
));
export const SubscriptionsNoMatch = story('订阅 · 搜索无结果', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} folders={subscriptions} query="zzz" />
  </TabFrame>
));
export const SubscriptionsEmpty = story('订阅 · 还没有订阅', () => (
  <TabFrame tab="subscriptions">
    <SubscriptionsView user={user} folders={[]} />
  </TabFrame>
));
export const SubscriptionsAdd = story('订阅 · 添加弹层（网站）', () => (
  <WithSheet page={<TabFrame tab="subscriptions"><SubscriptionsView user={user} folders={subscriptions} /></TabFrame>} title="添加订阅">
    <AddSubscriptionForm folder="AI" folders={folders} type="website" value="https://simonwillison.net" />
  </WithSheet>
));
export const SubscriptionsAddError = story('订阅 · 添加失败', () => (
  <WithSheet page={<TabFrame tab="subscriptions"><SubscriptionsView user={user} folders={subscriptions} /></TabFrame>} title="添加订阅">
    <AddSubscriptionForm error="这个网站没有找到 RSS / Atom。可以试试直接填它的 RSS 地址。" folder={null} folders={folders} type="website" value="https://example.com" />
  </WithSheet>
));
export const SubscriptionsAdding = story('订阅 · 添加中', () => (
  <WithSheet page={<TabFrame tab="subscriptions"><SubscriptionsView user={user} folders={subscriptions} /></TabFrame>} title="添加订阅">
    <AddSubscriptionForm folder={null} folders={folders} submitting type="youtube" value="@TwoMinutePapers" />
  </WithSheet>
));

// --- 收藏 -----------------------------------------------------------------------

export const Saved = story('收藏 · 正常', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={folders} items={feed.filter((item) => item.isSaved).concat(feed.slice(0, 2).map((item) => ({ ...item, id: `${item.id}-s`, isSaved: true })))} />
  </TabFrame>
));
export const SavedFolder = story('收藏 · 选了分类', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder="AI" folders={folders} items={feed.filter((item) => item.isSaved)} />
  </TabFrame>
));
export const SavedEmpty = story('收藏 · 还没有收藏', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={folders} items={[]} />
  </TabFrame>
));
export const SavedNoMatch = story('收藏 · 搜索无结果', () => (
  <TabFrame tab="saved">
    <SavedView user={user} folder={null} folders={folders} items={[]} query="量子" />
  </TabFrame>
));

// --- 我的 -----------------------------------------------------------------------

export const Me = story('我的', () => (
  <TabFrame tab="me">
    <MeView email="me@example.com" engine="DeepSeek · deepseek-flash" interfaceLanguage="简体中文" server="reader.example.com" targetLanguage="简体中文" translationEnabled user={user} />
  </TabFrame>
));

// --- 阅读页 ---------------------------------------------------------------------

export const ArticleWeb = story('阅读页 · 原网页', () => <ArticleView {...article} bilingual={false} reader={false} saved={false} />);
export const ArticleBilingual = story('阅读页 · 阅读模式 + 双语', () => <ArticleView {...article} bilingual reader saved />);
export const ArticleTranslating = story('阅读页 · 翻译中', () => <ArticleView {...article} bilingual busy="translating" reader saved={false} />);
export const ArticleQuota = story('阅读页 · 额度用完', () => <ArticleView {...article} bilingual busy="quota" reader={false} saved={false} />);

// --- 榜单条目 -------------------------------------------------------------------

export const BoardItemHn = story('榜单条目 · Hacker News', () => (
  <BoardItemView board="Hacker News" hasDiscussion meta={['github.com', '412 分', '128 评论']} saved={false} title="Protecting SSH keys with the macOS Secure Enclave" translatedTitle="用 macOS 安全隔区保护 SSH 密钥" />
));
export const BoardItemTechTwitter = story('榜单条目 · TechTwitter', () => (
  <BoardItemView
    board="TechTwitter"
    description="Claude is rolling out Whiteboards, Sheets and Data, which bring planning, tracking and data storytelling into the same AI workspace."
    hasDiscussion={false}
    meta={['质量 8', '11 月 10 日']}
    saved
    tags={['AI', 'productivity', 'data visualization']}
    title="Claude Adds Whiteboards, Sheets and Data Tools"
  />
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
