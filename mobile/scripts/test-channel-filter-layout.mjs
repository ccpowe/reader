import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

function mobileSource(relativePath) {
  return readFileSync(fileURLToPath(new URL(`../${relativePath}`, import.meta.url)), 'utf8');
}

const api = mobileSource('lib/api.ts');
const mainAppShell = mobileSource('components/MainAppShell.tsx');
const chip = mobileSource('components/Chip.tsx');
const folderFilterControls = mobileSource('components/FolderFilterControls.tsx');
const home = mobileSource('screens/HomeScreen.tsx');
const saved = mobileSource('screens/SavedScreen.tsx');
const sources = mobileSource('screens/SourcesScreen.tsx');
// Domain labels now use the real i18n runtime. CJS plus the script's require
// resolves its external packages without an ESM data URL's dynamic-require gap.
const runtimeBundle = buildSync({
  stdin: {
    contents: "export * from './domain/source'; export * from './domain/folders'; export * from './domain/mainNavigation'; export { i18n } from './i18n';",
    loader: 'ts',
    resolveDir: fileURLToPath(new URL('..', import.meta.url)),
    sourcefile: 'channel-filter-test-entry.ts',
  },
  bundle: true,
  format: 'cjs',
  packages: 'external',
  platform: 'node',
  write: false,
}).outputFiles[0].text;
const bundledModule = { exports: {} };
new Function('module', 'exports', 'require', runtimeBundle)(bundledModule, bundledModule.exports, createRequire(import.meta.url));
const { sourceHealthLabel, sourceLocationLabel, sourceSecondaryLabel, sourceFolders, UNCATEGORIZED_FOLDER, createMainNavigationState, reduceMainNavigation, i18n } = bundledModule.exports;
await i18n.changeLanguage('zh-CN');

assert.match(
  api,
  /if \(options\.sourceId\) params\.set\('source_id', options\.sourceId\)/,
  'feed requests must pass the selected source_id to the backend',
);
assert.match(
  api,
  /options\.sourceId && items\.some\(\(item\) => item\.source_id !== options\.sourceId\)/,
  'a legacy backend must not masquerade an unfiltered feed as one channel',
);
assert.match(
  home,
  /<ChannelPickerModal[\s\S]*?selectedSourceId=\{selectedSourceId\}[\s\S]*?visible=\{showChannelPicker\}/,
  'home must expose the searchable channel picker',
);
assert.match(
  home,
  /scope=\{sourceFeedScope\(selectedSource\.source_id\)\}/,
  'the selected channel must receive its own feed scope',
);
assert.match(
  sources,
  /const displayName = sourceDisplayName\(item\)[\s\S]*?accessibilityLabel=\{t\('openChannel', \{ name: displayName \}\)\}[\s\S]*?onPress=\{\(\) => onOpen\(item\)\}/,
  'subscription cards must open their channel feed',
);
assert.match(
  mainAppShell,
  /<SourcesScreen[\s\S]*?onOpenSource=\{openSource\}/,
  'the subscriptions screen must delegate source navigation to the app shell',
);
assert.match(
  mainAppShell,
  /<HomeScreen[\s\S]*?onClearSource=\{closeSourceFeed\}/,
  'the inbox must delegate source closing to the app shell',
);
let navigation = createMainNavigationState();
navigation = reduceMainNavigation(navigation, { tab: 'sources', type: 'select_tab' });
navigation = reduceMainNavigation(navigation, { returnTab: 'sources', sourceId: 'source-a', type: 'open_source' });
assert.equal(navigation.tab, 'inbox', 'opening a subscription routes to its inbox feed');
assert.equal(navigation.selectedInboxSourceId, 'source-a');
navigation = reduceMainNavigation(navigation, { sourceId: 'source-b', type: 'set_source_id' });
navigation = reduceMainNavigation(navigation, { type: 'close_source' });
assert.equal(navigation.tab, 'sources', 'closing a subscription feed returns to subscriptions');
assert.equal(navigation.selectedInboxSourceId, null);
assert.equal(navigation.sourceFeedReturnTab, 'inbox', 'the return origin is consumed after closing');
navigation = reduceMainNavigation(createMainNavigationState(), { tab: 'sources', type: 'select_tab' });
navigation = reduceMainNavigation(navigation, { returnTab: 'sources', sourceId: 'source-a', type: 'open_source' });
navigation = reduceMainNavigation(navigation, { type: 'clear_source_filter' });
navigation = reduceMainNavigation(navigation, { sourceId: 'source-b', type: 'set_source_id' });
navigation = reduceMainNavigation(navigation, { type: 'close_source' });
assert.equal(navigation.tab, 'inbox', 'returning to folder content consumes the subscription origin');
assert.match(
  home,
  /accessibilityLabel=\{t\('clearChannelFilter', \{ name: sourceDisplayName\(selectedSource\) \}\)\}[\s\S]*?onPress=\{\(\) => \{ revealChrome\(\); onClearSource\(\); \}\}/,
  'the selected channel close action must use origin-aware navigation',
);
assert.equal(sourceLocationLabel('https://www.anthropic.com/news/'), 'anthropic.com/news');
assert.equal(sourceLocationLabel('https://anthropic.com/research?tag=agents'), 'anthropic.com/research');
assert.equal(sourceSecondaryLabel({ canonical_url: 'https://anthropic.com/news/', display_name: null, kind: 'web' }), null);
assert.equal(sourceSecondaryLabel({ canonical_url: 'https://vercel.com/blog', display_name: 'Vercel', kind: 'web' }), 'vercel.com/blog');
const sourceHealth = { backlog_cycles: 0, gap_detected: true, status: 'pending', sync_phase: 'degraded' };
const accessFailureLabels = [
  ['web_http_403', '网站拒绝服务器访问（HTTP 403），暂时无法获取更新'],
  ['web_rss_http_403', '网站拒绝服务器访问（HTTP 403），暂时无法获取更新'],
  ['rss_http_403', '网站拒绝服务器访问（HTTP 403），暂时无法获取更新'],
  ['web_challenge_required', '网站要求浏览器验证，服务器暂时无法获取更新'],
  ['web_rss_challenge_required', '网站要求浏览器验证，服务器暂时无法获取更新'],
  ['rss_challenge_required', '网站要求浏览器验证，服务器暂时无法获取更新'],
  ['web_http_401', '网站要求登录或授权（HTTP 401），暂时无法获取更新'],
];
for (const [last_error_code, label] of [
  ...accessFailureLabels,
  ['web_feed_discovering', '正在查找订阅源'],
  ['web_feed_discovery_retry', '订阅源检测暂时失败，稍后重试'],
  ['web_rule_authoring', '正在解析此网站'],
  ['web_rule_repairing', '正在修复网站解析，已有内容仍可阅读'],
  ['web_rule_authoring_failed', '暂时无法解析此网站，已有内容仍可阅读'],
  ['web_rule_agent_unavailable', '网站解析服务暂不可用'],
]) {
  assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code }), label);
  assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code, status: 'paused' }), '同步已暂停');
}
const categorizedSources = [{ folder_name: 'Z' }, { folder_name: null }, { folder_name: '阿' }];
const chineseFolderOrder = sourceFolders(categorizedSources);
const partialSource = { ...sourceHealth, status: 'active', gap_detected: false, last_error_code: 'web_rule_partial_parse' };
assert.equal(sourceHealthLabel(partialSource), '正常更新，部分文章无法解析');
assert.equal(sourceHealthLabel({ ...partialSource, status: 'paused' }), '同步已暂停');
assert.equal(sourceHealthLabel({ ...partialSource, last_error_code: 'web_rule_missing_fields' }), '上游暂时异常，已有内容不受影响', 'a listing with no usable articles must not claim normal updates');
assert.equal(sourceHealthLabel({ ...partialSource, gap_detected: true }), sourceHealthLabel({ ...sourceHealth, status: 'active' }), 'history gaps retain priority over partial parsing');
assert.deepEqual(chineseFolderOrder, ['阿', 'Z', UNCATEGORIZED_FOLDER]);
await i18n.changeLanguage('en');
assert.equal(sourceHealthLabel(partialSource), 'Updating normally; some articles could not be parsed');
for (const last_error_code of ['web_http_403', 'web_rss_http_403', 'rss_http_403']) {
  assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code }), 'The website denied server access (HTTP 403); updates are unavailable for now');
}
assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code: 'web_challenge_required' }), 'The website requires browser verification; the server cannot fetch updates for now');
assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code: 'web_http_401' }), 'The website requires sign-in or authorization (HTTP 401); updates are unavailable for now');
assert.deepEqual(sourceFolders(categorizedSources), chineseFolderOrder, 'switching interface language must preserve category indices and mounted feed identity');
assert.equal(sourceHealthLabel({ ...sourceHealth, last_error_code: 'web_feed_discovering' }), 'Finding a feed');
assert.equal(sourceHealthLabel({ ...sourceHealth, status: 'paused' }), 'Sync paused', 'status helpers resolve the current interface language at call time');
await i18n.changeLanguage('zh-CN');
assert.match(sources, /sourceSecondaryLabel\(item\)/, 'subscription rows must expose a non-duplicated canonical path');
assert.match(
  home,
  /HOME_FILTER_BAR_HEIGHT = 46[\s\S]*HOME_CHROME_HEIGHT = PAGE_HEADER_HEIGHT \+ HOME_FILTER_BAR_HEIGHT[\s\S]*filterBar:\s*\{[^}]*height:\s*HOME_FILTER_BAR_HEIGHT[\s\S]*feedSectionHeader:\s*\{[^}]*marginTop:\s*8/,
  'the feed heading must stay visually connected to the category filters',
);
assert.match(
  chip,
  /backgroundColor:\s*colors\.textStrong,[\s\S]*?opacity:\s*activation\.value/,
  'category tabs must use a restrained animated black selection marker',
);
assert.match(
  chip,
  /chipBackground:\s*\{[^}]*bottom:\s*0[^}]*height:\s*3/,
  'the category selection marker must remain a compact underline',
);
assert.match(
  saved,
  /<FolderFilterControls[\s\S]*?searchAccessibilityLabel=\{t\('searchSaved'\)\}/,
  'saved must use the shared folder filter controls',
);
assert.match(
  sources,
  /<SearchField[^>]*label=\{t\('searchSources'\)\}/,
  'subscriptions must retain global source search above category cards',
);
for (const [key, zhCN, en] of [
  ['openChannel', '查看Example频道内容', 'View content from Example'],
  ['clearChannelFilter', '清除Example频道筛选', 'Clear the Example channel filter'],
  ['searchSaved', '搜索收藏内容', 'Search saved items'],
  ['searchSources', '搜索订阅源', 'Search subscriptions'],
]) {
  assert.equal(i18n.t(`feed:${key}`, { lng: 'zh-CN', name: 'Example' }), zhCN);
  assert.equal(i18n.t(`feed:${key}`, { lng: 'en', name: 'Example' }), en);
}
assert.doesNotMatch(
  saved,
  /function SavedTab|savedTabActive/,
  'saved must not keep a second underline-based category implementation',
);
assert.match(
  folderFilterControls,
  /FOLDER_FILTER_CONTROLS_HEIGHT = 110[\s\S]*FOLDER_FILTER_CONTENT_GAP = 8/,
  'folder filters must leave about 12px from the visible chip surface to page content',
);

console.log('channel filtering is connected across the picker, source list, and feed request');
