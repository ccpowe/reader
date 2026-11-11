import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Our type levels (global.css @theme --text-*) must be registered as font sizes; otherwise the
// merger reads `text-h2` as a color and drops it when a text color such as `text-primary` follows.
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['page', 'card', 'read', 'display', 'title', 'h1', 'h2', 'h3', 'section', 'body', 'ui', 'desc', 'caption', 'micro'] }],
    },
  },
});

/** Join class names; later Tailwind classes win over earlier conflicting ones. */
export function cn(...inputs: ClassValue[]) {
  return merge(clsx(inputs));
}
