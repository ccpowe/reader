const { getDefaultConfig } = require('expo/metro-config');
const { withUniwindConfig } = require('uniwind/metro');
const { withStorybook } = require('@storybook/react-native/withStorybook');

const config = getDefaultConfig(__dirname);

// Tailwind classes via uniwind; Storybook replaces the entry only when STORYBOOK_ENABLED=true
// (`pnpm run storybook`), otherwise its modules are stubbed out of the app bundle.
module.exports = withStorybook(
  withUniwindConfig(config, { cssEntryFile: './global.css', dtsFile: './src/uniwind-types.d.ts' }),
  { websockets: false },
);
