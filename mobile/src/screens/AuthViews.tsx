/**
 * 连接服务器 and 登录 / 注册 (PRODUCT §3.8): a single server (P17), then email sign-in. Presentational:
 * values and callbacks come from the routes (src/app/connect.tsx, sign-in.tsx).
 * Layout: the READER brand row and the old app's large title (P25), fields 44 high, one primary
 * button. The keyboard never covers the focused field (react-native-keyboard-controller).
 */
import type { ReactNode } from 'react';
import { Image, Pressable, View } from 'react-native';
import { KeyboardAwareScrollView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button, Callout, Input, Message, Text } from '@/ui';

const logo = require('../../assets/reader-logo.png');

function AuthFrame({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  const insets = useSafeAreaInsets();
  return (
    <KeyboardAwareScrollView
      bottomOffset={24}
      className="flex-1 bg-canvas"
      contentContainerStyle={{ paddingTop: insets.top + 8, paddingBottom: insets.bottom + 32 }}
      keyboardShouldPersistTaps="handled"
    >
      <View className="px-6">
        <View className="h-12 flex-row items-center gap-2">
          <Image accessibilityIgnoresInvertColors className="rounded-lg" source={logo} style={{ width: 32, height: 32 }} />
          <Text className="tracking-widest" level="caption-strong">
            READER
          </Text>
        </View>
        <Text className="mt-12" level="page">
          {title}
        </Text>
        {description ? (
          <Text className="mt-2" level="read" tone="secondary">
            {description}
          </Text>
        ) : null}
        <View className="mt-8 gap-4">{children}</View>
      </View>
    </KeyboardAwareScrollView>
  );
}

export type ConnectViewProps = {
  address: string;
  token: string;
  status: 'editing' | 'checking' | 'confirming';
  addressError?: string | null;
  tokenError?: string | null;
  /** shown above the fields, e.g. "the server token was changed" */
  notice?: string | null;
  /** the server that answered, to confirm */
  server?: { serverId: string; base: string; apiBaseUrl: string } | null;
  onChange?: (field: 'address' | 'token', value: string) => void;
  onSubmit?: () => void;
  onConfirm?: () => void;
  onBack?: () => void;
};

export function ConnectView({
  address,
  token,
  status,
  addressError,
  tokenError,
  notice,
  server,
  onChange,
  onSubmit,
  onConfirm,
  onBack,
}: ConnectViewProps) {
  if (status === 'confirming' && server) {
    return (
      <AuthFrame description="确认这是你的服务器再继续。" title="确认服务器">
        <Callout>
          <Text level="body-strong">{server.serverId}</Text>
          <Text className="mt-1" level="desc" tone="secondary">
            {server.base}
          </Text>
          {server.apiBaseUrl !== server.base ? (
            <Text className="mt-1" level="desc" tone="secondary">
              接口地址：{server.apiBaseUrl}
            </Text>
          ) : null}
        </Callout>
        <Button large onPress={onConfirm} title="确认并继续" variant="primary" />
        <Button large onPress={onBack} title="返回修改" variant="ghost" />
      </AuthFrame>
    );
  }
  const checking = status === 'checking';
  return (
    <AuthFrame description="输入你部署的 Reader 服务器地址，以及管理员设置的服务器口令。" title="连接服务器">
      {notice ? <Message kind="warning">{notice}</Message> : null}
      <Input
        autoCapitalize="none"
        autoComplete="url"
        autoCorrect={false}
        editable={!checking}
        error={addressError}
        keyboardType="url"
        label="服务器地址"
        onChangeText={(value) => onChange?.('address', value)}
        placeholder="https://reader.example.com"
        returnKeyType="next"
        value={address}
      />
      <Input
        autoCapitalize="none"
        autoCorrect={false}
        editable={!checking}
        error={tokenError}
        label="服务器口令"
        onChangeText={(value) => onChange?.('token', value)}
        onSubmitEditing={onSubmit}
        placeholder="部署时设置的口令"
        returnKeyType="go"
        secureTextEntry
        value={token}
      />
      <Button className="mt-2" large loading={checking} onPress={onSubmit} title="连接" variant="primary" />
    </AuthFrame>
  );
}

export type SignInViewProps = {
  mode: 'sign-in' | 'register';
  email: string;
  password: string;
  displayName: string;
  submitting: boolean;
  emailError?: string | null;
  passwordError?: string | null;
  /** a problem that belongs to no single field (offline, rate limited) */
  formError?: string | null;
  /** the session ended on the server */
  expired?: boolean;
  /** which server this signs in to */
  serverLabel: string;
  onChange?: (field: 'email' | 'password' | 'displayName', value: string) => void;
  onSubmit?: () => void;
  onSwitchMode?: () => void;
  onChangeServer?: () => void;
};

export function SignInView({
  mode,
  email,
  password,
  displayName,
  submitting,
  emailError,
  passwordError,
  formError,
  expired,
  serverLabel,
  onChange,
  onSubmit,
  onSwitchMode,
  onChangeServer,
}: SignInViewProps) {
  const register = mode === 'register';
  return (
    <AuthFrame
      description={
        <>
          {register ? '在 ' : '登录到 '}
          <Text level="read">{serverLabel}</Text>
          {register ? ' 创建账号。' : '。'}
        </>
      }
      title={register ? '注册' : '登录'}
    >
      {expired && !formError ? <Message kind="warning">登录已过期，请重新登录。</Message> : null}
      {register ? (
        <Input
          autoComplete="name"
          editable={!submitting}
          hint="可以不填，之后在「我的」里修改。"
          label="名字"
          onChangeText={(value) => onChange?.('displayName', value)}
          placeholder="怎么称呼你"
          value={displayName}
        />
      ) : null}
      <Input
        autoCapitalize="none"
        autoComplete="email"
        autoCorrect={false}
        editable={!submitting}
        error={emailError}
        keyboardType="email-address"
        label="邮箱"
        onChangeText={(value) => onChange?.('email', value)}
        placeholder="you@example.com"
        returnKeyType="next"
        textContentType="emailAddress"
        value={email}
      />
      <Input
        autoComplete={register ? 'new-password' : 'current-password'}
        editable={!submitting}
        error={passwordError}
        hint={register ? '至少 8 个字符。' : undefined}
        label="密码"
        onChangeText={(value) => onChange?.('password', value)}
        onSubmitEditing={onSubmit}
        returnKeyType="go"
        secureTextEntry
        textContentType={register ? 'newPassword' : 'password'}
        value={password}
      />
      {formError ? <Message kind="danger">{formError}</Message> : null}
      <Button className="mt-2" large loading={submitting} onPress={onSubmit} title={register ? '注册' : '登录'} variant="primary" />
      <Button disabled={submitting} onPress={onSwitchMode} title={register ? '已有账号？登录' : '还没有账号？注册'} variant="ghost" />
      <Pressable accessibilityRole="button" className="mt-6 h-11 items-center justify-center active:opacity-60" disabled={submitting} onPress={onChangeServer}>
        <Text level="ui" tone="secondary">
          更换服务器
        </Text>
      </Pressable>
    </AuthFrame>
  );
}
