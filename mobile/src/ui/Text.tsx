import type { ReactNode } from 'react';
import { Text as NativeText, type TextProps } from 'react-native';

import { cn } from '@/lib/cn';

/**
 * Type levels from tokens.css. chrome is `ui`, content is `body`; one line holds `ui` plus at
 * most one helper level (caption / desc / micro) — DESIGN.md 3.3.
 */
const level = {
  // mobile (P25, old app scale)
  page: 'text-page font-bold tracking-tight',
  card: 'text-card font-semibold',
  'card-plain': 'text-card',
  read: 'text-read',
  display: 'text-display font-bold',
  title: 'text-title font-bold',
  h1: 'text-h1 font-bold',
  h2: 'text-h2 font-semibold',
  h3: 'text-h3 font-semibold',
  section: 'text-section font-medium',
  body: 'text-body',
  'body-strong': 'text-body font-semibold',
  ui: 'text-ui',
  'ui-strong': 'text-ui font-medium',
  desc: 'text-desc',
  caption: 'text-caption',
  'caption-strong': 'text-caption font-medium',
  micro: 'text-micro font-medium',
} as const;

/**
 * Gray scale (D2): tertiary only for text nobody needs to read (meta, placeholders);
 * explanations, errors and empty states are secondary or darker.
 */
const tone = {
  primary: 'text-primary',
  secondary: 'text-secondary',
  tertiary: 'text-tertiary',
  disabled: 'text-disabled',
  link: 'text-link',
  'on-accent': 'text-on-accent',
  'on-danger': 'text-on-danger',
  'accent-soft': 'text-accent-soft-text',
  success: 'text-success-text',
  warning: 'text-warning-text',
  danger: 'text-danger-text',
} as const;

export type TextLevel = keyof typeof level;
export type TextTone = keyof typeof tone;

export function Text({
  level: textLevel = 'ui',
  tone: textTone = 'primary',
  className,
  children,
  ...props
}: TextProps & { level?: TextLevel; tone?: TextTone; className?: string; children?: ReactNode }) {
  return (
    <NativeText className={cn(level[textLevel], tone[textTone], className)} {...props}>
      {children}
    </NativeText>
  );
}
