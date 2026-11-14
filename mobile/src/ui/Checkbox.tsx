import { IconCheck } from '@tabler/icons-react-native';
import { Pressable } from 'react-native';

import { cn } from '@/lib/cn';

import { Icon } from './Icon';
import { hitSlopFor } from './theme';

const SIZE = 20; // mobile: quietpaper's 14 checkbox at the phone scale (old app ~20)

/** qp-checkbox: several choices in a list. Checked = accent fill with a check. */
export function Checkbox({ checked, label, onChange }: { checked: boolean; label: string; onChange?: (next: boolean) => void }) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      className={cn('items-center justify-center rounded-sm', checked ? 'bg-accent' : 'border-2 border-control')}
      hitSlop={hitSlopFor(SIZE)}
      onPress={() => onChange?.(!checked)}
      style={{ width: SIZE, height: SIZE }}
    >
      {checked ? <Icon icon={IconCheck} size="sm" tone="on-accent" /> : null}
    </Pressable>
  );
}
