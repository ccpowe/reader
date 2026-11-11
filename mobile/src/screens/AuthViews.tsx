/** 连接服务器 and 登录 (PRODUCT §3.8): single server (P17), then email sign-in / register. */
import { View } from 'react-native';

import { Button, Callout, Input, Message, Text } from '@/ui';

export function ConnectView({ address = '', error, identity, connecting = false }: {
  address?: string;
  error?: string | null;
  /** server found; ask to confirm it is the right one */
  identity?: { name: string; id: string } | null;
  connecting?: boolean;
}) {
  return (
    <View className="flex-1 justify-center gap-4 bg-canvas px-6">
      <Text level="h2">连接服务器</Text>
      <Text tone="secondary">输入你部署的 Reader 服务器地址。</Text>
      <Input autoCapitalize="none" defaultValue={address} error={error} keyboardType="url" label="服务器地址" placeholder="https://reader.example.com" />
      {identity ? (
        <Callout>
          <Text level="ui-strong">找到服务器：{identity.name}</Text>
          <Text className="mt-1" level="desc" tone="secondary">
            标识 {identity.id}。确认这是你的服务器再继续。
          </Text>
        </Callout>
      ) : null}
      <Button large loading={connecting} title={identity ? '确认并继续' : '连接'} variant="primary" />
    </View>
  );
}

export function SignInView({ mode = 'sign-in', error, retryAfter }: { mode?: 'sign-in' | 'register'; error?: string | null; retryAfter?: string | null }) {
  return (
    <View className="flex-1 justify-center gap-4 bg-canvas px-6">
      <Text level="h2">{mode === 'sign-in' ? '登录' : '注册'}</Text>
      {retryAfter ? <Message kind="warning">尝试次数过多，{retryAfter}后再试。</Message> : null}
      <Input autoCapitalize="none" keyboardType="email-address" label="邮箱" placeholder="you@example.com" />
      <Input error={error} label="密码" placeholder="至少 8 个字符" secureTextEntry />
      <Button large title={mode === 'sign-in' ? '登录' : '注册'} variant="primary" />
      <Button title={mode === 'sign-in' ? '还没有账号？注册' : '已有账号？登录'} variant="ghost" />
    </View>
  );
}
