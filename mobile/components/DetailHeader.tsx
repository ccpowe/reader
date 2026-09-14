import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { colors } from '../ui/tokens';
import { useTranslation } from '../i18n';

export function DetailHeader({ title, onBack, onSave, saved, saveBusy = false, onShare, onExternalOpen }: {
  title: string;
  onBack: () => void;
  onSave?: () => void;
  saved: boolean;
  saveBusy?: boolean;
  onShare: () => void;
  onExternalOpen: () => void;
}) {
  const { t } = useTranslation('common');
  return (
    <View style={styles.header}>
      <HeaderAction icon="arrow-left" label={t('back')} onPress={onBack} />
      <Text numberOfLines={1} style={styles.title}>{title}</Text>
      {onSave ? <HeaderAction busy={saveBusy} icon={saved ? 'bookmark' : 'bookmark-outline'} label={t(saved ? 'unsave' : 'save')} onPress={onSave} selected={saved} /> : null}
      <HeaderAction icon="share-variant-outline" label={t('share')} onPress={onShare} />
      <HeaderAction icon="open-in-new" label={t('openExternal')} onPress={onExternalOpen} />
    </View>
  );
}

function HeaderAction({ icon, label, onPress, selected, busy = false }: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  onPress: () => void;
  selected?: boolean;
  busy?: boolean;
}) {
  return (
    <Pressable accessibilityLabel={label} accessibilityRole="button" accessibilityState={{ selected, busy, disabled: busy }} disabled={busy} onPress={onPress} style={({ pressed }) => [styles.button, pressed && styles.pressed]}>
      {busy ? <ActivityIndicator color={colors.textPrimary} size="small" /> : <MaterialCommunityIcons color={colors.textPrimary} name={icon} size={22} />}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  header: { alignItems: 'center', backgroundColor: colors.background, flexDirection: 'row', height: 56, paddingHorizontal: 8 },
  title: { color: colors.textPrimary, flex: 1, fontSize: 16, fontWeight: '600', marginHorizontal: 8 },
  button: { alignItems: 'center', borderRadius: 24, height: 48, justifyContent: 'center', width: 48 },
  pressed: { backgroundColor: colors.surfaceSubtle },
});
