import type { ReactNode } from 'react';
import { Image, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from '../i18n';

export function AuthLayout({ children, eyebrow, title, subtitle, onBack, serverUrl, onChangeServer }: {
  children: ReactNode; eyebrow: string; title: string; subtitle: string;
  onBack?: () => void; serverUrl?: string; onChangeServer?: () => void;
}) {
  const { t } = useTranslation('auth');
  // App places auth content below the top safe area. Keyboard coordinates
  // include that inset; KAV's parent-relative layout coordinates do not.
  const insets = useSafeAreaInsets();
  let serverName = serverUrl;
  try { if (serverUrl) { const url = new URL(serverUrl); serverName = `${url.host}${url.pathname === '/' ? '' : url.pathname}`; } } catch { /* Keep the supplied address readable. */ }
  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} keyboardVerticalOffset={insets.top} style={authStyles.page}>
      <ScrollView contentContainerStyle={authStyles.content} keyboardDismissMode="interactive" keyboardShouldPersistTaps="handled">
        <View style={authStyles.top}>
          {onBack ? <Pressable accessibilityLabel={t('back')} accessibilityRole="button" onPress={onBack} style={authStyles.back}><Feather color="#242924" name="chevron-left" size={24} /></Pressable> : null}
          <Text style={authStyles.wordmark}>READER</Text>
        </View>
        <View style={authStyles.body}>
          <Image accessibilityLabel="Reader" source={require('../assets/reader-logo.png')} style={authStyles.logo} resizeMode="contain" />
          <Text style={authStyles.eyebrow}>{eyebrow}</Text>
          <Text accessibilityRole="header" style={authStyles.title}>{title}</Text>
          <Text style={authStyles.subtitle}>{subtitle}</Text>
          {children}
          {serverUrl || onChangeServer ? <View style={authStyles.server}>
            <Feather color="#92958c" name="server" size={17} />
            <Text numberOfLines={2} style={authStyles.serverName}>{serverName ?? t('currentServer')}</Text>
            {onChangeServer ? <Pressable accessibilityLabel={t('changeServer')} accessibilityRole="button" onPress={onChangeServer} style={authStyles.serverButton}><Text style={authStyles.mutedLink}>{t('change')}</Text></Pressable> : null}
          </View> : null}
        </View>
        <Text style={authStyles.footer}>{t('tagline')}</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

export const authStyles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#fcfbf8', width: '100%' },
  content: { flexGrow: 1, width: '100%', maxWidth: 520, alignSelf: 'center' },
  top: { minHeight: 58, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 24, gap: 8 },
  back: { width: 36, height: 44, alignItems: 'center', justifyContent: 'center', marginLeft: -12 },
  wordmark: { fontSize: 12, letterSpacing: 2.2, fontWeight: '700', color: '#242924' },
  body: { paddingHorizontal: 30, paddingTop: 24, paddingBottom: 20, flex: 1 },
  logo: { width: 70, height: 70, marginLeft: -5, marginBottom: 27 },
  eyebrow: { fontSize: 11, letterSpacing: 1.6, color: '#92958c', marginBottom: 9 },
  title: { fontSize: 32, lineHeight: 43, fontWeight: '700', letterSpacing: -1, color: '#242924', marginBottom: 13 },
  subtitle: { fontSize: 14, lineHeight: 25, color: '#777d72' },
  form: { marginTop: 32 },
  field: { marginBottom: 17 },
  labelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 44, marginBottom: 7 },
  label: { fontSize: 13, fontWeight: '500', color: '#242924' },
  inputWrap: { flexDirection: 'row', alignItems: 'center', minHeight: 52, borderRadius: 12, borderWidth: 1, borderColor: '#e4e5dd', backgroundColor: '#ffffff', paddingHorizontal: 14, gap: 10 },
  input: { flex: 1, minWidth: 0, minHeight: 50, paddingVertical: 12, color: '#242924', fontSize: 15 },
  reveal: { minHeight: 44, justifyContent: 'center', paddingLeft: 6 },
  mutedLink: { color: '#777d72', fontSize: 12 },
  primary: { alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 10, backgroundColor: '#242924', borderRadius: 13, minHeight: 52, padding: 12, marginTop: 8 },
  primaryText: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  alternative: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', marginTop: 18, gap: 4 },
  alternativeText: { fontSize: 13, color: '#92958c' },
  link: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 },
  linkText: { fontSize: 13, color: '#42483d', fontWeight: '500' },
  feedback: { borderRadius: 9, fontSize: 13, lineHeight: 20, marginBottom: 14, padding: 12 },
  errorFeedback: { backgroundColor: '#f8ede9', color: '#9a493f' },
  successFeedback: { backgroundColor: '#f0f1eb', color: '#59634f' },
  mailCard: { backgroundColor: '#f0f1eb', padding: 19, borderRadius: 14, marginBottom: 17, gap: 10 },
  mailAddress: { fontSize: 15, fontWeight: '600', color: '#242924' },
  mailNote: { fontSize: 13, lineHeight: 22, color: '#777d72' },
  minorNote: { color: '#92958c', fontSize: 11, lineHeight: 20, marginTop: 17, textAlign: 'center' },
  server: { flexDirection: 'row', alignItems: 'center', gap: 9, borderTopWidth: 1, borderTopColor: '#eeeee7', marginTop: 35, paddingTop: 12 },
  serverName: { flex: 1, color: '#92958c', fontSize: 12 },
  serverButton: { minHeight: 44, justifyContent: 'center', paddingLeft: 10 },
  footer: { color: '#92958c', fontSize: 11, letterSpacing: 2, textAlign: 'center', paddingVertical: 24 },
  disabled: { opacity: 0.6 },
});
