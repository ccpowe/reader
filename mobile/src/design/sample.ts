/**
 * Example data for the layout exploration (design step 4). Lengths follow SLICE.md:
 * short / typical / very long titles, with and without images, X posts with quotes and threads.
 * Field names follow the backend contract where one exists (FeedItem, BoardEntry, Subscription).
 */

export type FeedCardData = {
  id: string;
  source: { name: string; type: 'rss' | 'youtube' | 'x' | 'reddit' };
  seenAgo: string;
  title: string;
  summary: string | null;
  imageUrl: string | null;
  isSaved: boolean;
  x?: {
    author: { name: string; handle: string };
    text: string;
    replyTo?: string;
    quote?: { author: string; handle: string; text: string };
    isRepost?: boolean;
    threadCount?: number;
  };
};

const image = (seed: string) => `https://picsum.photos/seed/${seed}/400/400`;

export const feedItems: FeedCardData[] = [
  {
    id: 'a1',
    source: { name: 'Simon Willison', type: 'rss' },
    seenAgo: '12 分钟前',
    title: '把模型评测做成每天跑的测试',
    summary:
      '与其在发布前一次性评测，不如把关键用例写成每天都跑的测试：失败时能立刻知道是哪个改动引起的，也能看到能力随时间的变化。',
    imageUrl: null,
    isSaved: false,
  },
  {
    id: 'a2',
    source: { name: 'The Verge', type: 'rss' },
    seenAgo: '1 小时前',
    title: 'OpenAI 发布更快的开放权重推理模型，在多个编程基准上接近闭源旗舰，同时把推理成本降到原来的三分之一',
    summary: '新模型采用混合专家结构，激活参数更少；官方称在长上下文任务上也有明显提升。',
    imageUrl: image('verge'),
    isSaved: true,
  },
  {
    id: 'a3',
    source: { name: '一个名字特别长的独立开发者技术博客（每周更新）', type: 'rss' },
    seenAgo: '3 小时前',
    title:
      '我们是如何把一个十年历史的单体应用拆成可以独立部署的模块的：从依赖梳理、数据库所有权划分、灰度发布到回滚预案，一年里踩过的坑和最后留下来的几条规则',
    summary:
      '这篇文章记录了我们拆分单体应用的完整过程。最难的不是写代码，而是让每个团队对"谁拥有哪些数据"达成一致。我们先画出了所有模块之间的依赖，再按数据所有权划分边界，最后用灰度发布一点点切流量。中间有三次大的回滚，每一次都让我们改进了发布流程。最后留下来的几条规则，比任何框架选型都重要。',
    imageUrl: image('monolith'),
    isSaved: false,
  },
  {
    id: 'x1',
    source: { name: 'Andrej Karpathy', type: 'x' },
    seenAgo: '25 分钟前',
    title: '短帖',
    summary: null,
    imageUrl: null,
    isSaved: false,
    x: {
      author: { name: 'Andrej Karpathy', handle: 'karpathy' },
      text: '读论文最好的方法：先只看图和表，猜作者想证明什么，再读正文验证你的猜测。',
    },
  },
  {
    id: 'x2',
    source: { name: 'Simon Willison', type: 'x' },
    seenAgo: '2 小时前',
    title: '带引用',
    summary: null,
    imageUrl: null,
    isSaved: true,
    x: {
      author: { name: 'Simon Willison', handle: 'simonw' },
      text: '这个思路很好，我在自己的工具里也这样做了：每个评测用例都附上一句"为什么这个用例重要"。',
      quote: {
        author: 'Hamel Husain',
        handle: 'HamelHusain',
        text: '评测集最重要的不是数量，而是每个用例都对应一个你真正在乎的失败。',
      },
    },
  },
  {
    id: 'x3',
    source: { name: 'Swyx', type: 'x' },
    seenAgo: '5 小时前',
    title: '自回复串',
    summary: null,
    imageUrl: image('thread'),
    isSaved: false,
    x: {
      author: { name: 'swyx', handle: 'swyx' },
      text:
        '花了一周时间整理今年所有 AI 编程工具的发布记录，有几个趋势很明显。1/ 所有工具都在从"补全"转向"代理"：不再是帮你写下一行，而是帮你完成一个任务。2/ 评测从基准分数转向真实仓库里的任务完成率。',
      threadCount: 7,
    },
  },
];

export type BoardEntryData = {
  rank: number;
  title: string;
  description?: string;
  meta: string[];
  imageUrl?: string;
  isSaved: boolean;
  tags?: string[];
};

export type BoardData = {
  key: 'hn' | 'github' | 'product_hunt' | 'reddit' | 'techtwitter';
  label: string;
  variants: { key: string; label: string }[];
  selectedVariant: string;
  entries: BoardEntryData[];
};

export const boards: BoardData[] = [
  {
    key: 'hn',
    label: 'Hacker News',
    variants: [],
    selectedVariant: 'top',
    entries: [
      { rank: 1, title: 'Protecting SSH keys with the macOS Secure Enclave', meta: ['github.com', '412 分', '128 评论'], isSaved: false },
      { rank: 2, title: 'Show HN: A tiny database that fits in a single file and survives power loss', meta: ['sqlite.org', '298 分', '76 评论'], isSaved: true },
      { rank: 3, title: 'Why your build is slow', meta: ['jvns.ca', '201 分', '45 评论'], isSaved: false },
    ],
  },
  {
    key: 'github',
    label: 'GitHub',
    variants: [
      { key: 'daily', label: '日' },
      { key: 'weekly', label: '周' },
      { key: 'monthly', label: '月' },
    ],
    selectedVariant: 'weekly',
    entries: [
      { rank: 1, title: 'NVIDIA/auto-ontology', description: 'Build knowledge graphs from documents with LLMs.', meta: ['Python', '★ 4,210', '本周 +1,832'], isSaved: false },
      { rank: 2, title: 'astral-sh/ty', description: 'An extremely fast Python type checker, written in Rust.', meta: ['Rust', '★ 12,004', '本周 +960'], isSaved: false },
    ],
  },
  {
    key: 'product_hunt',
    label: 'Product Hunt',
    variants: [
      { key: 'today', label: '今天' },
      { key: 'yesterday', label: '昨天' },
    ],
    selectedVariant: 'today',
    entries: [
      { rank: 1, title: 'Claude Sheets', description: 'Spreadsheets you can talk to', meta: ['▲ 812'], imageUrl: image('ph1'), isSaved: false },
      { rank: 2, title: 'NovaAtom Lite', description: 'A smaller, faster on-device model for notes', meta: ['▲ 503'], imageUrl: image('ph2'), isSaved: false },
    ],
  },
  {
    key: 'reddit',
    label: 'Reddit',
    variants: [
      { key: 'hot', label: '热门' },
      { key: 'rising', label: '上升' },
      { key: 'top-week', label: 'Top · 周' },
    ],
    selectedVariant: 'hot',
    entries: [
      { rank: 1, title: 'Python 3.15 will ship with a JIT enabled by default', meta: ['r/python', 'u/someone'], imageUrl: image('reddit1'), isSaved: false },
      { rank: 2, title: 'What is your favourite underrated standard library module?', meta: ['r/python', 'u/curious'], isSaved: false },
    ],
  },
  {
    key: 'techtwitter',
    label: 'TechTwitter',
    variants: [],
    selectedVariant: 'trending',
    entries: [
      {
        rank: 1,
        title: 'Claude Adds Whiteboards, Sheets and Data Tools',
        description: 'Claude is rolling out Whiteboards, Sheets and Data, bringing planning, tracking and data storytelling into one workspace.',
        meta: ['质量 8'],
        tags: ['AI', 'productivity'],
        isSaved: false,
      },
    ],
  },
];

export type BoardStatus =
  | { kind: 'fresh'; age: string }
  | { kind: 'stale'; age: string }
  | { kind: 'paused'; reason: string; resumesIn: string }
  | { kind: 'unavailable'; reason: string };

export const boardStatuses: BoardStatus[] = [
  { kind: 'fresh', age: '2 分钟前' },
  { kind: 'stale', age: '48 分钟前' },
  { kind: 'paused', reason: '被限流', resumesIn: '约 8 分钟' },
  { kind: 'unavailable', reason: '需要浏览器验证' },
];

export type SubscriptionData = {
  id: string;
  name: string;
  type: 'rss' | 'youtube' | 'x' | 'reddit';
  newCount: number;
  health:
    | { kind: 'ok' }
    | { kind: 'failing'; reason: string; since: string }
    | { kind: 'gap'; detail: string };
};

export const subscriptionFolders: { name: string; items: SubscriptionData[] }[] = [
  {
    name: 'AI',
    items: [
      { id: 's1', name: 'Simon Willison', type: 'rss', newCount: 3, health: { kind: 'ok' } },
      { id: 's2', name: 'Andrej Karpathy', type: 'x', newCount: 1, health: { kind: 'failing', reason: 'X 服务没有配置', since: '3 小时前' } },
      { id: 's3', name: 'Two Minute Papers', type: 'youtube', newCount: 0, health: { kind: 'ok' } },
      { id: 's4', name: 'swyx', type: 'x', newCount: 12, health: { kind: 'gap', detail: '两次扫描之间更新超过 120 条，更早的没有补上' } },
    ],
  },
  {
    name: '新闻',
    items: [
      { id: 's5', name: 'The Verge', type: 'rss', newCount: 8, health: { kind: 'ok' } },
      { id: 's6', name: '一个名字特别长的独立开发者技术博客（每周更新）', type: 'rss', newCount: 0, health: { kind: 'failing', reason: '网站连不上', since: '昨天' } },
      { id: 's7', name: 'Hacker News Daily', type: 'rss', newCount: 2, health: { kind: 'ok' } },
    ],
  },
  {
    name: '编程',
    items: [
      { id: 's8', name: 'r/python', type: 'reddit', newCount: 5, health: { kind: 'ok' } },
      { id: 's9', name: 'Julia Evans', type: 'rss', newCount: 0, health: { kind: 'ok' } },
      { id: 's10', name: 'Fireship', type: 'youtube', newCount: 1, health: { kind: 'ok' } },
      { id: 's11', name: 'Rust Blog', type: 'rss', newCount: 0, health: { kind: 'ok' } },
      { id: 's12', name: 'jvns', type: 'x', newCount: 0, health: { kind: 'ok' } },
    ],
  },
];
