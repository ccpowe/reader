/** 我的 (PRODUCT §3.5): account, reading (translation), interface language, appearance, server, about. */
import { IconChevronRight } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Pressable, ScrollView, View } from 'react-native';

import { Avatar, Button, Divider, Icon, PageHeader, SectionHeading, Switch, Text } from '@/ui';

function SettingRow({ label, value, children, onPress }: { label: string; value?: string; children?: ReactNode; onPress?: () => void }) {
  return (
    <Pressable className="min-h-12 flex-row items-center gap-3 px-4 py-2 active:bg-pressed" disabled={!onPress && !children} onPress={onPress}>
      <Text className="flex-1" level="body">
        {label}
      </Text>
      {value ? (
        <Text numberOfLines={1} tone="secondary">
          {value}
        </Text>
      ) : null}
      {children ?? (onPress ? <Icon icon={IconChevronRight} /> : null)}
    </Pressable>
  );
}

export function MeView({
  name,
  email,
  translationEnabled,
  targetLanguage,
  engine,
  interfaceLanguage,
  server,
}: {
  name: string;
  email: string;
  translationEnabled: boolean;
  targetLanguage: string;
  engine: string;
  interfaceLanguage: string;
  server: string;
}) {
  return (
    <ScrollView className="flex-1 bg-canvas">
      <PageHeader title="我的" />
      <Pressable className="flex-row items-center gap-3 px-4 py-3 active:bg-pressed">
        <Avatar name={name} size={48} />
        <View className="min-w-0 flex-1">
          <Text level="section" numberOfLines={1}>
            {name}
          </Text>
          <Text numberOfLines={1} tone="secondary">
            {email}
          </Text>
        </View>
        <Icon icon={IconChevronRight} />
      </Pressable>
      <SectionHeading strong title="账号" />
      <SettingRow label="修改密码" onPress={() => undefined} />
      <Divider inset />
      <SectionHeading strong title="阅读" />
      <SettingRow label="自动翻译标题">
        <Switch label="自动翻译标题" value={translationEnabled} />
      </SettingRow>
      <SettingRow label="翻译成" onPress={() => undefined} value={targetLanguage} />
      <SettingRow label="翻译引擎" onPress={() => undefined} value={engine} />
      <Divider inset />
      <SectionHeading strong title="界面" />
      <SettingRow label="界面语言" onPress={() => undefined} value={interfaceLanguage} />
      <SettingRow label="外观" value="跟随系统" />
      <Divider inset />
      <SectionHeading strong title="服务器" />
      <SettingRow label="当前服务器" value={server} />
      <SettingRow label="切换服务器" onPress={() => undefined} />
      <Divider inset />
      <SettingRow label="关于 Reader" onPress={() => undefined} />
      <View className="items-start px-4 py-6">
        <Button title="退出登录" variant="secondary" />
      </View>
    </ScrollView>
  );
}
