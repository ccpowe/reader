import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { AuthLayout, authStyles as styles } from '../components/AuthLayout';
import type { ReaderAuthClient } from '../lib/readerAuth';
import { getConnectionGeneration } from '../lib/connection';
import { useTranslation } from '../i18n';
import type { AuthMessageKey } from '../i18n/messages/auth';

type FeedbackMessage = { key: AuthMessageKey } | { error: Error };

export function EmailAuthScreen({ client, generation, onChangeServer, serverUrl }: {
  client: ReaderAuthClient; generation: number; onChangeServer?: () => void; serverUrl?: string;
}) {
  const { t } = useTranslation('auth');
  const [screen, setScreen] = useState<'login' | 'signup' | 'reset'>('login');
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);
  const mountedRef = useRef(true);
  const busyRef = useRef(false);
  const passwordInputRef = useRef<TextInput>(null);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  async function submit() {
    if (busyRef.current || screen === 'reset') return;
    if (!email.trim() || !password || (screen === 'signup' && password.length < 8)) {
      setFeedback({ key: screen === 'signup' ? 'emailAndPasswordRequired' : 'emailAndPasswordLoginRequired' });
      return;
    }
    busyRef.current = true;
    setFeedback(null);
    setSubmitting(true);
    try {
      const body = { email: email.trim(), password };
      const result = screen === 'signup' ? await client.auth.signUp(body) : await client.auth.signInWithPassword(body);
      if (!mountedRef.current || getConnectionGeneration() !== generation) return;
      if (result.error) setFeedback({ error: result.error });
    } catch (error) {
      if (mountedRef.current && getConnectionGeneration() === generation) {
        setFeedback(error instanceof Error ? { error } : { key: 'retryLater' });
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current && getConnectionGeneration() === generation) setSubmitting(false);
    }
  }

  function navigate(next: typeof screen) {
    if (busyRef.current) return;
    setFeedback(null);
    setShowPassword(false);
    setScreen(next);
  }
  const copy = {
    login: [t('loginEyebrow'), t('loginTitle'), t('loginSubtitle')],
    signup: [t('signupEyebrow'), t('signupTitle'), t('signupSubtitle')],
    reset: [t('resetEyebrow'), t('forgotPassword'), t('adminPasswordReset')],
  }[screen];
  return (
    <AuthLayout eyebrow={copy[0]} title={copy[1]} subtitle={copy[2]} serverUrl={serverUrl} onChangeServer={onChangeServer} onBack={screen === 'login' ? onChangeServer : () => navigate('login')}>
      <View style={styles.form}>
        {screen === 'reset' ? (
          <Pressable accessibilityRole="button" onPress={() => navigate('login')} style={styles.primary}><Text style={styles.primaryText}>{t('backToSignIn')}</Text><Feather color="#ffffff" name="chevron-right" size={18} /></Pressable>
        ) : <>
          <View style={styles.field}>
            <View style={styles.labelRow}><Text style={styles.label}>{t('email')}</Text></View>
            <View style={styles.inputWrap}><TextInput accessibilityLabel={t('email')} autoCapitalize="none" autoCorrect={false} autoComplete="email" editable={!submitting} keyboardType="email-address" onChangeText={setEmail} onSubmitEditing={() => passwordInputRef.current?.focus()} placeholder="you@example.com" placeholderTextColor="#a1a697" returnKeyType="next" submitBehavior="submit" style={styles.input} value={email} /></View>
          </View>
          <View style={styles.field}>
            <View style={styles.labelRow}><Text style={styles.label}>{t('password')}</Text>{screen === 'login' ? <Pressable accessibilityRole="button" disabled={submitting} onPress={() => navigate('reset')} style={styles.reveal}><Text style={styles.mutedLink}>{t('forgotPassword')}</Text></Pressable> : null}</View>
            <View style={styles.inputWrap}>
              <TextInput ref={passwordInputRef} accessibilityLabel={t('password')} autoCapitalize="none" autoComplete={screen === 'signup' ? 'new-password' : 'current-password'} editable={!submitting} onChangeText={setPassword} onSubmitEditing={() => void submit()} placeholder={screen === 'signup' ? t('signupPasswordPlaceholder') : t('passwordPlaceholder')} placeholderTextColor="#a1a697" returnKeyType="done" secureTextEntry={!showPassword} style={styles.input} value={password} />
              <Pressable accessibilityLabel={showPassword ? t('hidePassword') : t('showPassword')} accessibilityRole="button" onPress={() => setShowPassword(!showPassword)} style={styles.reveal}><Text style={styles.mutedLink}>{showPassword ? t('hide') : t('show')}</Text></Pressable>
            </View>
          </View>
          {feedback ? <Text accessibilityLiveRegion="polite" accessibilityRole="alert" style={[styles.feedback, styles.errorFeedback]}>{'key' in feedback ? t(feedback.key) : feedback.error.message}</Text> : null}
          <Pressable accessibilityRole="button" accessibilityState={{ busy: submitting, disabled: submitting }} disabled={submitting} onPress={() => void submit()} style={[styles.primary, submitting && styles.disabled]}>
            {submitting ? <ActivityIndicator color="#ffffff" size="small" /> : null}<Text style={styles.primaryText}>{submitting ? t('submitting') : screen === 'signup' ? t('createAccount') : t('signIn')}</Text>{!submitting ? <Feather color="#ffffff" name="chevron-right" size={18} /> : null}
          </Pressable>
          <View style={styles.alternative}><Text style={styles.alternativeText}>{screen === 'login' ? t('noAccount') : t('hasAccount')}</Text><Pressable accessibilityRole="button" disabled={submitting} onPress={() => navigate(screen === 'login' ? 'signup' : 'login')} style={styles.link}><Text style={styles.linkText}>{screen === 'login' ? t('createAccount') : t('goToSignIn')}</Text></Pressable></View>
        </>}
      </View>
    </AuthLayout>
  );
}
