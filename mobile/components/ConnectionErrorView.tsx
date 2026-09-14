import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from '../i18n';

export function ConnectionErrorView({
  message,
  onRetry,
  onChangeServer,
}: {
  message: string;
  onRetry?: () => void;
  onChangeServer?: () => void;
}) {
  const { t } = useTranslation('auth');
  return (
    <View accessibilityLiveRegion="polite" style={styles.container}>
      <Text style={styles.title}>{t('connectionFailed')}</Text>
      <Text style={styles.message}>{message}</Text>
      <View style={styles.actions}>
        {onRetry ? <Pressable accessibilityRole="button" onPress={onRetry} style={styles.primary}><Text style={styles.primaryText}>{t('retry')}</Text></Pressable> : null}
        {onChangeServer ? <Pressable accessibilityRole="button" onPress={onChangeServer} style={styles.secondary}><Text style={styles.secondaryText}>{t('changeServer')}</Text></Pressable> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', backgroundColor: '#fcfbf8', flex: 1, justifyContent: 'center', padding: 28 },
  title: { color: '#242924', fontSize: 20, fontWeight: '700', marginBottom: 10, textAlign: 'center' },
  message: { color: '#777d72', fontSize: 14, lineHeight: 22, maxWidth: 420, textAlign: 'center' },
  actions: { flexDirection: 'row', gap: 8, marginTop: 20 },
  primary: { backgroundColor: '#242924', borderRadius: 13, paddingHorizontal: 16, paddingVertical: 11 },
  primaryText: { color: '#FFFFFF', fontWeight: '700' },
  secondary: { borderRadius: 13, paddingHorizontal: 16, paddingVertical: 11 },
  secondaryText: { color: '#444444', fontWeight: '600' },
});
