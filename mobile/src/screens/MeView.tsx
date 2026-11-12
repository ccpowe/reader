/**
 * 我的 (PRODUCT §3.5), old app layout (device screenshots) at P25 values: profile, then two groups
 * — 阅读与翻译, 账户与连接 — of rows with a leading icon and the current value under the label;
 * sign out as a quiet text action, and the brand line at the bottom. Appearance follows the
 * system (P20), so it has no row. Also the pushed pages (profile, password, about) and the sheet
 * contents (choices, confirmations). Presentational.
 */
import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import {
  IconCheck,
  IconChevronRight,
  IconCpu,
  IconInfoCircle,
  IconLanguage,
  IconLetterT,
  IconLock,
  IconServer,
  IconWorld,
} from '@tabler/icons-react-native';
import { useState, type ReactNode } from 'react';
import { Pressable, ScrollView, View } from 'react-native';

import { Avatar, Button, Divider, Icon, Input, Message, PageHeader, SectionHeading, Spinner, Switch, Text } from '@/ui';

function SettingRow({
  icon,
  label,
  value,
  control,
  disabled = false,
  onPress,
}: {
  icon: TablerIcon;
  label: string;
  value?: string;
  control?: ReactNode;
  disabled?: boolean;
  onPress?: () => void;
}) {
  const pressable = Boolean(onPress) && !disabled;
  return (
    <Pressable
      accessibilityRole={pressable ? 'button' : undefined}
      className="active:bg-pressed"
      disabled={!pressable}
      onPress={onPress}
    >
      <View className="mx-6 min-h-16 flex-row items-center gap-3 border-b border-subtle py-3">
        <Icon icon={icon} size="toolbar" tone="strong" />
        <View className="min-w-0 flex-1">
          <Text level="body" numberOfLines={1} tone={disabled ? 'tertiary' : 'primary'}>
            {label}
          </Text>
          {value ? (
            <Text level="desc" numberOfLines={1} tone="tertiary">
              {value}
            </Text>
          ) : null}
        </View>
        {control ?? (pressable ? <Icon icon={IconChevronRight} /> : null)}
      </View>
    </Pressable>
  );
}

/** The translation rows' data; null while it loads or when it failed (message). */
export type TranslationSetting =
  /** saving: a change is on its way (shown once the server accepts it); the rows wait */
  | { kind: 'ready'; enabled: boolean; language: string; engine: string; saving?: boolean }
  | { kind: 'loading' }
  | { kind: 'failed'; message: string };

export function MeView({
  user,
  email,
  banner,
  translation,
  interfaceLanguage,
  server,
  onOpenProfile,
  onInterfaceLanguage,
  onToggleTranslation,
  onTargetLanguage,
  onEngine,
  onRetryTranslation,
  onChangePassword,
  onChangeServer,
  onAbout,
  onSignOut,
}: {
  user: { name: string; avatarUrl?: string | null };
  email: string;
  banner?: string | null;
  translation: TranslationSetting;
  interfaceLanguage: string;
  server: string;
  onOpenProfile?: () => void;
  onInterfaceLanguage?: () => void;
  onToggleTranslation?: (enabled: boolean) => void;
  onTargetLanguage?: () => void;
  onEngine?: () => void;
  onRetryTranslation?: () => void;
  onChangePassword?: () => void;
  onChangeServer?: () => void;
  onAbout?: () => void;
  onSignOut?: () => void;
}) {
  const ready = translation.kind === 'ready' && !translation.saving ? translation : null;
  const saving = translation.kind === 'ready' && translation.saving;
  const pending = translation.kind === 'loading' ? '加载中…' : undefined;
  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <ScrollView className="flex-1" contentContainerClassName="pb-8">
        <PageHeader title="我的" user={user} />
        <Pressable
          accessibilityRole="button"
          className="flex-row items-center gap-4 px-6 py-4 active:bg-pressed"
          onPress={onOpenProfile}
        >
          <Avatar name={user.name} size={56} uri={user.avatarUrl} />
          <View className="min-w-0 flex-1">
            <Text level="h2" numberOfLines={1}>
              {user.name}
            </Text>
            <Text numberOfLines={1} tone="secondary">
              {email}
            </Text>
          </View>
          <Icon icon={IconChevronRight} />
        </Pressable>
        <Divider inset />
        <SectionHeading title="阅读与翻译" />
        <SettingRow icon={IconWorld} label="界面语言" onPress={onInterfaceLanguage} value={interfaceLanguage} />
        {translation.kind === 'failed' ? (
          <View className="mx-6 py-3">
            <Message action={<Button onPress={onRetryTranslation} title="重试" variant="ghost" />} kind="danger">
              {`翻译设置没加载出来：${translation.message}`}
            </Message>
          </View>
        ) : null}
        <SettingRow
          control={
            ready ? (
              <Switch label="内容翻译" onChange={onToggleTranslation} value={ready.enabled} />
            ) : translation.kind === 'loading' || saving ? (
              <Spinner />
            ) : undefined
          }
          disabled={!ready}
          icon={IconLetterT}
          label="内容翻译"
          value="标题自动翻译，正文按需翻译"
        />
        <SettingRow
          disabled={!ready}
          icon={IconLanguage}
          label="翻译语言"
          onPress={onTargetLanguage}
          value={translation.kind === 'ready' ? `内容语言：${translation.language}` : pending}
        />
        <SettingRow
          disabled={!ready}
          icon={IconCpu}
          label="翻译引擎"
          onPress={onEngine}
          value={translation.kind === 'ready' ? translation.engine : pending}
        />
        <SectionHeading title="账户与连接" />
        <SettingRow icon={IconLock} label="修改密码" onPress={onChangePassword} />
        <SettingRow icon={IconServer} label="更换服务器" onPress={onChangeServer} value={server} />
        <SettingRow icon={IconInfoCircle} label="关于 Reader" onPress={onAbout} />
        <Pressable
          accessibilityRole="button"
          className="mt-8 h-11 items-center justify-center self-center rounded-md px-4 active:bg-pressed"
          onPress={onSignOut}
        >
          <Text tone="danger">退出登录</Text>
        </Pressable>
        <Text className="mt-4 text-center tracking-widest" level="caption" tone="tertiary">
          READER · 好内容，慢慢读。
        </Text>
      </ScrollView>
    </View>
  );
}

/** Sheet content: one choice among a few (languages, engines); unavailable ones cannot be chosen. */
export function ChoiceList<K extends string>({
  options,
  selected,
  onSelect,
}: {
  options: { key: K; label: string; detail?: string; disabled?: boolean }[];
  selected: K | null;
  onSelect?: (key: K) => void;
}) {
  return (
    <View className="-mx-4">
      {options.map((option) => (
        <Pressable
          accessibilityRole="radio"
          accessibilityState={{ checked: option.key === selected, disabled: option.disabled }}
          className="min-h-14 flex-row items-center gap-3 px-4 py-3 active:bg-pressed"
          disabled={option.disabled}
          key={option.key}
          onPress={() => onSelect?.(option.key)}
        >
          <View className="min-w-0 flex-1">
            <Text level="body" tone={option.disabled ? 'tertiary' : 'primary'}>
              {option.label}
            </Text>
            {option.detail ? (
              <Text level="desc" tone="tertiary">
                {option.detail}
              </Text>
            ) : null}
          </View>
          {option.key === selected ? <Icon icon={IconCheck} size="toolbar" tone="strong" /> : null}
        </Pressable>
      ))}
    </View>
  );
}

/** Sheet content: say what will happen, then do it or not. */
export function ConfirmSheet({
  message,
  action,
  onConfirm,
  onCancel,
}: {
  message: string;
  action: string;
  onConfirm?: () => void;
  onCancel?: () => void;
}) {
  return (
    <View className="gap-3">
      <Text tone="secondary">{message}</Text>
      <Button large onPress={onConfirm} title={action} variant="danger" />
      <Button onPress={onCancel} title="取消" variant="ghost" />
    </View>
  );
}

/** 个人资料: the avatar (tap to change or remove), the display name, and the email (read only). */
export function ProfileView({
  name,
  email,
  avatarUrl,
  avatarBusy = false,
  saving = false,
  error,
  onAvatar,
  onSave,
  onEdit,
}: {
  /** the display name as stored (empty = none) */
  name: string;
  email: string;
  avatarUrl?: string | null;
  avatarBusy?: boolean;
  saving?: boolean;
  error?: string | null;
  onAvatar?: () => void;
  onSave?: (name: string) => void;
  onEdit?: () => void;
}) {
  const [typed, setTyped] = useState(name);
  const changed = typed.trim() !== name;
  return (
    <ScrollView contentContainerClassName="gap-6 px-6 py-6" keyboardShouldPersistTaps="handled">
      <Pressable
        accessibilityHint="更换或删除头像"
        accessibilityLabel="头像"
        accessibilityRole="button"
        className="items-center gap-2 self-center active:opacity-70"
        disabled={avatarBusy}
        onPress={onAvatar}
      >
        <Avatar name={typed || email} size={88} uri={avatarUrl} />
        {avatarBusy ? <Spinner /> : <Text tone="secondary">更换头像</Text>}
      </Pressable>
      <Input
        editable={!saving}
        error={error}
        hint="不填就显示邮箱前面的部分。"
        label="昵称"
        maxLength={120}
        onChangeText={(text) => {
          setTyped(text);
          onEdit?.();
        }}
        onSubmitEditing={() => changed && onSave?.(typed)}
        returnKeyType="done"
        value={typed}
      />
      <View className="gap-1">
        <Text level="ui-strong">邮箱</Text>
        <Text tone="secondary">{email}</Text>
      </View>
      <Button
        disabled={!changed || saving}
        large
        loading={saving}
        onPress={() => onSave?.(typed)}
        title="保存"
        variant="primary"
      />
    </ScrollView>
  );
}

/** 修改密码: current, new, new again; a problem shows under its field. */
export function PasswordView({
  saving = false,
  problem,
  onSubmit,
  onEdit,
}: {
  saving?: boolean;
  problem?: { field: 'current' | 'next' | 'confirm' | 'form'; message: string } | null;
  onSubmit?: (current: string, next: string, confirm: string) => void;
  onEdit?: () => void;
}) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const field = (name: 'current' | 'next' | 'confirm') => (problem?.field === name ? problem.message : undefined);
  const edit = (set: (text: string) => void) => (text: string) => {
    set(text);
    onEdit?.();
  };
  return (
    <ScrollView contentContainerClassName="gap-4 px-6 py-6" keyboardShouldPersistTaps="handled">
      <Input
        autoCapitalize="none"
        autoComplete="current-password"
        editable={!saving}
        error={field('current')}
        label="当前密码"
        onChangeText={edit(setCurrent)}
        secureTextEntry
        value={current}
      />
      <Input
        autoCapitalize="none"
        autoComplete="new-password"
        editable={!saving}
        error={field('next')}
        hint="8 到 128 个字符。"
        label="新密码"
        onChangeText={edit(setNext)}
        secureTextEntry
        value={next}
      />
      <Input
        autoCapitalize="none"
        autoComplete="new-password"
        editable={!saving}
        error={field('confirm')}
        label="再输一次新密码"
        onChangeText={edit(setConfirm)}
        onSubmitEditing={() => onSubmit?.(current, next, confirm)}
        secureTextEntry
        value={confirm}
      />
      {problem?.field === 'form' ? <Message kind="danger">{problem.message}</Message> : null}
      <Text level="desc" tone="tertiary">
        改完后，其他设备上的登录会失效，需要重新登录。
      </Text>
      <Button
        disabled={saving}
        large
        loading={saving}
        onPress={() => onSubmit?.(current, next, confirm)}
        title="修改密码"
        variant="primary"
      />
    </ScrollView>
  );
}

/** 关于 Reader: what is running. */
export function AboutView({ appVersion, server }: { appVersion: string; server: string }) {
  return (
    <ScrollView contentContainerClassName="gap-6 py-8">
      <View className="items-center gap-2">
        <Text level="h1">READER</Text>
        <Text tone="secondary">好内容，慢慢读。</Text>
      </View>
      <View>
        <SettingRow icon={IconInfoCircle} label="App 版本" value={appVersion} />
        <SettingRow icon={IconServer} label="服务器" value={server} />
      </View>
    </ScrollView>
  );
}
