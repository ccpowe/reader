/**
 * 我的 (PRODUCT §3.5), old app layout (device screenshots) at P25 values: profile, then two groups
 * — 阅读与翻译, 账户与连接 — of rows with a leading icon and the current value under the label;
 * sign out as a quiet text action, and the brand line at the bottom. Appearance follows the
 * system (P20), so it has no row.
 */
import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import { IconChevronRight, IconCpu, IconInfoCircle, IconLanguage, IconLock, IconServer, IconLetterT, IconWorld } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Pressable, ScrollView, View } from 'react-native';

import { Avatar, Divider, Icon, PageHeader, SectionHeading, Switch, Text } from '@/ui';

function SettingRow({ icon, label, value, control, onPress }: { icon: TablerIcon; label: string; value?: string; control?: ReactNode; onPress?: () => void }) {
  return (
    <Pressable accessibilityRole={onPress ? 'button' : undefined} className="active:bg-pressed" disabled={!onPress} onPress={onPress}>
      <View className="mx-6 min-h-16 flex-row items-center gap-3 border-b border-subtle py-3">
        <Icon icon={icon} size="toolbar" tone="strong" />
        <View className="min-w-0 flex-1">
          <Text level="body" numberOfLines={1}>
            {label}
          </Text>
          {value ? (
            <Text level="desc" numberOfLines={1} tone="tertiary">
              {value}
            </Text>
          ) : null}
        </View>
        {control ?? (onPress ? <Icon icon={IconChevronRight} /> : null)}
      </View>
    </Pressable>
  );
}

export function MeView({
  user,
  email,
  translationEnabled,
  targetLanguage,
  engine,
  interfaceLanguage,
  server,
}: {
  user: { name: string; avatarUrl?: string | null };
  email: string;
  translationEnabled: boolean;
  targetLanguage: string;
  engine: string;
  interfaceLanguage: string;
  server: string;
}) {
  const go = () => undefined;
  return (
    <ScrollView className="flex-1 bg-canvas" contentContainerClassName="pb-8">
      <PageHeader title="我的" user={user} />
      <Pressable accessibilityRole="button" className="flex-row items-center gap-4 px-6 py-4 active:bg-pressed" onPress={go}>
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
      <SettingRow icon={IconWorld} label="界面语言" onPress={go} value={interfaceLanguage} />
      <SettingRow
        control={<Switch label="内容翻译" value={translationEnabled} />}
        icon={IconLetterT}
        label="内容翻译"
        value="标题自动翻译，正文按需翻译"
      />
      <SettingRow icon={IconLanguage} label="翻译语言" onPress={go} value={`内容语言：${targetLanguage}`} />
      <SettingRow icon={IconCpu} label="翻译引擎" onPress={go} value={engine} />
      <SectionHeading title="账户与连接" />
      <SettingRow icon={IconLock} label="修改密码" onPress={go} />
      <SettingRow icon={IconServer} label="更换服务器" onPress={go} value={server} />
      <SettingRow icon={IconInfoCircle} label="关于 Reader" onPress={go} />
      <Pressable accessibilityRole="button" className="mt-8 h-11 items-center justify-center self-center px-4 active:bg-pressed rounded-md" onPress={go}>
        <Text tone="danger">退出登录</Text>
      </Pressable>
      <Text className="mt-4 text-center tracking-widest" level="caption" tone="tertiary">
        READER · 好内容，慢慢读。
      </Text>
    </ScrollView>
  );
}
