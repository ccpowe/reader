/**
 * The one sanctioned `useEffect` in src/ (react-guidelines §3): sync with an outside system once
 * when the component mounts, and undo it on unmount. Inputs that change are handled by the parent
 * re-mounting this component with a `key`, not by a dependency array.
 */
// eslint-disable-next-line no-restricted-imports -- this file is the documented exception
import { useEffect } from 'react';

export function useMountEffect(setup: () => () => void) {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately once per mount
  useEffect(setup, []);
}
