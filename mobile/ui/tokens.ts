export const colors = {
  background: '#FCFBF8',
  surface: '#FFFFFF',
  surfaceMuted: '#F3F3F3',
  surfaceSubtle: '#EEEEEE',
  imagePlaceholder: '#E8E8E6',
  border: '#E7E7E7',
  borderStrong: '#C9C9C6',
  textPrimary: '#242924',
  textStrong: '#242424',
  textSecondary: '#687062',
  textTertiary: '#666666',
  textMuted: '#858B7F',
  accent: '#242424',
  danger: '#BA1A1A',
  warningSurface: '#FFF4E5',
  warningText: '#7A4B00',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radii = {
  sm: 8,
  md: 12,
  lg: 16,
  sheet: 22,
  pill: 999,
} as const;

/** 44pt meets iOS guidance; Android callers should use at least 48dp where possible. */
export const touchTarget = 44;
