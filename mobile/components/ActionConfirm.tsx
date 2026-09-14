import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { redactConnectionText } from '../lib/connection/errors';
import { useTranslation } from '../i18n';

export type ActionConfirmProps = {
  title: string;
  message: string;
  note?: string;
  confirmLabel: string;
  cancelLabel?: string;
  onCancel: () => void;
  onConfirm: () => Promise<void> | void;
  /** Prevent late mutation errors/state from an obsolete runtime. */
  isCurrent?: () => boolean;
};

/**
 * Cross-platform confirmation card for actions that may perform async work.
 * Unlike a native Alert, this remains operable on Web and keeps failures in
 * the mounted surface so assistive technology can announce them.
 */
export function ActionConfirm({
  cancelLabel,
  confirmLabel,
  isCurrent,
  message,
  note,
  onCancel,
  onConfirm,
  title,
}: ActionConfirmProps) {
  const { t } = useTranslation('common');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | true | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  function current(): boolean {
    return !isCurrent || isCurrent();
  }

  async function confirm() {
    if (busy || !current()) return;
    setError(null);
    setBusy(true);
    try {
      await onConfirm();
    } catch (cause) {
      if (mountedRef.current && current()) {
        setError(cause instanceof Error ? cause : true);
      }
    } finally {
      if (mountedRef.current && current()) setBusy(false);
    }
  }

  return (
    <View accessibilityViewIsModal style={styles.card}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.message}>{message}</Text>
      {note ? <Text style={styles.note}>{note}</Text> : null}
      {error ? (
        <Text accessibilityLiveRegion="assertive" accessibilityRole="alert" style={styles.error}>
          {error === true ? t('actionFailed') : redactConnectionText(error.message)}
        </Text>
      ) : null}
      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: busy }}
          disabled={busy}
          onPress={onCancel}
          style={styles.cancel}
        >
          <Text style={styles.cancelText}>{cancelLabel ?? t('cancel')}</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ busy, disabled: busy }}
          disabled={busy}
          onPress={() => void confirm()}
          style={[styles.confirm, busy && styles.disabled]}
        >
          {busy ? <ActivityIndicator color="#FFFFFF" size="small" /> : null}
          <Text style={styles.confirmText}>{busy ? t('processing') : confirmLabel}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#FFFFFF', borderColor: '#E5E5E3', borderRadius: 16, borderWidth: 1, padding: 20, width: '100%' },
  title: { color: '#111111', fontSize: 22, fontWeight: '700', marginBottom: 8 },
  message: { color: '#444444', fontSize: 15, lineHeight: 23 },
  note: { color: '#777777', fontSize: 12, lineHeight: 18, marginTop: 12 },
  error: { backgroundColor: '#FEF2F2', borderRadius: 8, color: '#B42318', fontSize: 14, lineHeight: 20, marginTop: 14, paddingHorizontal: 12, paddingVertical: 10 },
  actions: { flexDirection: 'row', gap: 8, justifyContent: 'flex-end', marginTop: 20 },
  cancel: { borderRadius: 8, minHeight: 42, paddingHorizontal: 14, paddingVertical: 11 },
  cancelText: { color: '#444444', fontWeight: '600' },
  confirm: { alignItems: 'center', backgroundColor: '#111111', borderRadius: 8, flexDirection: 'row', gap: 8, justifyContent: 'center', minHeight: 42, paddingHorizontal: 14, paddingVertical: 11 },
  confirmText: { color: '#FFFFFF', fontWeight: '700' },
  disabled: { opacity: 0.6 },
});
