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
