/**
 * Example data for the step-5 stories, with real lengths (SLICE.md): short / long / very long
 * titles, with and without images, X posts with quotes and threads, subscriptions with problems.
 */
import type { BoardEntryItem } from '@/components/BoardEntry';
import type { FeedCardItem } from '@/components/FeedCard';
import type { SubscriptionItem } from '@/components/SubscriptionRow';

const image = (seed: string) => `https://picsum.photos/seed/${seed}/400/400`;

export const feed: FeedCardItem[] = [
  {
    id: 'a1',
    source: { name: 'Simon Willison', kind: 'rss' },
    seenAgo: '12 分钟前',
    title: '把模型评测做成每天跑的测试',
    summary: '与其在发布前一次性评测，不如把关键用例写成每天都跑的测试：失败时能立刻知道是哪个改动引起的，也能看到能力随时间的变化。',
    imageUrl: null,
    isSaved: false,
  },
  {
    id: 'r1',
    source: { name: 'r/MachineLearning', kind: 'reddit', byline: '/u/council_of_desires' },
    seenAgo: '25 分钟前',
    title: '寻求研究合作伙伴与资金支持：探索生物智能与神经计算 [D]',
    summary: 'Can living neural systems provide new insights into computation, adaptation, and intelligence? We are developing a platform for this.',
    imageUrl: null,
    isSaved: false,
  },
  {
    id: 'x3',
    source: { name: 'swyx', kind: 'x' },
    seenAgo: '40 分钟前',
    title: '',
    summary: null,
    imageUrl: image('thread'),
    isSaved: false,
    x: {
      author: { name: 'swyx', handle: 'swyx' },
      text: '花了一周时间整理今年所有 AI 编程工具的发布记录，有几个趋势很明显。1/ 所有工具都在从"补全"转向"代理"：不再是帮你写下一行，而是帮你完成一个任务。',
      threadCount: 7,
    },
  },
  {
    id: 'a2',
    source: { name: 'The Verge', kind: 'rss' },
    seenAgo: '1 小时前',
    title: 'OpenAI 发布更快的开放权重推理模型，在多个编程基准上接近闭源旗舰，同时把推理成本降到原来的三分之一',
    summary: '新模型采用混合专家结构，激活参数更少；官方称在长上下文任务上也有明显提升。',
    imageUrl: image('verge'),
    isSaved: true,
  },
  {
    id: 'x2',
    source: { name: 'Simon Willison', kind: 'x' },
    seenAgo: '2 小时前',
    title: '',
    summary: null,
    imageUrl: null,
    isSaved: true,
    x: {
      author: { name: 'Simon Willison', handle: 'simonw' },
      text: '这个思路很好，我在自己的工具里也这样做了：每个评测用例都附上一句"为什么这个用例重要"。',
      quote: { author: 'Hamel Husain', handle: 'HamelHusain', text: '评测集最重要的不是数量，而是每个用例都对应一个你真正在乎的失败。' },
    },
  },
  {
    id: 'a3',
    source: { name: '一个名字特别长的独立开发者技术博客（每周更新）', kind: 'web' },
    seenAgo: '3 小时前',
    title: '我们是如何把一个十年历史的单体应用拆成可以独立部署的模块的：从依赖梳理、数据库所有权划分、灰度发布到回滚预案，一年里踩过的坑和最后留下来的几条规则',
    summary:
      '这篇文章记录了我们拆分单体应用的完整过程。最难的不是写代码，而是让每个团队对"谁拥有哪些数据"达成一致。我们先画出了所有模块之间的依赖，再按数据所有权划分边界。',
    imageUrl: image('monolith'),
    isSaved: false,
  },
  {
    id: 'x1',
    source: { name: 'Andrej Karpathy', kind: 'x' },
    seenAgo: '5 小时前',
    title: '',
    summary: null,
    imageUrl: null,
    isSaved: false,
    x: {
      author: { name: 'Andrej Karpathy', handle: 'karpathy' },
      text: '读论文最好的方法：先只看图和表，猜作者想证明什么，再读正文验证你的猜测。',
      repostedBy: 'Simon Willison',
    },
  },
];

export const folders = ['AI', '新闻', '编程'];

export const subscriptions: { name: string; items: SubscriptionItem[] }[] = [
  {
    name: 'AI',
    items: [
      { id: 's1', name: 'Simon Willison', kind: 'rss', address: 'simonwillison.net/atom/everything', newCount: 3, health: { kind: 'ok' }, includeInHome: true },
      { id: 's2', name: 'Andrej Karpathy', kind: 'x', address: '@karpathy', newCount: 1, health: { kind: 'failing', reason: 'X 服务没有配置', since: '3 小时前' }, includeInHome: true },
      { id: 's3', name: 'Two Minute Papers', kind: 'youtube', address: 'youtube.com/@TwoMinutePapers', newCount: 0, health: { kind: 'ok' }, includeInHome: true },
      { id: 's4', name: 'swyx', kind: 'x', address: '@swyx', newCount: 12, health: { kind: 'gap', detail: '两次扫描之间更新超过 120 条' }, includeInHome: true },
      { id: 's11', name: 'OpenAI News', kind: 'rss', address: 'openai.com/news/rss.xml', newCount: 2, health: { kind: 'ok' }, includeInHome: true },
      { id: 's12', name: 'r/MachineLearning', kind: 'reddit', address: 'reddit.com/r/MachineLearning', newCount: 1520, health: { kind: 'ok' }, includeInHome: true },
    ],
  },
  {
    name: '新闻',
    items: [
      { id: 's5', name: 'The Verge', kind: 'rss', address: 'theverge.com/rss/index.xml', newCount: 8, health: { kind: 'ok' }, includeInHome: true },
      { id: 's6', name: '一个名字特别长的独立开发者技术博客（每周更新）', kind: 'web', address: 'example.com/blog/feed.xml', newCount: 0, health: { kind: 'failing', reason: '网站连不上', since: '昨天' }, includeInHome: true },
      { id: 's7', name: 'Hacker News Daily', kind: 'rss', address: 'hn.algolia.com/daily.rss', newCount: 2, health: { kind: 'ok' }, includeInHome: true },
    ],
  },
  {
    name: '项目动态',
    items: [{ id: 's9', name: 'Release notes from claude-code', kind: 'github', address: 'github.com/anthropics/claude-code/releases.atom', newCount: 10, health: { kind: 'ok' }, includeInHome: true }],
  },
  {
    name: '编程',
    items: [
      { id: 's8', name: 'r/python', kind: 'reddit', address: 'reddit.com/r/python', newCount: 5, health: { kind: 'ok' }, includeInHome: true },
      { id: 's10', name: 'Fireship', kind: 'youtube', address: 'youtube.com/@Fireship', newCount: 1, health: { kind: 'ok' }, includeInHome: true },
    ],
  },
];

const people = (...names: string[]) => names.map((name) => ({ name }));

export const boardEntries: Record<'hn' | 'github' | 'product_hunt' | 'reddit' | 'techtwitter', BoardEntryItem[]> = {
  hn: [
    { rank: 1, title: '用 macOS 安全隔区保护 SSH 密钥', byline: 'gatezh.com', score: '260', comments: '93' },
    { rank: 2, title: 'Smart home devices should work without the internet', byline: 'docodethatmatters.com', score: '1.0k', comments: '365' },
    { rank: 3, title: '.name Grandfathered', byline: 'neil.fraser.name', score: '878', comments: '191' },
    { rank: 4, title: 'Show HN: 一个只有单个文件、断电也不会坏的小数据库，以及我们为了让它在各种文件系统上都可靠而做的十几项测试', byline: 'sqlite.org', score: '24', comments: '4' },
  ],
  github: [
    { rank: 1, title: 'NVIDIA/auto-ontology', description: 'Auto Ontology is a Python framework for building semantic, graph-based reasoning layers over data. It enables deterministic query generation.', language: 'Python', stars: '12k', forks: '958', contributors: people('a', 'b', 'c') },
    { rank: 2, title: 'anthropics/skills', description: 'Public repository for Agent Skills', language: 'Python', stars: '193k', forks: '23k', contributors: people('d', 'e', 'f') },
    { rank: 3, title: 'someone/very-long-repository-name-for-testing-overflow', description: 'Computer use CLI for AI agents', language: 'Rust', stars: '22k', forks: '1.6k', contributors: people('g', 'h') },
  ],
  product_hunt: [
    { rank: 1, title: 'Claude Sheets', description: 'Spreadsheets you can talk to', score: '812', comments: '96', imageUrl: image('ph1') },
    { rank: 2, title: 'NovaAtom Lite', description: 'A smaller, faster on-device model for notes', score: '503', comments: '41', imageUrl: image('ph2') },
  ],
  reddit: [
    { rank: 1, title: 'Codex 使用与运行讨论 - 最后更新于 11 月 9 日', description: '请在此处提出您对 Codex 使用限制和模型性能的疑虑、问题与讨论。本聚合帖（Megathread）的目的是集中讨论。', byline: 'u/codex-megathread' },
    { rank: 2, title: '也许很快就要重置了？', description: 'submitted by /u/rubanbhatia [link] [comments]', byline: 'u/rubanbhatia', imageUrl: image('reddit1') },
  ],
  techtwitter: [
    { rank: 1, title: 'Claude 新增白板、表格和数据工具', description: 'Claude is rolling out Whiteboards, Sheets and Data, bringing planning, tracking and data storytelling into one workspace.', tags: ['AI', 'productivity'] },
  ],
};

export const user = { name: 'Lin' };

export const article = {
  host: 'simonwillison.net',
  title: 'Run your evals every day',
  paragraphs: [
    {
      original: 'Evaluations are most useful when they run every day. A failing case then points at the change that caused it, and the history shows how capabilities move over time.',
      translated: '评测每天都跑的时候最有用：失败的用例能直接指向引起它的改动，历史记录也能看出能力随时间的变化。',
    },
    {
      original: 'Keep each case small and attach a one-line reason explaining why it matters. Large suites without reasons decay: nobody remembers which failures are real.',
      translated: '让每个用例保持小巧，并附一句话说明它为什么重要。没有理由的大型评测集会慢慢失效：没人记得哪些失败是真的。',
    },
    {
      original: 'Finally, treat flaky cases as bugs in the evaluation, not in the model. Fix or delete them before they teach everyone to ignore red.',
      translated: '最后，把不稳定的用例当作评测本身的缺陷，而不是模型的。在大家学会忽略红色之前，修好或删掉它们。',
    },
  ],
};
