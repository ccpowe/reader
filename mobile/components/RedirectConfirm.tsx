import { Pressable, StyleSheet, Text, View } from 'react-native';

import { redirectConfirmationFor } from '../lib/connection/discovery';
import type { ReaderDiscoveryResult } from '../lib/connection/types';
import { useTranslation } from '../i18n';

export function RedirectConfirm({
  discovery,
  onCancel,
  onConfirm,
}: {
  discovery: ReaderDiscoveryResult;
  onCancel: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  const { t } = useTranslation('auth');
  const redirect = redirectConfirmationFor(discovery);
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{t('redirectTitle')}</Text>
      <Text style={styles.body}>{t('redirectDescription')}</Text>
      {redirect ? <View style={styles.urls}>
        <Text selectable style={styles.label}>{t('redirectFrom')}</Text>
        <Text selectable style={styles.url}>{redirect.from_url}</Text>
        <Text selectable style={styles.label}>{t('redirectTo')}</Text>
        <Text selectable style={styles.url}>{redirect.to_url}</Text>
      </View> : null}
      <Text style={styles.note}>{t('redirectNote')}</Text>
      <View style={styles.actions}>
        <Pressable accessibilityRole="button" onPress={onCancel} style={styles.cancel}><Text style={styles.cancelText}>{t('cancel')}</Text></Pressable>
        <Pressable accessibilityRole="button" onPress={() => void onConfirm()} style={styles.confirm}><Text style={styles.confirmText}>{t('trustAndConnect')}</Text></Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#FFFFFF', borderColor: '#e4e5dd', borderRadius: 20, borderWidth: 1, padding: 20, width: '100%' },
  title: { color: '#242924', fontSize: 22, fontWeight: '700', marginBottom: 8 },
  body: { color: '#444444', fontSize: 15, lineHeight: 23 },
  urls: { backgroundColor: '#f0f1eb', borderRadius: 10, marginTop: 16, padding: 12 },
  label: { color: '#92958c', fontSize: 12, marginTop: 4 },
  url: { color: '#222222', fontSize: 13, marginBottom: 4 },
  note: { color: '#92958c', fontSize: 12, lineHeight: 18, marginTop: 12 },
  actions: { flexDirection: 'row', gap: 8, justifyContent: 'flex-end', marginTop: 20 },
  cancel: { borderRadius: 13, minHeight: 42, paddingHorizontal: 14, paddingVertical: 11 },
  cancelText: { color: '#444444', fontWeight: '600' },
  confirm: { backgroundColor: '#242924', borderRadius: 13, minHeight: 42, paddingHorizontal: 14, paddingVertical: 11 },
  confirmText: { color: '#FFFFFF', fontWeight: '700' },
});
