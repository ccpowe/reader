import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const webViewAndroidSource = join(
  process.cwd(),
  'node_modules/react-native-webview/android/src/main/java/com/reactnativecommunity/webview',
);
const managerSource = readFileSync(
  join(webViewAndroidSource, 'RNCWebViewManagerImpl.kt'),
  'utf8',
);
const webViewSource = readFileSync(
  join(webViewAndroidSource, 'RNCWebView.java'),
  'utf8',
);
const chromeClientSource = readFileSync(
  join(webViewAndroidSource, 'RNCWebChromeClient.java'),
  'utf8',
);

assert.doesNotMatch(
  managerSource,
  /FLAG_LAYOUT_NO_LIMITS/,
  'Android fullscreen video must not extend the activity window outside the physical display.',
);
assert.match(
  managerSource,
  /rootView\.addView\(mVideoView, FULLSCREEN_LAYOUT_PARAMS\)/,
  'Android fullscreen video must still use the dedicated match-parent layout.',
);
assert.match(
  chromeClientSource,
  /MATCH_PARENT, ViewGroup\.LayoutParams\.MATCH_PARENT, Gravity\.CENTER/,
  'Android fullscreen video must remain centered inside its fullscreen container.',
);
assert.match(
  webViewSource,
  /WindowInsetsCompat\.Type\.systemBars\(\)\s*\|\s*WindowInsetsCompat\.Type\.displayCutout\(\)/,
  'The WebView must identify native system-bar and display-cutout insets.',
);
assert.match(
  webViewSource,
  /\.setInsets\(webContentInsetTypes, Insets\.NONE\)/,
  'Native insets already handled by the app must be zeroed before reaching web content.',
);
assert.match(
  webViewSource,
  /super\.onApplyWindowInsets\(adjustedPlatformInsets != null \? adjustedPlatformInsets : insets\)/,
  'Zeroed insets must still reach Chromium so stale safe-area values are cleared.',
);
assert.doesNotMatch(
  webViewSource,
  /WindowInsetsCompat\.CONSUMED/,
  'Insets must be zeroed rather than consumed so IME and later inset updates keep propagating.',
);

console.log('Android WebView fullscreen layout guard passed.');
