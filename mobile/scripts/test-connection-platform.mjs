import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { buildSync } from 'esbuild';
import Module, { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
const require = createRequire(import.meta.url);
const [appConfig, envExample, tunnel, appSource, apiSource, storageSource, formSource, authMessagesSource] = await Promise.all([
  readFile(new URL('../app.json', import.meta.url), 'utf8'),
  readFile(new URL('../.env.example', import.meta.url), 'utf8'),
  readFile(new URL('./start-tunnel.sh', import.meta.url), 'utf8'),
  readFile(new URL('../App.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../lib/api.ts', import.meta.url), 'utf8'),
  readFile(new URL('../lib/connection/storage.ts', import.meta.url), 'utf8'),
  readFile(new URL('../components/ServerConnectionForm.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../i18n/messages/auth.ts', import.meta.url), 'utf8'),
]);
const config = JSON.parse(appConfig);
assert.equal(config.expo.ios.infoPlist.NSAppTransportSecurity.NSAllowsArbitraryLoads, true);
const buildProperties = config.expo.plugins.find((plugin) => Array.isArray(plugin) && plugin[0] === 'expo-build-properties');
assert.deepEqual(buildProperties?.[1]?.android?.usesCleartextTraffic, true);
assert.doesNotMatch(envExample, /EXPO_PUBLIC_(?:API_BASE_URL|SUPABASE_URL|SUPABASE_PUBLISHABLE_KEY|SERVER_ACCESS_TOKEN)=/);
assert.doesNotMatch(tunnel, /EXPO_PUBLIC_(?:API_BASE_URL|SUPABASE_URL|SUPABASE_PUBLISHABLE_KEY|SERVER_ACCESS_TOKEN)/);
assert.doesNotMatch(appSource, /process\.env\.EXPO_PUBLIC_/);
assert.doesNotMatch(apiSource, /process\.env\.EXPO_PUBLIC_/);
assert.match(storageSource, /server_id/);
assert.match(storageSource, /api_base_url/);
assert.match(formSource, /useTranslation\('auth'\)/);
assert.match(formSource, /accessibilityLabel=\{t\('serverAddressAccessibility'\)\}/);
assert.match(formSource, /secureTextEntry/);
assert.match(formSource, /accessibilityState=\{\{ busy/);
assert.match(authMessagesSource, /serverAddressAccessibility:\s*'Reader 服务器地址'/);
assert.match(authMessagesSource, /serverAddressAccessibility:\s*'Reader server address'/);

function compile(file, mocks = {}) {
  const filename = join(root, file);
  const mod = new Module(filename);
  mod.filename = filename;
  mod.require = name => Object.hasOwn(mocks, name) ? mocks[name] : require(name);
  mod._compile(buildSync({ entryPoints: [filename], bundle: true, packages: 'external', format: 'cjs', platform: 'node', write: false }).outputFiles[0].text, filename);
  return mod.exports;
}
const values = new Map();
const tabStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: key => { values.delete(key); } };
const originalWindow = globalThis.window;
const originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
globalThis.window = { sessionStorage: tabStorage };
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { ...tabStorage, setItem: () => { throw new Error('Credential leaked to localStorage'); } } });
try {
  const { secretStorage } = compile('lib/connection/secretStorage.ts');
  assert.equal(secretStorage(), tabStorage);
  const { createNamespacedStorage, writeServerToken, readServerToken } = compile('lib/connection/storage.ts');
  const authStorage = createNamespacedStorage({ server_id: 'reader-a', api_base_url: 'https://reader.test' });
  authStorage.setItem('refresh', 'private-refresh');
  writeServerToken('https://reader.test', 'private-entry');
  assert.equal(authStorage.getItem('refresh'), 'private-refresh');
  assert.equal(readServerToken('https://reader.test'), 'private-entry');
  assert.equal(readServerToken('https://other.test'), null);
  const secondTabValues = new Map();
  globalThis.window.sessionStorage = { getItem: key => secondTabValues.get(key) ?? null, setItem: (key, value) => { secondTabValues.set(key, value); }, removeItem: key => { secondTabValues.delete(key); } };
  assert.equal(readServerToken('https://reader.test'), null, 'web credentials are not retained across tab sessions');
} finally {
  if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow;
  if (originalLocalStorage) Object.defineProperty(globalThis, 'localStorage', originalLocalStorage); else delete globalThis.localStorage;
}

const secureValues = new Map();
const secureStore = {
  getItem: key => secureValues.get(key) ?? null,
  setItem: (key, value) => { assert.match(key, /^[\w.-]+$/); secureValues.set(key, value); },
};
const nativeStorage = compile('lib/connection/secretStorage.native.ts', { 'expo-secure-store': secureStore }).secretStorage();
nativeStorage.setItem('token:https://reader.test/%雪', 'one');
nativeStorage.setItem('token:https://reader.test/_25雪', 'two');
assert.equal(secureValues.size, 2, 'native key encoding has no percent/underscore collision');
assert.equal(nativeStorage.getItem('token:https://reader.test/%雪'), 'one');
nativeStorage.removeItem('token:https://reader.test/%雪');
nativeStorage.setItem('token:https://reader.test/%雪', 'new-login');
assert.equal(nativeStorage.getItem('token:https://reader.test/%雪'), 'new-login', 'a prior logout cannot asynchronously erase the next login');
assert.equal(nativeStorage.getItem('token:https://reader.test/_25雪'), 'two');
let nativeRequest;
const expoFetch = async (...args) => { nativeRequest = args; return new Response('{}'); };
const nativeTransport = compile('lib/readerTransport.native.ts', { 'expo/fetch': { fetch: expoFetch } });
await nativeTransport.readerFetch('https://reader.test', { redirect: 'error' });
assert.equal(nativeRequest[1].redirect, 'error', 'native transport preserves redirect refusal through expo/fetch');
console.log('Platform transport, cleartext policy and per-server private credential storage passed.');
