import { type ReactNode, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Image, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import Animated, { type SharedValue } from 'react-native-reanimated';
import { Feather } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import type { Session, ReaderAuthClient } from '../lib/readerAuth';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { PageHeaderContent } from '../components/PageHeader';
import { BottomSheetModal } from '../components/BottomSheetModal';
import {
  uploadAvatar,
  getProfile,
  updateTranslationPreference,
  updateProfile,
  type ManagedTranslationEngine,
  type Profile,
  type TranslationPreference,
} from '../lib/api';
import { captureRuntimeContext, getConnectionGeneration, isRuntimeContextCurrent, type ActiveReaderRuntime, type ReaderRuntimeContext } from '../lib/connection';
import { useReaderRuntime } from '../lib/connection/react';
import { invalidateAfterTranslationPreferenceMutation } from '../state/invalidation';
import { readerQueryKeys } from '../state/queryClient';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import { useChromeStyle, useCollapsingChrome } from '../hooks/useCollapsingChrome';
import { PAGE_HEADER_HEIGHT, SCREEN_LIST_BOTTOM_PADDING } from '../ui/layout';
import { touchTarget } from '../ui/tokens';
import { i18n, useTranslation } from '../i18n';
import { useAppLanguage } from '../i18n/react';
import { appLanguages, type AppLanguage } from '../i18n/registry';

const localeLabels: Record<string, string> = {
  'zh-CN': '简体中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
  de: 'Deutsch',
  fr: 'Français',
  es: 'Español',
};

const engineUnavailableKeys = {
  langchain_deepseek_not_installed: 'engineDependencyMissing',
  missing_deepseek_api_key: 'engineApiKeyMissing',
} as const;

function engineDescription(engine: ManagedTranslationEngine): string {
  if (engine.available) return `${engine.provider_name} · ${engine.model_name}`;
  const reasonKey = engineUnavailableKeys[engine.unavailable_reason as keyof typeof engineUnavailableKeys] ?? 'engineConfigIncomplete';
  return i18n.t('profile:engineUnavailable', { reason: i18n.t(`profile:${reasonKey}`) });
}

type ProfileFeedbackKey =
  | 'currentPasswordRequired' | 'passwordTooShort' | 'passwordsDoNotMatch' | 'passwordUpdateFailed' | 'passwordUpdated'
  | 'avatarFormatInvalid' | 'avatarTooLarge' | 'avatarUploaded' | 'avatarUploadFailed'
  | 'emailInvalid' | 'emailUpdateFailed' | 'profileSaved' | 'profileSaveFailed';

type ProfileFeedback = { kind: 'error' | 'success' } & (
  | { messageKey: ProfileFeedbackKey }
  | { error: Error }
);

type PreferenceMutationContext = {
  context: ReaderRuntimeContext | null;
  previous?: TranslationPreference;
  queryKey: ReturnType<typeof readerQueryKeys.translationPreference>;
};

type PreferenceMutationVariables<T> = {
  value: T;
  context: ReaderRuntimeContext | null;
};

export function ProfileScreen({ active, chromeProgress, onChangeServer, onLogout, session, authClient }: { active: boolean; chromeProgress: SharedValue<number>; onChangeServer: () => void; onLogout: () => Promise<void>; session: Session; authClient: ReaderAuthClient }) {
  const { t } = useTranslation('profile');
  const { preference: appLanguagePreference, language: appLanguage, setPreference: setAppLanguagePreference } = useAppLanguage();
  const client = useQueryClient();
  const runtime = useReaderRuntime();
  const translation = useTranslationPreference(session, true);
  const profileQuery = useQuery({
    queryKey: readerQueryKeys.profile(session.user.id, runtime?.identity.server_id),
    queryFn: () => getProfile(session, runtime ?? undefined),
    enabled: Boolean(runtime),
    staleTime: 5 * 60_000,
  });
  const [showAppLanguagePicker, setShowAppLanguagePicker] = useState(false);
  const [appLanguageBusy, setAppLanguageBusy] = useState(false);
  const [showLanguagePicker, setShowLanguagePicker] = useState(false);
  const [showEnginePicker, setShowEnginePicker] = useState(false);
  const [showPasswordChange, setShowPasswordChange] = useState(false);
  const [showProfileEditor, setShowProfileEditor] = useState(false);
  const { onScroll } = useCollapsingChrome(active, chromeProgress, undefined, {
    height: PAGE_HEADER_HEIGHT,
    locked: showAppLanguagePicker || showLanguagePicker || showEnginePicker || showPasswordChange || showProfileEditor,
  });
  const chromeStyle = useChromeStyle(chromeProgress, PAGE_HEADER_HEIGHT);
  const avatarUrl = profileQuery.data?.avatar_url ?? null;
  const displayName = profileQuery.data?.display_name?.trim() || (session.user.email?.split('@')[0] ?? t('defaultDisplayName'));
  const localeMutation = useMutation<TranslationPreference, Error, PreferenceMutationVariables<string>, PreferenceMutationContext>({
    mutationKey: ['reader', runtime?.identity.server_id ?? 'unconfigured', session.user.id, 'translation-preference', 'locale'],
    mutationFn: ({ value, context }) => {
      if (!context || !isRuntimeContextCurrent(context)) throw new Error(t('serverOperationCancelled'));
      return updateTranslationPreference(session, { targetLocale: value }, context.runtime);
    },
    onMutate: async ({ value, context }) => {
      const queryKey = readerQueryKeys.translationPreference(session.user.id, context?.serverId);
      if (!context || !isRuntimeContextCurrent(context)) return { context: null, queryKey };
      await client.cancelQueries({ queryKey });
      if (!isRuntimeContextCurrent(context)) return { context: null, queryKey };
      const previous = client.getQueryData<TranslationPreference>(queryKey);
      if (previous) client.setQueryData<TranslationPreference>(queryKey, { ...previous, target_locale: value });
      return { context, previous, queryKey };
    },
    onError: (error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      if (mutationContext.previous) client.setQueryData(mutationContext.queryKey, mutationContext.previous);
      Alert.alert(t('translationLanguageUpdateFailed'), error instanceof Error ? error.message : t('retryLater'));
    },
    onSuccess: (next, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      client.setQueryData(mutationContext.queryKey, next);
    },
    onSettled: (_data, _error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      void invalidateAfterTranslationPreferenceMutation(client, session.user.id, mutationContext.context.serverId, mutationContext.context).catch(() => undefined);
    },
  });
  const enabledMutation = useMutation<TranslationPreference, Error, PreferenceMutationVariables<boolean>, PreferenceMutationContext>({
    mutationKey: ['reader', runtime?.identity.server_id ?? 'unconfigured', session.user.id, 'translation-preference', 'enabled'],
    mutationFn: ({ value, context }) => {
      if (!context || !isRuntimeContextCurrent(context)) throw new Error(t('serverOperationCancelled'));
      return updateTranslationPreference(session, { isEnabled: value }, context.runtime);
    },
    onMutate: async ({ value, context }) => {
      const queryKey = readerQueryKeys.translationPreference(session.user.id, context?.serverId);
      if (!context || !isRuntimeContextCurrent(context)) return { context: null, queryKey };
      await client.cancelQueries({ queryKey });
      if (!isRuntimeContextCurrent(context)) return { context: null, queryKey };
      const previous = client.getQueryData<TranslationPreference>(queryKey);
      if (previous) client.setQueryData<TranslationPreference>(queryKey, { ...previous, is_enabled: value });
      return { context, previous, queryKey };
    },
    onError: (error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      if (mutationContext.previous) client.setQueryData(mutationContext.queryKey, mutationContext.previous);
      Alert.alert(t('translationSettingsUpdateFailed'), error instanceof Error ? error.message : t('retryLater'));
    },
    onSuccess: (next, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      client.setQueryData(mutationContext.queryKey, next);
    },
    onSettled: (_data, _error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      void invalidateAfterTranslationPreferenceMutation(client, session.user.id, mutationContext.context.serverId, mutationContext.context).catch(() => undefined);
    },
  });
  const engineMutation = useMutation<TranslationPreference, Error, PreferenceMutationVariables<string | null>, PreferenceMutationContext>({
    mutationKey: ['reader', runtime?.identity.server_id ?? 'unconfigured', session.user.id, 'translation-preference', 'engine'],
    mutationFn: ({ value, context }) => {
      if (!context || !isRuntimeContextCurrent(context)) throw new Error(t('serverOperationCancelled'));
      return updateTranslationPreference(session, { engineId: value }, context.runtime);
    },
    onMutate: ({ context }) => ({
      context,
      queryKey: readerQueryKeys.translationPreference(session.user.id, context?.serverId),
    }),
    onError: (error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      Alert.alert(
        t('translationEngineUpdateFailed'),
        t('engineSwitchFailureMessage', { message: error instanceof Error ? error.message : t('retryLater') }),
      );
    },
    onSuccess: (next, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      client.setQueryData(mutationContext.queryKey, next);
      setShowEnginePicker(false);
    },
    onSettled: (_data, _error, _variables, mutationContext) => {
      if (!mutationContext?.context || !isRuntimeContextCurrent(mutationContext.context)) return;
      void invalidateAfterTranslationPreferenceMutation(client, session.user.id, mutationContext.context.serverId, mutationContext.context).catch(() => undefined);
    },
  });

  async function selectAppLanguage(preference: 'system' | AppLanguage) {
    if (appLanguageBusy) return;
    setAppLanguageBusy(true);
    try {
      await setAppLanguagePreference(preference);
      setShowAppLanguagePicker(false);
    } catch {
      Alert.alert(i18n.t('profile:interfaceLanguageUpdateFailed'), i18n.t('profile:retryLater'));
    } finally {
      setAppLanguageBusy(false);
    }
  }

  function selectLocale(targetLocale: string) {
    if (localeMutation.isPending) return;
    setShowLanguagePicker(false);
    if (targetLocale === translation.targetLocale) return;
    const context = captureRuntimeContext(runtime);
    if (context) localeMutation.mutate({ value: targetLocale, context });
  }

  function selectEngine(engineId: string | null) {
    if (engineMutation.isPending) return;
    const selectedEngineId = translation.data?.provider_mode === 'app_default'
      ? null
      : translation.data?.engine_id ?? null;
    if (selectedEngineId === engineId) {
      setShowEnginePicker(false);
      return;
    }
    const context = captureRuntimeContext(runtime);
    if (context) engineMutation.mutate({ value: engineId, context });
  }

  const selectedEngineId = translation.data?.provider_mode === 'app_default'
    ? null
    : translation.data?.engine_id ?? null;
  const selectedAppLanguageName = appLanguages.find((item) => item.id === appLanguage)?.nativeName ?? appLanguage;
  const appLanguageSubtitle = appLanguagePreference === 'system'
    ? t('interfaceLanguageSystemValue', { language: selectedAppLanguageName })
    : selectedAppLanguageName;
  const engineName = translation.data?.provider_mode === 'app_default'
    ? t('defaultEngineLabel', { engine: translation.engineLabel })
    : translation.engineLabel;
  const engineSubtitle = translation.isPending
    ? t('loadingEngine')
    : engineMutation.isPending
      ? t('switchingEngine')
      : translation.engineAvailable ? engineName : t('unavailableEngineLabel', { engine: engineName });

  const hasOpenEditor = showAppLanguagePicker || showLanguagePicker || showEnginePicker || showPasswordChange || showProfileEditor;
  if (!active && !hasOpenEditor) return null;

  return <View style={styles.page}>
    <Animated.View pointerEvents="box-none" style={[styles.header, chromeStyle]}>
      <PageHeaderContent
        compact
        title={t('title')}
        session={session}
        onProfile={() => setShowProfileEditor(true)}
      />
    </Animated.View>
    <Animated.ScrollView style={styles.scroll} contentContainerStyle={styles.content} onScroll={active ? onScroll : undefined} scrollEventThrottle={16} showsVerticalScrollIndicator={false}>
      <Pressable accessibilityRole="button" accessibilityLabel={t('editProfileAccessibility')} onPress={() => setShowProfileEditor(true)} style={styles.hero}>
        {avatarUrl ? <Image source={{ uri: avatarUrl }} style={styles.profileImage} /> : <View style={styles.avatarFallback}><Text style={styles.avatarText}>{displayName.slice(0, 1).toUpperCase()}</Text></View>}
        <View style={styles.identityCopy}><Text numberOfLines={1} style={styles.name}>{displayName}</Text><Text numberOfLines={1} style={styles.email}>{session.user.email ?? t('emailMissing')}</Text></View>
        <Feather color="#A0A398" name="chevron-right" size={18} />
      </Pressable>
      <Text style={styles.sectionCaption}>{t('readingSection')}</Text>
      <View style={styles.settingsGroup}>
        <SettingRow disabled={appLanguageBusy} icon="globe" onPress={() => setShowAppLanguagePicker(true)} subtitle={appLanguageSubtitle} title={t('interfaceLanguage')} />
        <SettingRow
          icon="type"
          right={(
            <Switch
              accessibilityLabel={t('contentTranslation')}
              disabled={translation.isPending || enabledMutation.isPending}
              onValueChange={(value) => {
                const context = captureRuntimeContext(runtime);
                if (context) enabledMutation.mutate({ value, context });
              }}
              trackColor={{ false: '#C9C9C6', true: '#111111' }}
              thumbColor="#FCFBF8"
              value={translation.enabled}
            />
          )}
          subtitle={translation.isPending ? t('loadingTranslationPreference') : enabledMutation.isPending ? t('saving') : translation.enabled ? t('translationEnabled') : t('translationDisabled')}
          title={t('contentTranslation')}
        />
        <SettingRow disabled={translation.isPending} icon="globe" onPress={() => setShowLanguagePicker(true)} subtitle={localeMutation.isPending ? t('savingTranslationLanguage') : t('contentLanguage', { language: localeLabels[translation.targetLocale] ?? translation.targetLocale })} title={t('translationLanguage')} />
        <SettingRow disabled={translation.isPending} icon="cpu" onPress={() => setShowEnginePicker(true)} subtitle={engineSubtitle} title={t('translationEngine')} />
      </View>
      <Text style={styles.sectionCaption}>{t('accountSection')}</Text>
      <View style={styles.settingsGroup}>
        <SettingRow icon="lock" onPress={() => setShowPasswordChange(true)} title={t('changePassword')} />
        <SettingRow icon="server" onPress={onChangeServer} title={t('changeServer')} />
        <SettingRow icon="info" onPress={() => Alert.alert('Reader', t('aboutDescription'))} title={t('aboutReader')} />
      </View>
      <Pressable accessibilityRole="button" onPress={() => void onLogout()} style={styles.signOut}><Text style={styles.signOutText}>{t('signOut')}</Text></Pressable>
      <View style={styles.version}><Text style={styles.versionText}>{t('tagline')}</Text></View>
    </Animated.ScrollView>
    <BottomSheetModal onClose={() => setShowAppLanguagePicker(false)} visible={showAppLanguagePicker}>
      <View style={styles.modalHandle} />
      <View style={styles.modalHeader}><Text style={styles.modalTitle}>{t('interfaceLanguage')}</Text><Pressable accessibilityLabel={t('closeInterfaceLanguage')} accessibilityRole="button" onPress={() => setShowAppLanguagePicker(false)} style={styles.modalClose}><Feather color="#444444" name="x" size={22} /></Pressable></View>
      <Text style={styles.modalHint}>{t('interfaceLanguageHint')}</Text>
      {([{ id: 'system' as const, nativeName: t('interfaceLanguageSystem') }, ...appLanguages]).map((item) => {
        const selected = appLanguagePreference === item.id;
        return <Pressable accessibilityLabel={item.nativeName} accessibilityRole="radio" accessibilityState={{ checked: selected, disabled: appLanguageBusy }} disabled={appLanguageBusy} key={item.id} onPress={() => void selectAppLanguage(item.id)} style={[styles.localeChoice, selected && styles.localeChoiceActive, appLanguageBusy && styles.localeChoiceDisabled]}><Text style={[styles.localeChoiceText, selected && styles.localeChoiceTextActive]}>{item.nativeName}</Text>{selected ? <Feather color="#FFFFFF" name="check" size={20} /> : null}</Pressable>;
      })}
    </BottomSheetModal>
    <BottomSheetModal onClose={() => setShowLanguagePicker(false)} visible={showLanguagePicker}>
          <View style={styles.modalHandle} />
          <View style={styles.modalHeader}><Text style={styles.modalTitle}>{t('translationLanguage')}</Text><Pressable accessibilityLabel={t('closeTranslationLanguage')} accessibilityRole="button" onPress={() => setShowLanguagePicker(false)} style={styles.modalClose}><Feather color="#444444" name="x" size={22} /></Pressable></View>
          <Text style={styles.modalHint}>{t('translationLanguageHint')}</Text>
          {(translation.data?.supported_locales ?? Object.keys(localeLabels)).map((locale) => <Pressable accessibilityLabel={localeLabels[locale] ?? locale} accessibilityRole="radio" accessibilityState={{ checked: translation.targetLocale === locale, disabled: localeMutation.isPending }} disabled={localeMutation.isPending} key={locale} onPress={() => selectLocale(locale)} style={[styles.localeChoice, translation.targetLocale === locale && styles.localeChoiceActive, localeMutation.isPending && styles.localeChoiceDisabled]}><Text style={[styles.localeChoiceText, translation.targetLocale === locale && styles.localeChoiceTextActive]}>{localeLabels[locale] ?? locale}</Text>{translation.targetLocale === locale ? <Feather color="#FFFFFF" name="check" size={20} /> : null}</Pressable>)}
    </BottomSheetModal>
    <BottomSheetModal onClose={() => setShowEnginePicker(false)} visible={showEnginePicker}>
          <View style={styles.modalHandle} />
          <View style={styles.modalHeader}><Text style={styles.modalTitle}>{t('translationEngine')}</Text><Pressable accessibilityLabel={t('closeTranslationEngine')} accessibilityRole="button" onPress={() => setShowEnginePicker(false)} style={styles.modalClose}><Feather color="#444444" name="x" size={22} /></Pressable></View>
          <Text style={styles.modalHint}>{t('translationEngineHint')}</Text>
          <EngineChoice
            available
            description={t('currentEngine', { engine: translation.data?.provider_mode === 'app_default' ? translation.engineLabel : t('engineDefinedBySystem') })}
            disabled={engineMutation.isPending}
            label={t('followDefaultEngine')}
            onPress={() => selectEngine(null)}
            selected={selectedEngineId === null}
          />
          {(translation.data?.managed_engines ?? []).map((engine) => <EngineChoice
            available={engine.available}
            description={engineDescription(engine)}
            disabled={engineMutation.isPending || !engine.available}
            key={engine.engine_id}
            label={engine.label}
            onPress={() => selectEngine(engine.engine_id)}
            selected={selectedEngineId === engine.engine_id}
          />)}
    </BottomSheetModal>
    <ProfileEditorSheet
      client={authClient}
      email={session.user.email ?? ''}
      generation={getConnectionGeneration()}
      onClose={() => setShowProfileEditor(false)}
      onSaved={(next) => client.setQueryData(readerQueryKeys.profile(session.user.id, runtime?.identity.server_id), next)}
      profile={profileQuery.data ?? null}
      runtime={runtime}
      session={session}
      visible={showProfileEditor}
    />
    <PasswordChangeSheet client={authClient} generation={getConnectionGeneration()} onClose={() => setShowPasswordChange(false)} visible={showPasswordChange} />
  </View>;
}

function PasswordChangeSheet({ client, generation, onClose, visible }: { client: ReaderAuthClient; generation: number; onClose: () => void; visible: boolean }) {
  const { t } = useTranslation('profile');
  const [currentPassword, setCurrentPassword] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<ProfileFeedback | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  async function submit() {
    if (busy) return;
    if (!currentPassword) {
      setFeedback({ kind: 'error', messageKey: 'currentPasswordRequired' });
      return;
    }
    if (password.length < 8 || password !== confirmation) {
      setFeedback({ kind: 'error', messageKey: password.length < 8 ? 'passwordTooShort' : 'passwordsDoNotMatch' });
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const result = await client.auth.updatePassword({ current_password: currentPassword, password });
      if (!mountedRef.current || getConnectionGeneration() !== generation) return;
      if (result.error) {
        setFeedback({ kind: 'error', error: result.error });
        return;
      }
      setFeedback({ kind: 'success', messageKey: 'passwordUpdated' });
      setCurrentPassword('');
      setPassword('');
      setConfirmation('');
      onClose();
    } catch {
      if (mountedRef.current && getConnectionGeneration() === generation) {
        setFeedback({ kind: 'error', messageKey: 'passwordUpdateFailed' });
      }
    } finally {
      if (mountedRef.current && getConnectionGeneration() === generation) setBusy(false);
    }
  }

  return <BottomSheetModal onClose={onClose} visible={visible}>
    <View style={styles.modalHeader}><Text style={styles.modalTitle}>{t('changePassword')}</Text><Pressable accessibilityLabel={t('closeChangePassword')} accessibilityRole="button" onPress={onClose} style={styles.modalClose}><Feather color="#444444" name="x" size={22} /></Pressable></View>
    <Text style={styles.modalHint}>{t('passwordHint')}</Text>
    <TextInput accessibilityLabel={t('currentPassword')} autoCapitalize="none" autoComplete="current-password" onChangeText={setCurrentPassword} placeholder={t('currentPassword')} secureTextEntry style={passwordInputStyle} value={currentPassword} />
    <TextInput accessibilityLabel={t('newPassword')} autoCapitalize="none" autoComplete="new-password" onChangeText={setPassword} placeholder={t('newPassword')} placeholderTextColor="#94a3b8" secureTextEntry style={passwordInputStyle} value={password} />
    <TextInput accessibilityLabel={t('confirmPassword')} autoCapitalize="none" autoComplete="new-password" onChangeText={setConfirmation} placeholder={t('confirmPassword')} placeholderTextColor="#94a3b8" secureTextEntry style={passwordInputStyle} value={confirmation} />
    {feedback ? <Text accessibilityLiveRegion="polite" accessibilityRole={feedback.kind === 'error' ? 'alert' : undefined} style={[feedbackTextStyle, feedback.kind === 'error' ? errorFeedbackStyle : successFeedbackStyle]}>{'messageKey' in feedback ? t(feedback.messageKey) : feedback.error.message}</Text> : null}
    <Pressable accessibilityRole="button" accessibilityState={{ busy, disabled: busy }} disabled={busy} onPress={() => void submit()} style={[passwordButtonStyle, busy && styles.localeChoiceDisabled]}>
      {busy ? <ActivityIndicator color="#FFFFFF" size="small" /> : null}
      <Text style={passwordButtonTextStyle}>{busy ? t('updatingPassword') : t('updatePassword')}</Text>
    </Pressable>
  </BottomSheetModal>;
}

const passwordInputStyle = {
  backgroundColor: '#FFFFFF',
  borderColor: '#E5E5E3',
  borderRadius: 10,
  borderWidth: 1,
  color: '#111111',
  marginBottom: 10,
  minHeight: 48,
  paddingHorizontal: 14,
};
const feedbackTextStyle = { fontSize: 13, lineHeight: 19, marginBottom: 4, marginTop: 4 };
const errorFeedbackStyle = { color: '#BA1A1A' };
const successFeedbackStyle = { color: '#206A3A' };
const passwordButtonStyle = {
  alignItems: 'center' as const,
  backgroundColor: '#111111',
  borderRadius: 999,
  flexDirection: 'row' as const,
  gap: 8,
  justifyContent: 'center' as const,
  minHeight: 48,
  marginTop: 8,
};
const passwordButtonTextStyle = { color: '#FFFFFF', fontSize: 15, fontWeight: '700' as const };

function ProfileEditorSheet({ client, email, generation, onClose, onSaved, profile, runtime, session, visible }: {
  client: ReaderAuthClient;
  email: string;
  generation: number;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
  profile: Profile | null;
  runtime: ActiveReaderRuntime | null;
  session: Session;
  visible: boolean;
}) {
  const { t } = useTranslation('profile');
  const [displayName, setDisplayName] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [nextEmail, setNextEmail] = useState(email);
  const [currentPassword, setCurrentPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarPreviewUri, setAvatarPreviewUri] = useState('');
  const [feedback, setFeedback] = useState<ProfileFeedback | null>(null);
  const mountedRef = useRef(true);

  const draftScope = JSON.stringify([generation, runtime?.identity.server_id, session.user.id]);
  const draftRef = useRef({ scope: draftScope, open: false, name: false, avatar: false, email: false });

  useEffect(() => {
    if (!visible) {
      draftRef.current.open = false;
      return;
    }
    if (!draftRef.current.open || draftRef.current.scope !== draftScope) {
      draftRef.current = { scope: draftScope, open: true, name: false, avatar: false, email: false };
      setFeedback(null);
      setBusy(false);
      setAvatarBusy(false);
    }
    // Late query data may fill untouched fields, but never replace typed drafts.
    if (!draftRef.current.name) setDisplayName(profile?.display_name ?? '');
    if (!draftRef.current.avatar) {
      setAvatarUrl(profile?.avatar_url ?? '');
      setAvatarPreviewUri(profile?.avatar_url ?? '');
    }
    if (!draftRef.current.email) setNextEmail(email);
  }, [draftScope, email, profile, visible]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  async function pickAvatar() {
    if (busy || avatarBusy || getConnectionGeneration() !== generation) return;
    const draft = draftRef.current;
    const isCurrent = () => mountedRef.current && draftRef.current === draft && draft.open && getConnectionGeneration() === generation;
    setFeedback(null);
    const result = await ImagePicker.launchImageLibraryAsync({
      allowsEditing: true,
      aspect: [1, 1],
      mediaTypes: ['images'],
      quality: 0.85,
    });
    if (!isCurrent() || result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    const contentType = normaliseAvatarContentType(asset.mimeType, asset.fileName);
    if (!contentType) {
      setFeedback({ kind: 'error', messageKey: 'avatarFormatInvalid' });
      return;
    }
    draftRef.current.avatar = true;
    setAvatarBusy(true);
    setAvatarPreviewUri(asset.uri);
    try {
      const fileResponse = await fetch(asset.uri);
      const fileBody = asset.file ?? await fileResponse.arrayBuffer();
      const fileSize = asset.file?.size ?? (fileBody instanceof ArrayBuffer ? fileBody.byteLength : asset.fileSize ?? 0);
      if (!fileSize || fileSize > 5 * 1024 * 1024) {
        throw new Error('avatar_too_large');
      }
      if (!isCurrent()) return;
      const updated = await uploadAvatar(session, { contentType, body: fileBody }, runtime ?? undefined);
      if (!isCurrent()) return;
      setAvatarUrl(updated.avatar_url ?? '');
      setAvatarPreviewUri(updated.avatar_url ?? '');
      onSaved(updated);
      setFeedback({ kind: 'success', messageKey: 'avatarUploaded' });
    } catch (error) {
      if (isCurrent()) {
        setAvatarPreviewUri(avatarUrl);
        setFeedback(error instanceof Error && error.message === 'avatar_too_large'
          ? { kind: 'error', messageKey: 'avatarTooLarge' }
          : error instanceof Error && error.message.includes('5 MB')
            ? { kind: 'error', error }
            : { kind: 'error', messageKey: 'avatarUploadFailed' });
      }
    } finally {
      if (isCurrent()) setAvatarBusy(false);
    }
  }

  async function submit() {
    if (busy || avatarBusy) return;
    const draft = draftRef.current;
    const isCurrent = () => mountedRef.current && draftRef.current === draft && draft.open && getConnectionGeneration() === generation;
    const trimmedName = displayName.trim();
    const trimmedAvatar = avatarUrl.trim();
    const trimmedEmail = nextEmail.trim();
    if (!trimmedEmail || !trimmedEmail.includes('@')) {
      setFeedback({ kind: 'error', messageKey: 'emailInvalid' });
      return;
    }
    if (trimmedEmail !== email && !currentPassword) {
      setFeedback({ kind: 'error', messageKey: 'currentPasswordRequired' });
      return;
    }
    if (getConnectionGeneration() !== generation) return;
    setBusy(true);
    setFeedback(null);
    try {
      let nextProfile = profile;
      if (trimmedName !== (profile?.display_name ?? '') || trimmedAvatar !== (profile?.avatar_url ?? '')) {
        nextProfile = await updateProfile(
          session,
          { avatarUrl: trimmedAvatar || null, displayName: trimmedName || null },
          runtime ?? undefined,
        );
      }
      if (!isCurrent()) return;
      const emailChanged = trimmedEmail !== email;
      if (emailChanged) {
        const result = await client.auth.updateEmail({ email: trimmedEmail, current_password: currentPassword });
        if (!isCurrent()) return;
        if (result.error) {
          setFeedback({ kind: 'error', error: result.error });
          return;
        }
      }
      if (nextProfile) onSaved(nextProfile);
      setFeedback({
        kind: 'success',
        messageKey: 'profileSaved',
      });
      setCurrentPassword('');
      onClose();
    } catch {
      if (isCurrent()) {
        setFeedback({ kind: 'error', messageKey: 'profileSaveFailed' });
      }
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  return <BottomSheetModal onClose={onClose} visible={visible}>
    <View style={styles.modalHeader}><Text style={styles.modalTitle}>{t('profile')}</Text><Pressable accessibilityLabel={t('closeProfile')} accessibilityRole="button" onPress={onClose} style={styles.modalClose}><Feather color="#444444" name="x" size={22} /></Pressable></View>
    <Text style={styles.modalHint}>{t('profileHint')}</Text>
    <View style={styles.avatarEditor}>
      {avatarPreviewUri
        ? <Image accessibilityLabel={t('avatarPreview')} source={{ uri: avatarPreviewUri }} style={styles.avatarEditorImage} />
        : <View accessibilityLabel={t('defaultAvatar')} style={styles.avatarEditorFallback}><Feather color="#666666" name="user" size={38} /></View>}
      <View style={styles.avatarEditorActions}>
        <Pressable accessibilityLabel={t('pickAvatarAccessibility')} accessibilityRole="button" accessibilityState={{ busy: avatarBusy, disabled: busy || avatarBusy }} disabled={busy || avatarBusy} onPress={() => void pickAvatar()} style={styles.avatarPickButton}>
          {avatarBusy ? <ActivityIndicator color="#111111" size="small" /> : <Feather color="#111111" name="image" size={18} />}
          <Text style={styles.avatarPickButtonText}>{avatarBusy ? t('uploadingAvatar') : t('pickAvatar')}</Text>
        </Pressable>
        {avatarUrl ? <Pressable accessibilityLabel={t('removeAvatar')} accessibilityRole="button" disabled={busy || avatarBusy} onPress={() => { draftRef.current.avatar = true; setAvatarUrl(''); setAvatarPreviewUri(''); }}><Text style={styles.avatarRemoveText}>{t('removeAvatar')}</Text></Pressable> : null}
      </View>
    </View>
    <TextInput accessibilityLabel={t('nickname')} autoCapitalize="sentences" onChangeText={(value) => { draftRef.current.name = true; setDisplayName(value); }} placeholder={t('nickname')} placeholderTextColor="#94a3b8" style={passwordInputStyle} value={displayName} />
    {nextEmail.trim() !== email ? <TextInput accessibilityLabel={t('currentPassword')} autoCapitalize="none" autoComplete="current-password" onChangeText={setCurrentPassword} placeholder={t('currentPassword')} secureTextEntry style={passwordInputStyle} value={currentPassword} /> : null}
    <TextInput accessibilityLabel={t('email')} autoCapitalize="none" autoComplete="email" autoCorrect={false} keyboardType="email-address" onChangeText={(value) => { draftRef.current.email = true; setNextEmail(value); }} placeholder={t('email')} placeholderTextColor="#94a3b8" style={passwordInputStyle} value={nextEmail} />
    {feedback ? <Text accessibilityLiveRegion="polite" accessibilityRole={feedback.kind === 'error' ? 'alert' : undefined} style={[feedbackTextStyle, feedback.kind === 'error' ? errorFeedbackStyle : successFeedbackStyle]}>{'messageKey' in feedback ? t(feedback.messageKey) : feedback.error.message}</Text> : null}
    <Pressable accessibilityRole="button" accessibilityState={{ busy, disabled: busy || avatarBusy }} disabled={busy || avatarBusy} onPress={() => void submit()} style={[passwordButtonStyle, (busy || avatarBusy) && styles.localeChoiceDisabled]}>
      {busy ? <ActivityIndicator color="#FFFFFF" size="small" /> : null}
      <Text style={passwordButtonTextStyle}>{busy ? t('saving') : t('saveProfile')}</Text>
    </Pressable>
  </BottomSheetModal>;
}

function normaliseAvatarContentType(mimeType?: string | null, fileName?: string | null): 'image/jpeg' | 'image/png' | 'image/webp' | null {
  const normalised = mimeType?.toLowerCase();
  if (normalised === 'image/jpeg' || normalised === 'image/png' || normalised === 'image/webp') return normalised;
  const extension = fileName?.toLowerCase().split('.').pop();
  if (extension === 'jpg' || extension === 'jpeg') return 'image/jpeg';
  if (extension === 'png') return 'image/png';
  if (extension === 'webp') return 'image/webp';
  return null;
}

function EngineChoice({ available, description, disabled, label, onPress, selected }: { available: boolean; description: string; disabled: boolean; label: string; onPress: () => void; selected: boolean }) {
  return <Pressable accessibilityLabel={label} accessibilityRole="radio" accessibilityState={{ checked: selected, disabled }} disabled={disabled} onPress={onPress} style={[styles.engineChoice, selected && styles.engineChoiceActive, disabled && styles.localeChoiceDisabled]}><View style={styles.engineChoiceCopy}><Text style={[styles.engineChoiceText, selected && styles.engineChoiceTextActive]}>{label}</Text><Text style={[styles.engineChoiceDescription, selected && styles.engineChoiceDescriptionActive]}>{description}</Text></View>{selected ? <Feather color="#FFFFFF" name="check" size={20} /> : <Feather color={available ? '#777777' : '#BA1A1A'} name={available ? 'circle' : 'alert-circle'} size={20} />}</Pressable>;
}

function SettingRow({ disabled = false, icon, onPress, right, subtitle, title }: { disabled?: boolean; icon: keyof typeof Feather.glyphMap; onPress?: () => void; right?: ReactNode; subtitle?: string; title: string }) {
  const { t } = useTranslation('profile');
  const accessibilityLabel = subtitle ? t('settingAccessibility', { title, subtitle }) : title;
  const content = <><View style={styles.settingIcon}><Feather color="#5E5E5E" name={icon} size={20} /></View><View style={styles.settingCopy}><Text style={styles.settingTitle}>{title}</Text>{subtitle ? <Text style={styles.settingSubtitle}>{subtitle}</Text> : null}</View>{right ?? (onPress ? <Feather color="#777777" name={disabled ? 'lock' : 'chevron-right'} size={20} /> : null)}</>;
  if (!onPress) {
    return <View accessibilityLabel={accessibilityLabel} style={[styles.settingRow, disabled && styles.disabled]}>{content}</View>;
  }
  return <Pressable accessibilityLabel={accessibilityLabel} accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={[styles.settingRow, disabled && styles.disabled]}>{content}</Pressable>;
}

const styles = StyleSheet.create({
  modalClose: { alignItems: 'center', flexShrink: 0, justifyContent: 'center', minHeight: touchTarget, minWidth: touchTarget },
  page: { backgroundColor: '#FCFBF8', flex: 1, overflow: 'hidden' },
  header: { backgroundColor: '#FCFBF8', height: PAGE_HEADER_HEIGHT, left: 0, paddingHorizontal: 24, position: 'absolute', right: 0, top: 0, zIndex: 2 },
  scroll: { flex: 1 },
  content: { paddingHorizontal: 24, paddingTop: PAGE_HEADER_HEIGHT, paddingBottom: SCREEN_LIST_BOTTOM_PADDING },
  hero: { alignItems: 'center', flexDirection: 'row', gap: 14, paddingVertical: 24, borderBottomWidth: 1, borderBottomColor: '#E8E9E2' },
  identityCopy: { flex: 1, minWidth: 0 },
  profileImage: { backgroundColor: '#EAECE3', borderRadius: 29, height: 58, width: 58 },
  avatarFallback: { alignItems: 'center', backgroundColor: '#EAECE3', borderRadius: 29, height: 58, justifyContent: 'center', width: 58 },
  avatarText: { color: '#575F4E', fontSize: 26, fontWeight: '600' }, name: { color: '#242924', fontSize: 21, fontWeight: '700', lineHeight: 30 }, email: { color: '#929589', fontSize: 12, marginTop: 3 },
  sectionCaption: { color: '#93968C', fontSize: 11, letterSpacing: .5, marginTop: 23, marginBottom: 6 },
  signOut: { alignItems: 'center', justifyContent: 'center', minHeight: 48, marginTop: 24 }, signOutText: { color: '#986456', fontSize: 13 },
  settingsGroup: {}, settingRow: { alignItems: 'center', borderBottomColor: '#E8E9E2', borderBottomWidth: 1, flexDirection: 'row', gap: 12, minHeight: 58, paddingVertical: 12 }, disabled: { opacity: .55 },
  settingIcon: { alignItems: 'center', justifyContent: 'center', width: 24 }, settingCopy: { flex: 1, minWidth: 0 }, settingTitle: { color: '#242924', fontSize: 14 }, settingSubtitle: { color: '#96998E', fontSize: 11, lineHeight: 16, marginTop: 3 },
  version: { alignItems: 'center', paddingVertical: 16 }, versionText: { color: '#A6A89E', fontSize: 9, letterSpacing: 2 }, modalHandle: { alignSelf: 'center', backgroundColor: '#C9C9C6', borderRadius: 3, height: 5, marginBottom: 18, width: 40 }, modalHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' }, modalTitle: { color: '#111111', flexShrink: 1, fontSize: 22, fontWeight: '700' }, modalHint: { color: '#666666', fontSize: 13, lineHeight: 20, marginBottom: 16, marginTop: 6 }, avatarEditor: { alignItems: 'center', flexDirection: 'row', gap: 14, marginBottom: 16 }, avatarEditorImage: { backgroundColor: '#E8E8E6', borderRadius: 34, height: 68, width: 68 }, avatarEditorFallback: { alignItems: 'center', backgroundColor: '#E8E8E6', borderRadius: 34, height: 68, justifyContent: 'center', width: 68 }, avatarEditorActions: { alignItems: 'flex-start', flex: 1, gap: 8 }, avatarPickButton: { alignItems: 'center', backgroundColor: '#F2F2F0', borderRadius: 999, flexDirection: 'row', gap: 7, minHeight: 42, paddingHorizontal: 14 }, avatarPickButtonText: { color: '#111111', fontSize: 14, fontWeight: '700' }, avatarRemoveText: { color: '#BA1A1A', fontSize: 13, fontWeight: '600', paddingHorizontal: 8, paddingVertical: 4 }, localeChoice: { alignItems: 'center', backgroundColor: '#FFFFFF', borderColor: '#E5E5E3', borderRadius: 12, borderWidth: 1, flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8, minHeight: 52, paddingHorizontal: 16 }, localeChoiceActive: { backgroundColor: '#111111', borderColor: '#111111' }, localeChoiceDisabled: { opacity: .55 }, localeChoiceText: { color: '#222222', flexShrink: 1, fontSize: 15, fontWeight: '600' }, localeChoiceTextActive: { color: '#FFFFFF' }, engineChoice: { alignItems: 'center', backgroundColor: '#FFFFFF', borderColor: '#E5E5E3', borderRadius: 12, borderWidth: 1, flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8, minHeight: 66, paddingHorizontal: 16, paddingVertical: 10 }, engineChoiceActive: { backgroundColor: '#111111', borderColor: '#111111' }, engineChoiceCopy: { flex: 1, paddingRight: 12 }, engineChoiceText: { color: '#222222', fontSize: 15, fontWeight: '700' }, engineChoiceTextActive: { color: '#FFFFFF' }, engineChoiceDescription: { color: '#777777', fontSize: 11, lineHeight: 16, marginTop: 3 }, engineChoiceDescriptionActive: { color: '#D8D8D6' },
});
