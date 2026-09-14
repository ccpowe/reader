import { Pressable, StyleSheet, Text, View, type StyleProp, type TextStyle } from 'react-native';
import { useTranslation } from '../i18n';

export type RankingTitleProps = {
  title: string;
  translatedTitle?: string | null;
  status?: string | null;
  timedOut?: boolean;
  retrying?: boolean;
  onRetry?: () => void;
  numberOfLines?: number;
  titleStyle?: StyleProp<TextStyle>;
};

/** Title presentation used only by rankings surfaces. Feed cards stay unchanged. */
export function RankingTitle({
  numberOfLines,
  onRetry,
  retrying = false,
  status,
  timedOut = false,
  title,
  titleStyle,
  translatedTitle,
}: RankingTitleProps) {
  const { t } = useTranslation('common');
  const failed = timedOut || status === 'failed';
  const display = failed ? title : translatedTitle?.trim() || title;
  return (
    <View style={styles.container}>
      <Text accessibilityLiveRegion="polite" numberOfLines={numberOfLines} style={titleStyle}>
        {display}
      </Text>
      {failed ? (
        <View accessibilityLiveRegion="polite" style={styles.failureRow}>
          <Text style={styles.failureText}>{t(timedOut ? 'translationTimedOut' : 'translationFailed')}</Text>
          {onRetry ? (
            <Pressable
              accessibilityLabel={t('retryTitleTranslation')}
              accessibilityRole="button"
              accessibilityState={{ busy: retrying, disabled: retrying }}
              disabled={retrying}
              onPress={(event) => {
                event?.stopPropagation?.();
                onRetry();
              }}
              style={styles.retry}
            >
              <Text style={styles.retryText}>{t(retrying ? 'retrying' : 'retryTranslation')}</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { minWidth: 0 },
  failureRow: { alignItems: 'center', flexDirection: 'row', gap: 8, marginTop: 6 },
  failureText: { color: '#B42318', fontSize: 12, fontWeight: '600' },
  retry: { borderColor: '#B42318', borderRadius: 6, borderWidth: 1, minHeight: 30, paddingHorizontal: 9, paddingVertical: 5 },
  retryText: { color: '#8A1C13', fontSize: 12, fontWeight: '700' },
});
