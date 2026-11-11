import '../global.css';

import AsyncStorage from '@react-native-async-storage/async-storage';
import { LiteUI } from '@storybook/react-native-ui-lite';
import { registerRootComponent } from 'expo';

import { view } from './storybook.requires';

// The lightweight on-device navigator: story list and switching, no extra native addons.
const StorybookUI = view.getStorybookUI({
  onDeviceUI: true,
  storage: AsyncStorage,
  enableWebsockets: false,
  CustomUIComponent: LiteUI,
});

registerRootComponent(StorybookUI);
