import type { Preview } from '@storybook/react-native';
import { View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

// Full-screen stories own their insets; component stories get a 16dp gutter.
const preview: Preview = {
  decorators: [
    (Story, context) => (
      <SafeAreaProvider>
        <View className="flex-1 bg-canvas">
          {/* The bottom inset belongs to the tab bar (or the page), not to the frame. */}
          <SafeAreaView edges={['top', 'left', 'right']} style={{ flex: 1 }}>
            <View className={context.parameters.fullScreen ? 'flex-1' : 'flex-1 p-4'}>
              <Story />
            </View>
          </SafeAreaView>
        </View>
      </SafeAreaProvider>
    ),
  ],
};
export default preview;
