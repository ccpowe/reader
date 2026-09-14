import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from '../i18n';

export type TitleTranslationNoticeProps = {
  contentIds: readonly string[];
  message: string;
  onRetry: () => Promise<void> | void;
};

/**
 * Shared, accessible notice for title work whose fixed convergence window
 * expired. It is intentionally a user action: no render or query change can
 * reopen the window without this button being pressed.
 */
export function TitleTranslationNotice({ contentIds, message, onRetry }: TitleTranslationNoticeProps) {
  const { t } = useTranslation('common');
  const [busy, setBusy] = useState(false);
  if (!contentIds.length) return null;

  async function retry() {
    if (busy) return;
    setBusy(true);
    try {
      await onRetry();
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.notice}>
      <Text accessibilityLiveRegion="assertive" accessibilityRole="alert" style={styles.message}>
        {message}
      </Text>
      <Pressable
        accessibilityLabel={t('retryTitleTranslation')}
        accessibilityRole="button"
        accessibilityState={{ busy, disabled: busy }}
        disabled={busy}
        onPress={() => { void retry(); }}
        style={[styles.retry, busy && styles.retryDisabled]}
      >
        <Text style={styles.retryText}>{t(busy ? 'querying' : 'retryTranslation')}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  notice: { alignItems: 'center', backgroundColor: '#FFF4E5', borderColor: '#F0D6AD', borderRadius: 10, borderWidth: 1, flexDirection: 'row', gap: 10, marginBottom: 14, marginHorizontal: 20, paddingHorizontal: 12, paddingVertical: 10 },
  message: { color: '#7A4B00', flex: 1, fontSize: 13, lineHeight: 19 },
  retry: { borderColor: '#7A4B00', borderRadius: 7, borderWidth: 1, minHeight: 36, justifyContent: 'center', paddingHorizontal: 10 },
  retryDisabled: { opacity: 0.6 },
  retryText: { color: '#7A4B00', fontSize: 12, fontWeight: '700' },
});
