/**
 * 我的 — assembly only (react-guidelines §6): the account, the translation setting (saved at once,
 * PRODUCT §3.5), the interface language (this device), and the sheets that choose or confirm.
 * Profile, password and about are pages of their own.
 */
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import Animated from 'react-native-reanimated';

import type { UpdatePreferenceRequest } from '@/lib/api/schema.gen';
import { session, useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl } from '@/lib/feed';
import { interfaceLanguage, useInterfaceLanguage } from '@/lib/interface-language';
import {
  engineLabel,
  INTERFACE_LANGUAGES,
  interfaceLanguageLabel,
  localeLabel,
  TARGET_LOCALES,
  type InterfaceLanguage,
} from '@/lib/me';
import { translationPreferenceQuery } from '@/lib/queries/boards';
import { AccountChanged, setTranslationPreference } from '@/lib/queries/me';
import { meQuery, UnknownOutcome } from '@/lib/queries/today';
import { errorMessage } from '@/lib/user-error';
import { ChoiceList, ConfirmSheet, MeView, type TranslationSetting } from '@/screens/MeView';
import { Sheet, Toast } from '@/ui';
import { duration } from '@/ui/theme';

const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

type SheetKind = 'interface' | 'language' | 'engine' | 'sign-out' | 'server' | null;

export default function Me() {
  const identity = useIdentity();
  const current = useSession();
  const visible = useRef(true);
  useFocusEffect(
    useCallback(() => {
      visible.current = true;
      return () => {
        visible.current = false;
      };
    }, []),
  );
  const online = useOnline();
  const me = useQuery(meQuery(identity));
  const preference = useQuery(translationPreferenceQuery(identity));
  const language = useInterfaceLanguage();
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [savingPreference, setSavingPreference] = useState(false);
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);

  const showNotice = useCallback((message: string) => {
    if (visible.current) setNotice((n) => ({ id: (n?.id ?? 0) + 1, message }));
  }, []);

  if (current.status !== 'signed-in') return null;
  const server = current.server.base.replace(/^https?:\/\//, '');
  const absolute = absoluteUrl(current.server.apiBaseUrl);
  const email = me.data?.email ?? current.user.email;
  const name = me.data?.display_name || email.split('@')[0] || '';

  async function change(update: UpdatePreferenceRequest) {
    setSheet(null);
    setSavingPreference(true);
    try {
      await setTranslationPreference(identity, update);
    } catch (error) {
      if (error instanceof AccountChanged) return;
      showNotice(
        error instanceof UnknownOutcome ? '没收到服务器的确认，已重新读取翻译设置。' : `翻译设置没有保存：${errorMessage(error)}`,
      );
    } finally {
      setSavingPreference(false);
    }
  }

  let translation: TranslationSetting;
  if (preference.data)
    translation = {
      kind: 'ready',
      enabled: preference.data.enabled,
      language: localeLabel(preference.data.target_locale),
      engine: engineLabel(preference.data),
      saving: savingPreference,
    };
  else if (preference.isError) translation = { kind: 'failed', message: errorMessage(preference.error) };
  else translation = { kind: 'loading' };

  const sheets: Record<Exclude<SheetKind, null>, { title: string; body: ReactNode }> = {
    interface: {
      title: '界面语言',
      body: (
        <ChoiceList<InterfaceLanguage>
          onSelect={(key) => {
            interfaceLanguage.set(key);
            setSheet(null);
          }}
          options={INTERFACE_LANGUAGES.map((l) => ({
            ...l,
            detail: l.key === 'en' ? '英文界面即将支持；先记下这个选择' : undefined,
          }))}
          selected={language}
        />
      ),
    },
    language: {
      title: '翻译语言',
      body: (
        <ChoiceList
          onSelect={(key) => void change({ target_locale: key })}
          options={TARGET_LOCALES.map((l) => ({ key: l.key as string, label: l.label }))}
          selected={preference.data?.target_locale ?? null}
        />
      ),
    },
    engine: {
      title: '翻译引擎',
      body: (
        <ChoiceList
          onSelect={(key) => void change({ engine_id: key })}
          options={(preference.data?.engines ?? []).map((e) => ({
            key: e.id,
            label: e.label,
            detail: e.available ? undefined : '暂不可用',
            disabled: !e.available,
          }))}
          selected={preference.data?.engine_id ?? null}
        />
      ),
    },
    'sign-out': {
      title: '退出登录',
      body: (
        <ConfirmSheet
          action="退出登录"
          message={`将退出 ${email}。收藏和订阅都保存在服务器上，重新登录后还在。`}
          onCancel={() => setSheet(null)}
          onConfirm={() => {
            setSheet(null);
            session.signOut();
          }}
        />
      ),
    },
    server: {
      title: '更换服务器',
      body: (
        <ConfirmSheet
          action="断开并更换"
          message={`将断开 ${server} 并退出登录，然后填写新的服务器地址。`}
          onCancel={() => setSheet(null)}
          onConfirm={() => {
            setSheet(null);
            session.forgetServer();
          }}
        />
      ),
    },
  };
  const open = sheet ? sheets[sheet] : null;

  return (
    <>
      <MeView
        banner={online ? null : '没有网络，设置暂时改不了。'}
        email={email}
        interfaceLanguage={interfaceLanguageLabel(language)}
        onAbout={() => router.push('/about')}
        onChangePassword={() => router.push('/password')}
        onChangeServer={() => setSheet('server')}
        onEngine={() => setSheet('engine')}
        onInterfaceLanguage={() => setSheet('interface')}
        onOpenProfile={() => router.push('/profile')}
        onRetryTranslation={() => void preference.refetch()}
        onSignOut={() => setSheet('sign-out')}
        onTargetLanguage={() => setSheet('language')}
        onToggleTranslation={(enabled) => void change({ enabled })}
        server={server}
        translation={translation}
        user={{ name, avatarUrl: absolute(me.data?.avatar_url) }}
      />
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="none" style={toastLife}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
      <Sheet onClose={() => setSheet(null)} title={open?.title} visible={open !== null}>
        {open?.body}
      </Sheet>
    </>
  );
}
