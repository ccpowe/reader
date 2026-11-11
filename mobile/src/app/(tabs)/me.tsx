/**
 * 我的 — interim for block 1: who is signed in, sign out, change server. The full page
 * (MeView: profile, translation, language) comes in block 7 (DECISIONS P28).
 */
import { View } from 'react-native';

import { session, useSession } from '@/lib/app-session';
import { Button, PageHeader, Text } from '@/ui';

export default function Me() {
  const current = useSession();
  if (current.status !== 'signed-in') return null;
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="我的" />
      <View className="gap-3 px-6 pt-4">
        <Text level="body">{current.user.email}</Text>
        <Text level="desc" tone="secondary">
          {current.server.base.replace(/^https?:\/\//, '')}
        </Text>
        <View className="mt-6 items-start gap-3">
          <Button onPress={() => session.signOut()} title="退出登录" />
          <Button onPress={() => session.forgetServer()} title="更换服务器" variant="ghost" />
        </View>
      </View>
    </View>
  );
}
