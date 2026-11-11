import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import { ActivityIndicator, Pressable, View } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';

import { Icon, type IconTone } from './Icon';
import { Text, type TextTone } from './Text';
import { hitSlopFor, iconSize } from './theme';

/**
 * qp-btn. Choosing (DESIGN.md 3.1): primary = the one most important action of a view (at most
 * one per view); soft = one secondary emphasis; secondary = ordinary form actions; ghost = toolbar
 * and inline actions, `on` for toggles; danger = only in a confirmation dialog.
 * Mobile: pressed replaces hover (same overlay colors); 28dp tall like qp-btn, hit area 44.
 */
export type ButtonVariant = 'primary' | 'soft' | 'secondary' | 'ghost' | 'danger';

const box: Record<ButtonVariant, string> = {
  primary: 'bg-accent active:bg-accent-pressed',
  soft: 'bg-accent-soft active:bg-accent-soft-pressed',
  secondary: 'border border-strong active:bg-pressed',
  ghost: 'active:bg-pressed',
  danger: 'bg-danger-button active:opacity-90',
};
const label: Record<ButtonVariant, TextTone> = {
  primary: 'on-accent',
  soft: 'accent-soft',
  secondary: 'primary',
  ghost: 'secondary',
  danger: 'on-accent',
};
const icon: Record<ButtonVariant, IconTone> = {
  primary: 'on-accent',
  soft: 'accent-soft-text',
  secondary: 'neutral',
  ghost: 'neutral',
  danger: 'on-accent',
};

const HEIGHT = 28; // --size-control
const HEIGHT_LARGE = 40; // mobile: full-width form buttons (sign in, add subscription)

export function Button({
  title,
  variant = 'secondary',
  icon: leading,
  on = false,
  loading = false,
  disabled = false,
  large = false,
  onPress,
  className,
}: {
  title: string;
  variant?: ButtonVariant;
  icon?: TablerIcon;
  /** ghost toggle that is on (qp-btn--ghost is-on) */
  on?: boolean;
  loading?: boolean;
  disabled?: boolean;
  large?: boolean;
  onPress?: () => void;
  className?: string;
}) {
  const spinner = useCSSVariable(
    variant === 'primary' || variant === 'danger' ? '--color-on-accent' : '--color-icon-neutral',
  ) as string | undefined;
  const height = large ? HEIGHT_LARGE : HEIGHT;
  const inactive = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading, selected: on }}
      className={cn(
        'flex-row items-center justify-center gap-2 rounded-md px-2',
        disabled && variant === 'primary' ? 'bg-sunken' : box[variant],
        on && variant === 'ghost' ? 'bg-selected' : '',
        className,
      )}
      disabled={inactive}
      hitSlop={hitSlopFor(height)}
      onPress={onPress}
      style={{ height }}
    >
      {loading ? (
        <ActivityIndicator color={spinner} size={iconSize.sm} />
      ) : leading ? (
        <Icon icon={leading} tone={disabled ? 'disabled' : on ? 'strong' : icon[variant]} />
      ) : null}
      <Text
        level="ui-strong"
        numberOfLines={1}
        tone={disabled ? 'disabled' : on && variant === 'ghost' ? 'primary' : label[variant]}
      >
        {title}
      </Text>
    </Pressable>
  );
}

/**
 * qp-icon-btn: an icon-only button; needs an accessibility label. `size` toolbar (default on
 * mobile headers) or sm (inside rows, qp-icon-btn--xs).
 */
export function IconButton({
  icon: Glyph,
  label: accessibilityLabel,
  on = false,
  disabled = false,
  size = 'toolbar',
  onPress,
}: {
  icon: TablerIcon;
  label: string;
  on?: boolean;
  disabled?: boolean;
  size?: 'toolbar' | 'sm';
  onPress?: () => void;
}) {
  const box = size === 'toolbar' ? 32 : 24;
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      accessibilityState={{ disabled, selected: on }}
      className={cn('items-center justify-center rounded-md active:bg-pressed', on ? 'bg-selected' : '')}
      disabled={disabled}
      hitSlop={hitSlopFor(box)}
      onPress={onPress}
      style={{ width: box, height: box }}
    >
      <View pointerEvents="none">
        <Icon icon={Glyph} size={size} tone={disabled ? 'disabled' : on ? 'strong' : 'neutral'} />
      </View>
    </Pressable>
  );
}
