import { Pressable, StyleSheet } from 'react-native';
import Reanimated, {
  Easing as ReanimatedEasing,
  interpolateColor,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';
import { useEffect } from 'react';
import { colors, touchTarget } from '../ui/tokens';

export function Chip({ active = false, label, onPress }: { active?: boolean; label: string; onPress: () => void }) {
  const activation = useSharedValue(active ? 1 : 0);

  useEffect(() => {
    activation.value = withTiming(active ? 1 : 0, {
      duration: 170,
      easing: ReanimatedEasing.out(ReanimatedEasing.cubic),
    });
  }, [activation, active]);

  const bubbleStyle = useAnimatedStyle(() => ({
    backgroundColor: colors.textStrong,
    opacity: activation.value,
  }));
  const textStyle = useAnimatedStyle(() => ({
    color: interpolateColor(activation.value, [0, 1], [colors.textMuted, colors.textStrong]),
  }));

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={styles.chip}
    >
      <Reanimated.View pointerEvents="none" style={[styles.chipBackground, bubbleStyle]} />
      <Reanimated.Text numberOfLines={1} style={[styles.chipText, textStyle, { fontWeight: active ? '700' : '400' }]}>
        {label}
      </Reanimated.Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: { justifyContent: 'center', maxWidth: 160, minHeight: touchTarget, paddingHorizontal: 0, paddingBottom: 3 },
  chipBackground: { borderRadius: 2, bottom: 0, height: 3, left: 0, position: 'absolute', right: 0 },
  chipText: { color: colors.textSecondary, fontSize: 14, fontWeight: '600' },
});
