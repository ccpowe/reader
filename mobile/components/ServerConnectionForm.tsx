import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import type { ConnectionError } from '../lib/connection';
import { AuthLayout, authStyles as styles } from './AuthLayout';
import { useTranslation } from '../i18n';

export function ServerConnectionForm({ busy = false, error, initialValue = '', onSubmit, onCancel }: {
  busy?: boolean; error?: ConnectionError | Error | null; initialValue?: string;
  onSubmit: (value: string, serverToken: string) => Promise<void> | void; onCancel?: () => void;
}) {
  const { t } = useTranslation('auth');
  const [value, setValue] = useState(initialValue);
  const [serverToken, setServerToken] = useState('');
  return (
    <AuthLayout eyebrow={t('connectionEyebrow')} title={t('connectionTitle')} subtitle={t('connectionSubtitle')} onBack={onCancel}>
      <View style={styles.form}>
        <View style={styles.field}>
          <View style={styles.labelRow}><Text style={styles.label}>{t('serverAddress')}</Text></View>
          <View style={styles.inputWrap}>
            <Feather color="#a1a697" name="globe" size={18} />
            <TextInput accessibilityLabel={t('serverAddressAccessibility')} autoCapitalize="none" autoCorrect={false} editable={!busy} keyboardType="url" onChangeText={setValue} onSubmitEditing={() => { if (!busy) void onSubmit(value, serverToken); }} placeholder="https://reader.example.com" placeholderTextColor="#a1a697" returnKeyType="go" style={styles.input} value={value} />
          </View>
          <Text style={[styles.minorNote, { textAlign: 'left', marginTop: 9 }]}>{t('serverAddressHint')}</Text>
        </View>
        <View style={styles.field}>
          <View style={styles.labelRow}><Text style={styles.label}>{t('serverToken')}</Text></View>
          <View style={styles.inputWrap}>
            <Feather color="#a1a697" name="lock" size={18} />
            <TextInput accessibilityLabel={t('serverToken')} autoCapitalize="none" autoCorrect={false} autoComplete="off" editable={!busy} onChangeText={setServerToken} onSubmitEditing={() => { if (!busy) void onSubmit(value, serverToken); }} placeholder={t('serverTokenPlaceholder')} placeholderTextColor="#a1a697" returnKeyType="go" secureTextEntry style={styles.input} value={serverToken} />
          </View>
        </View>
        {error ? <Text accessibilityRole="alert" accessibilityLiveRegion="polite" style={[styles.feedback, styles.errorFeedback]}>{error.message}</Text> : null}
        <Pressable accessibilityRole="button" accessibilityState={{ busy, disabled: busy }} disabled={busy} onPress={() => void onSubmit(value, serverToken)} style={[styles.primary, busy && styles.disabled]}>
          {busy ? <ActivityIndicator color="#FFFFFF" size="small" /> : null}<Text style={styles.primaryText}>{busy ? t('connecting') : t('connectServer')}</Text>{!busy ? <Feather color="#ffffff" name="chevron-right" size={18} /> : null}
        </Pressable>
        {onCancel ? <View style={styles.alternative}><Pressable accessibilityRole="button" onPress={onCancel} style={styles.link}><Text style={styles.linkText}>{t('returnToCurrentServer')}</Text></Pressable></View> : null}
      </View>
    </AuthLayout>
  );
}
