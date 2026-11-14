/**
 * Root: providers, session restore, and which routes exist in each session state
 * (Stack.Protected): connect → sign in → the app. While the session restores nothing is shown.
 */
import '../../global.css';

import { QueryClientProvider } from '@tanstack/react-query';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { connectAppLifecycle, queryClient, session, useSession } from '@/lib/app-session';
import { useMountEffect } from '@/lib/use-mount-effect';

function Routes() {
  const state = useSession();
  if (state.status === 'restoring') return <View className="flex-1 bg-canvas" />;
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: 'transparent' } }}>
      <Stack.Protected guard={state.status === 'no-server'}>
        <Stack.Screen name="connect" />
      </Stack.Protected>
      <Stack.Protected guard={state.status === 'signed-out'}>
        <Stack.Screen name="sign-in" />
      </Stack.Protected>
      <Stack.Protected guard={state.status === 'signed-in'}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="article/[id]" />
        <Stack.Screen name="board-item" />
        <Stack.Screen name="subscription/[id]" />
        <Stack.Screen name="profile" />
        <Stack.Screen name="password" />
        <Stack.Screen name="about" />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  useMountEffect(() => {
    void session.restore();
    return connectAppLifecycle();
  });
  return (
    <GestureHandlerRootView className="flex-1 bg-canvas">
      <SafeAreaProvider>
        <KeyboardProvider>
          <QueryClientProvider client={queryClient}>
            <Routes />
          </QueryClientProvider>
          {/* light icons on the dark theme, dark on the light one (P20) */}
          <StatusBar style="auto" />
        </KeyboardProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
