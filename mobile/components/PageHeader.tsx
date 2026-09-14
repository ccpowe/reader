import { ActivityIndicator, Image, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import type { ReactNode } from 'react';
import type { Session } from '../lib/readerAuth';
import { useQuery } from '@tanstack/react-query';
import { getProfile } from '../lib/api';
import { readerQueryKeys } from '../state/queryClient';
import { useReaderRuntime } from '../lib/connection/react';
import { colors, radii, spacing, touchTarget } from '../ui/tokens';
import { PAGE_HEADER_HEIGHT } from '../ui/layout';
import { useTranslation } from '../i18n';

export function ReaderLogo() {
  return (
    <View style={styles.logo}>
      <Image
        resizeMode="contain"
        source={require('../assets/reader-logo.png')}
        style={styles.logoImage}
      />
    </View>
  );
}

export function PageHeaderContent({ right, headingRight, title, session, onProfile }: {
  compact?: boolean; right?: ReactNode; headingRight?: ReactNode; title: string;
  session?: Session; onProfile?: () => void;
}) {
  return (
    <View style={styles.pageHeader}>
      <View style={styles.brandRow}>
        <View style={styles.brand}><ReaderLogo /><Text style={styles.wordmark}>READER</Text></View>
        {right ?? (session ? <ProfileAvatar session={session} onPress={onProfile} /> : null)}
      </View>
      <View style={styles.heading}>
        <Text
          accessibilityLabel={title}
          adjustsFontSizeToFit
          ellipsizeMode="tail"
          minimumFontScale={0.8}
          numberOfLines={1}
          style={styles.title}
        >
          {title}
        </Text>
        {headingRight != null ? <View style={styles.headingRight}>{headingRight}</View> : null}
      </View>
    </View>
  );
}

function ProfileAvatar({ session, onPress }: { session: Session; onPress?: () => void }) {
  const { t } = useTranslation('common');
  const runtime = useReaderRuntime();
  const profile = useQuery({ queryKey: readerQueryKeys.profile(session.user.id, runtime?.identity.server_id), queryFn: () => getProfile(session, runtime ?? undefined), enabled: Boolean(runtime), staleTime: 300_000 });
  const uri = profile.data?.avatar_url;
  const name = profile.data?.display_name || session.user.email || 'R';
  return <Pressable accessibilityLabel={t('profile')} accessibilityRole="button" onPress={onPress} hitSlop={4} style={styles.avatar}>{typeof uri === 'string' && uri ? <Image source={{ uri }} style={styles.avatarImage} /> : <Text style={styles.avatarText}>{name.slice(0, 1).toUpperCase()}</Text>}</Pressable>;
}

export function IconButton({
  busy = false,
  disabled = false,
  icon,
  label,
  onPress,
  quiet = false,
}: {
  busy?: boolean;
  disabled?: boolean;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  onPress: () => void;
  quiet?: boolean;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ busy, disabled: disabled || busy }}
      disabled={disabled || busy}
      hitSlop={4}
      onPress={onPress}
      style={({ pressed }) => [
        quiet ? styles.iconButtonQuiet : styles.iconButton,
        (disabled || busy) && styles.iconButtonDisabled,
        pressed && !(disabled || busy) && styles.iconButtonPressed,
      ]}
    >
      {busy ? (
        quiet ? (
          <View style={styles.iconButtonQuietVisual}>
            <ActivityIndicator color={colors.textSecondary} size="small" />
          </View>
        ) : (
          <ActivityIndicator color={colors.textSecondary} size="small" />
        )
      ) : quiet ? (
        <View style={styles.iconButtonQuietVisual}>
          <MaterialCommunityIcons color={colors.textSecondary} name={icon} size={20} />
        </View>
      ) : (
        <MaterialCommunityIcons color={colors.textSecondary} name={icon} size={22} />
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pageHeader: { height: PAGE_HEADER_HEIGHT, paddingTop: 8, paddingBottom: 8 },
  brandRow: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', height: 36 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, height: 44, justifyContent: 'space-between', marginTop: 8 },
  headingRight: { alignItems: 'flex-end', flexShrink: 0, maxWidth: '50%', minWidth: 0 },
  avatar: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: '#dedfd6', backgroundColor: '#eeefe7', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
  avatarImage: { width: 36, height: 36 },
  avatarText: { fontSize: 17, color: colors.textPrimary },
  wordmark: { color: colors.textStrong, fontSize: 12, fontWeight: '700', letterSpacing: 2 },
  brand: { alignItems: 'center', flexDirection: 'row', gap: 8 },
  logo: { backgroundColor: colors.background, height: 32, width: 32 },
  logoImage: { height: 32, width: 32 },
  // Native auto-fit needs font metrics: a scaled explicit line height would still overflow the row.
  title: { color: colors.textPrimary, flexShrink: 1, fontSize: 34, lineHeight: Platform.OS === 'web' ? 44 : undefined, fontWeight: '700', letterSpacing: -1.2, maxHeight: 44, minWidth: 0 },
  iconButton: {
    alignItems: 'center',
    backgroundColor: colors.background,
    borderRadius: radii.pill,
    height: touchTarget,
    justifyContent: 'center',
    width: touchTarget,
  },
  iconButtonDisabled: { opacity: 0.55 },
  iconButtonPressed: { opacity: 0.72 },
  iconButtonQuiet: { alignItems: 'center', height: touchTarget, justifyContent: 'center', width: touchTarget },
  iconButtonQuietVisual: {
    alignItems: 'center',
    backgroundColor: colors.background,
    borderRadius: 16,
    height: 32,
    justifyContent: 'center',
    width: 32,
  },
});
