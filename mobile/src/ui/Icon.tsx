import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import { useCSSVariable } from 'uniwind';

import { iconSize, iconStroke } from './theme';

export type IconTone =
  | 'neutral'
  | 'strong'
  | 'disabled'
  | 'danger'
  | 'on-accent'
  | 'on-danger'
  | 'accent-soft-text'
  | 'success'
  | 'warning'
  | 'error';

const toneVariable: Record<IconTone, string> = {
  neutral: '--color-icon-neutral',
  strong: '--color-icon-strong',
  disabled: '--color-icon-disabled',
  danger: '--color-icon-danger',
  'on-accent': '--color-on-accent',
  'on-danger': '--color-on-danger',
  'accent-soft-text': '--color-accent-soft-text',
  success: '--color-success',
  warning: '--color-warning',
  // Error feedback uses the status color; `danger` is only for delete icons (DESIGN.md 3.2).
  error: '--color-danger',
};

/** A Tabler outline icon (D6): gray by default, never colored by topic or type (D9, D10). */
export function Icon({
  icon: Glyph,
  tone = 'neutral',
  size = 'sm',
}: {
  icon: TablerIcon;
  tone?: IconTone;
  size?: keyof typeof iconSize;
}) {
  const color = useCSSVariable(toneVariable[tone]) as string | undefined;
  return <Glyph color={color} size={iconSize[size]} strokeWidth={iconStroke} />;
}
