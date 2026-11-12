/**
 * 我的 — assembly only (react-guidelines §6): the account, the translation setting (saved at once,
 * PRODUCT §3.5), the interface language (this device), and the sheets that choose or confirm.
 * Profile, password and about are pages of their own.
 */
import { useTabBarInset } from '@/components/CollapsingChrome';
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import Animated from 'react-native-reanimated';

import type { UpdatePreferenceRequest } from '@/lib/api/schema.gen';
import { session, useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl } from '@/lib/feed';
import { interfaceLanguage, useInterfaceLanguage, useLanguage } from '@/lib/interface-language';
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
import { t } from '@/lib/i18n';

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
  useLanguage(); // texts follow the interface language
  const identity = useIdentity();
  const tabBar = useTabBarInset();
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
        error instanceof UnknownOutcome ? t().me.translationUnknown : t().me.translationNotSaved(errorMessage(error)),
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
      title: t().me.interfaceLanguage,
      body: (
        <ChoiceList<InterfaceLanguage>
          onSelect={(key) => {
            interfaceLanguage.set(key);
            setSheet(null);
          }}
          options={INTERFACE_LANGUAGES.map((l) => ({ key: l.key, label: l.label }))}
          selected={language}
        />
      ),
    },
    language: {
      title: t().me.targetLanguage,
      body: (
        <ChoiceList
          onSelect={(key) => void change({ target_locale: key })}
          options={TARGET_LOCALES.map((l) => ({ key: l.key as string, label: l.label }))}
          selected={preference.data?.target_locale ?? null}
        />
      ),
    },
    engine: {
      title: t().me.engine,
      body: (
        <ChoiceList
          onSelect={(key) => void change({ engine_id: key })}
          options={(preference.data?.engines ?? []).map((e) => ({
            key: e.id,
            label: e.label,
            detail: e.available ? undefined : t().me.engineUnavailable,
            disabled: !e.available,
          }))}
          selected={preference.data?.engine_id ?? null}
        />
      ),
    },
    'sign-out': {
      title: t().me.signOut,
      body: (
        <ConfirmSheet
          action={t().me.signOut}
          message={t().me.signOutMessage(email)}
          onCancel={() => setSheet(null)}
          onConfirm={() => {
            setSheet(null);
            session.signOut();
          }}
        />
      ),
    },
    server: {
      title: t().me.changeServer,
      body: (
        <ConfirmSheet
          action={t().me.changeServerAction}
          message={t().me.changeServerMessage(server)}
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
        banner={online ? null : t().me.offline}
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
        <Animated.View className="absolute left-0 right-0" key={notice.id} pointerEvents="none" style={[toastLife, { bottom: tabBar + 24 }]}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
      <Sheet onClose={() => setSheet(null)} title={open?.title} visible={open !== null}>
        {open?.body}
      </Sheet>
    </>
  );
}
