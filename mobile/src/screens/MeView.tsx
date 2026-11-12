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
import { t } from '@/lib/i18n';

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
  const pending = translation.kind === 'loading' ? t().common.loading : undefined;
  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <ScrollView className="flex-1" contentContainerClassName="pb-8">
        <PageHeader title={t().me.title} user={user} />
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
        <SectionHeading title={t().me.reading} />
        <SettingRow icon={IconWorld} label={t().me.interfaceLanguage} onPress={onInterfaceLanguage} value={interfaceLanguage} />
        {translation.kind === 'failed' ? (
          <View className="mx-6 py-3">
            <Message action={<Button onPress={onRetryTranslation} title={t().common.retry} variant="ghost" />} kind="danger">
              {t().me.translationLoadFailed(translation.message)}
            </Message>
          </View>
        ) : null}
        <SettingRow
          control={
            ready ? (
              <Switch label={t().me.translation} onChange={onToggleTranslation} value={ready.enabled} />
            ) : translation.kind === 'loading' || saving ? (
              <Spinner />
            ) : undefined
          }
          disabled={!ready}
          icon={IconLetterT}
          label={t().me.translation}
          value={t().me.translationHint}
        />
        <SettingRow
          disabled={!ready}
          icon={IconLanguage}
          label={t().me.targetLanguage}
          onPress={onTargetLanguage}
          value={translation.kind === 'ready' ? t().me.contentLanguage(translation.language) : pending}
        />
        <SettingRow
          disabled={!ready}
          icon={IconCpu}
          label={t().me.engine}
          onPress={onEngine}
          value={translation.kind === 'ready' ? translation.engine : pending}
        />
        <SectionHeading title={t().me.account} />
        <SettingRow icon={IconLock} label={t().me.changePassword} onPress={onChangePassword} />
        <SettingRow icon={IconServer} label={t().me.changeServer} onPress={onChangeServer} value={server} />
        <SettingRow icon={IconInfoCircle} label={t().me.about} onPress={onAbout} />
        <Pressable
          accessibilityRole="button"
          className="mt-8 h-11 items-center justify-center self-center rounded-md px-4 active:bg-pressed"
          onPress={onSignOut}
        >
          <Text tone="danger">{t().me.signOut}</Text>
        </Pressable>
        <Text className="mt-4 text-center tracking-widest" level="caption" tone="tertiary">
          {`READER · ${t().me.tagline}`}
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
      <Button onPress={onCancel} title={t().common.cancel} variant="ghost" />
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
        accessibilityHint={t().me.changeAvatarHint}
        accessibilityLabel={t().me.avatar}
        accessibilityRole="button"
        className="items-center gap-2 self-center active:opacity-70"
        disabled={avatarBusy}
        onPress={onAvatar}
      >
        <Avatar name={typed || email} size={88} uri={avatarUrl} />
        {avatarBusy ? <Spinner /> : <Text tone="secondary">{t().me.changeAvatar}</Text>}
      </Pressable>
      <Input
        editable={!saving}
        error={error}
        hint={t().me.nicknameHint}
        label={t().me.nickname}
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
        <Text level="ui-strong">{t().me.email}</Text>
        <Text tone="secondary">{email}</Text>
      </View>
      <Button
        disabled={!changed || saving}
        large
        loading={saving}
        onPress={() => onSave?.(typed)}
        title={t().common.save}
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
        label={t().me.currentPassword}
        onChangeText={edit(setCurrent)}
        secureTextEntry
        value={current}
      />
      <Input
        autoCapitalize="none"
        autoComplete="new-password"
        editable={!saving}
        error={field('next')}
        hint={t().me.newPasswordHint}
        label={t().me.newPassword}
        onChangeText={edit(setNext)}
        secureTextEntry
        value={next}
      />
      <Input
        autoCapitalize="none"
        autoComplete="new-password"
        editable={!saving}
        error={field('confirm')}
        label={t().me.confirmPassword}
        onChangeText={edit(setConfirm)}
        onSubmitEditing={() => onSubmit?.(current, next, confirm)}
        secureTextEntry
        value={confirm}
      />
      {problem?.field === 'form' ? <Message kind="danger">{problem.message}</Message> : null}
      <Text level="desc" tone="tertiary">
        {t().me.passwordNote}
      </Text>
      <Button
        disabled={saving}
        large
        loading={saving}
        onPress={() => onSubmit?.(current, next, confirm)}
        title={t().me.changePassword}
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
        <Text tone="secondary">{t().me.tagline}</Text>
      </View>
      <View>
        <SettingRow icon={IconInfoCircle} label={t().me.appVersion} value={appVersion} />
        <SettingRow icon={IconServer} label={t().me.server} value={server} />
      </View>
    </ScrollView>
  );
}
