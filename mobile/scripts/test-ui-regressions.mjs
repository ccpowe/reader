import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const [profile, sources, addSourceSheet, saved, reader, auth, api, startTunnel, connectionForm, redirectConfirm, connectionErrors, authLayout, authMessages] = await Promise.all([
  readFile(new URL('../screens/ProfileScreen.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../screens/SourcesScreen.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../components/AddSourceSheet.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../screens/SavedScreen.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../screens/ArticleReaderScreen.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../screens/EmailAuthScreen.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../lib/api.ts', import.meta.url), 'utf8'),
  readFile(new URL('./start-tunnel.sh', import.meta.url), 'utf8'),
  readFile(new URL('../components/ServerConnectionForm.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../components/RedirectConfirm.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../components/ConnectionErrorView.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../components/AuthLayout.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../i18n/messages/auth.ts', import.meta.url), 'utf8'),
]);

assert.match(profile, /updateTranslationPreference\(session, \{ isEnabled: value \}, context\.runtime\)/);
assert.match(profile, /<Switch[\s\S]*value=\{translation\.enabled\}/);
assert.doesNotMatch(profile, /targetLocale, isEnabled: true/);

assert.match(addSourceSheet, /setErrorMessage\(error instanceof Error/);
assert.match(addSourceSheet, /<BottomSheetModal[\s\S]*\{errorMessage \?/);
assert.match(sources, /<AddSourceSheet[\s\S]*onClose=\{\(\) => setShowAddSheet\(false\)\}/);

assert.match(saved, /useSavedContent\(session, active, debouncedQuery\)/);
assert.match(saved, /savedQuery\.hasNextPage/);
assert.match(api, /params\.set\('q', options\.query\.trim\(\)\)/);

assert.match(reader, /article\?\.external_url \?\? item\.external_url/);
assert.match(reader, /setLoadAttempt\(\(current\) => current \+ 1\)/);
assert.match(auth, /client\.auth\.signUp/);
assert.match(authLayout, /KeyboardAvoidingView/);
assert.match(auth, /accessibilityLiveRegion="polite"/);
assert.doesNotMatch(auth, /Alert\.alert/, 'auth feedback should stay inline without a duplicate native alert');
assert.match(auth, /styles\.errorFeedback/);

assert.match(startTunnel, /EXPO_NO_DOTENV=1/);
assert.match(startTunnel, /PASEO_PORT/);
assert.match(startTunnel, /Connection coordinates are entered and discovered inside the app/);
assert.doesNotMatch(startTunnel, /EXPO_PUBLIC_(?:API_BASE_URL|SUPABASE_URL|SUPABASE_PUBLISHABLE_KEY)/);
assert.doesNotMatch(api, /EXPO_PUBLIC_API_BASE_URL/);
assert.match(connectionForm, /useTranslation\('auth'\)/);
assert.match(connectionForm, /accessibilityLabel=\{t\('serverAddressAccessibility'\)\}/);
assert.match(authMessages, /serverAddressAccessibility:\s*'Reader 服务器地址'/);
assert.match(authMessages, /serverAddressAccessibility:\s*'Reader server address'/);
assert.match(connectionForm, /accessibilityState=\{\{ busy/);
assert.match(redirectConfirm, /useTranslation\('auth'\)/);
assert.match(redirectConfirm, /onPress=\{\(\) => void onConfirm\(\)\}[\s\S]*?t\('trustAndConnect'\)/);
assert.match(authMessages, /trustAndConnect:\s*'信任并连接'/);
assert.match(authMessages, /trustAndConnect:\s*'Trust and connect'/);
assert.match(connectionErrors, /accessibilityLiveRegion="polite"/);

console.log('UI regression contracts passed.');
