import { useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { useTranslation } from '../i18n';
import { READER_TAB_BAR_HEIGHT } from '../ui/layout';
import { colors, radii, spacing, touchTarget } from '../ui/tokens';

// Keep list headers at the top and let zero-row feedback fill only the space
// left above the overlaid navigation. Populated lists retain their tail inset.
export const listFeedbackStyles = StyleSheet.create({
  content: { flexGrow: 1, paddingBottom: READER_TAB_BAR_HEIGHT },
  state: { flexGrow: 1, justifyContent: 'center', paddingVertical: spacing.xxl },
});

export function EmptyState({
  icon,
  message,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  message: string;
}) {
  return (
    <View style={styles.empty}>
      <View style={styles.emptyIcon}>
        <MaterialCommunityIcons color="#64748b" name={icon} size={30} />
      </View>
      <Text style={styles.emptyText}>{message}</Text>
    </View>
  );
}

export function LoadingBlock() {
  return (
    <View style={styles.loading}>
      <ActivityIndicator color="#111111" />
    </View>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void | Promise<unknown>;
}) {
  const { t } = useTranslation('common');
  const [retrying, setRetrying] = useState(false);
  const retryInFlight = useRef(false);

  async function retry() {
    if (retryInFlight.current) return;
    retryInFlight.current = true;
    setRetrying(true);
    try {
      await onRetry();
    } catch {
      // The caller owns the error message; keep its error state available to retry.
    } finally {
      retryInFlight.current = false;
      setRetrying(false);
    }
  }

  return (
    <View accessibilityLiveRegion="polite" style={styles.empty}>
      <View style={[styles.emptyIcon, styles.errorIcon]}>
        <MaterialCommunityIcons color="#BA1A1A" name="cloud-alert-outline" size={30} />
      </View>
      <Text style={styles.errorTitle}>{t('loadUnavailable')}</Text>
      <Text style={styles.emptyText}>{message}</Text>
      <Pressable
        accessibilityLabel={t(retrying ? 'retrying' : 'reload')}
        accessibilityRole="button"
        accessibilityState={{ busy: retrying, disabled: retrying }}
        disabled={retrying}
        onPress={() => { void retry(); }}
        style={({ pressed }) => [styles.retryButton, pressed && styles.retryButtonPressed]}
      >
        <View style={styles.retryIcon}>
          {retrying
            ? <ActivityIndicator color={colors.textSecondary} size="small" />
            : <MaterialCommunityIcons color={colors.textSecondary} name="refresh" size={16} />}
        </View>
        <Text style={styles.retryButtonText}>{t('retry')}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: { alignItems: 'center', flex: 1, justifyContent: 'center', paddingHorizontal: 32 },
  emptyIcon: { alignItems: 'center', backgroundColor: '#EEEEEC', borderRadius: 30, height: 60, justifyContent: 'center', marginBottom: 14, width: 60 },
  emptyText: { color: '#666666', fontSize: 15, lineHeight: 23, textAlign: 'center' },
  errorIcon: { backgroundColor: '#FDECEC' },
  errorTitle: { color: '#1A1C1B', fontSize: 17, fontWeight: '700', marginBottom: 5 },
  loading: { alignItems: 'center', paddingVertical: 28 },
  retryButton: { alignItems: 'center', backgroundColor: colors.surface, borderColor: colors.border, borderRadius: radii.md, borderWidth: 1, flexDirection: 'row', gap: 6, marginTop: spacing.lg, minHeight: touchTarget, paddingHorizontal: 14, paddingVertical: spacing.sm },
  retryButtonPressed: { backgroundColor: colors.surfaceMuted },
  retryIcon: { alignItems: 'center', height: 20, justifyContent: 'center', width: 20 },
  retryButtonText: { color: colors.textSecondary, fontSize: 14, fontWeight: '600' },
});
