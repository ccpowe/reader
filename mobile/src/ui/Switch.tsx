import { Pressable, View } from 'react-native';

import { cn } from '@/lib/cn';

import { hitSlopFor, switchSize } from './theme';

/** qp-switch: immediate binary setting, no Save next to it. Accent when on. */
export function Switch({ value, onChange, label }: { value: boolean; onChange?: (next: boolean) => void; label: string }) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      className={cn('justify-center rounded-full px-0.5', value ? 'bg-accent' : 'bg-control')}
      hitSlop={hitSlopFor(switchSize.height)}
      onPress={() => onChange?.(!value)}
      style={{ width: switchSize.width, height: switchSize.height }}
    >
      <View
        className={cn('rounded-full bg-canvas', value ? 'self-end' : 'self-start')}
        style={{ width: switchSize.knob, height: switchSize.knob }}
      />
    </Pressable>
  );
}
