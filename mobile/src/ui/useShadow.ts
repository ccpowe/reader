import { useCSSVariable } from 'uniwind';

import { shadowLayers } from './theme';

/** A quietpaper shadow level as a style object (layers + the --shadow-ring hairline). */
export function useShadow(level: keyof typeof shadowLayers) {
  const ring = useCSSVariable('--color-subtle') as string | undefined;
  return { boxShadow: ring ? `${shadowLayers[level]}, 0 0 0 1px ${ring}` : shadowLayers[level] };
}
