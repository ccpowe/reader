/**
 * Numbers React Native props need (sizes, durations). Colors, type and radii are Tailwind
 * classes from global.css; these mirror tokens.css where it has a value and are marked
 * "mobile" where quietpaper (desktop-measured) has none — see design/project/DESIGN.project.md.
 */
export const iconSize = {
  xs: 12, // --size-icon-xs: chevrons, spinners
  sm: 16, // --size-icon-sm: inline and row icons (quietpaper default)
  md: 18, // --size-icon-md
  toolbar: 20, // mobile: header / toolbar icon buttons
  tab: 22, // mobile: bottom tab bar
} as const;

export const iconStroke = 2.1; // --icon-stroke-width (D6)

/** --size-switch-w / -h / -knob */
export const switchSize = { width: 30, height: 18, knob: 14 } as const;

/** --size-touch-target: every tappable thing reaches this, through hitSlop if it is drawn smaller. */
export const touchTarget = 44;

export const duration = {
  instant: 20, // --duration-instant
  fast: 100, // --duration-fast
  base: 200, // --duration-base
  ambient: 700, // --duration-ambient
} as const;

export const delayLoading = 300; // --delay-loading: shorter work shows no loading state

/** Extra hit area that brings a control of `size` up to the touch target. */
export function hitSlopFor(size: number) {
  const pad = Math.max(0, Math.ceil((touchTarget - size) / 2));
  return { top: pad, bottom: pad, left: pad, right: pad };
}

/**
 * tokens.css shadows (--shadow-sm / md / lg / sheet) as React Native `boxShadow` strings, without
 * the 1px ring; useShadow() adds the ring from --color-subtle so it follows the theme.
 */
export const shadowLayers = {
  sm: '0 2px 4px 0 rgba(0, 0, 0, 0.04)',
  md: '0 8px 12px 0 rgba(25, 25, 25, 0.027), 0 2px 6px 0 rgba(25, 25, 25, 0.027)',
  lg: '0 20px 24px 0 rgba(25, 25, 25, 0.05), 0 5px 8px 0 rgba(25, 25, 25, 0.027)',
  sheet: '0 -8px 24px 0 rgba(25, 25, 25, 0.08)',
} as const;
