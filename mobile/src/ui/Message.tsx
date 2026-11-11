import { IconAlertCircle, IconAlertTriangle, IconCircleCheck, IconInfoCircle } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { View } from 'react-native';

import { cn } from '@/lib/cn';

import { Icon } from './Icon';
import { Text } from './Text';

type Kind = 'info' | 'success' | 'warning' | 'danger';

const box: Record<Kind, string> = {
  info: 'bg-tint',
  success: 'bg-success-bg',
  warning: 'bg-warning-bg',
  danger: 'bg-danger-bg',
};
const glyph = { info: IconInfoCircle, success: IconCircleCheck, warning: IconAlertTriangle, danger: IconAlertCircle };
const tone = { info: 'neutral', success: 'success', warning: 'warning', danger: 'error' } as const;
const text = { info: 'secondary', success: 'success', warning: 'warning', danger: 'danger' } as const;

/**
 * qp-msg: validation, failure or status in place. `bar` = qp-msg--bar, one view-level banner
 * (disconnected, signed out, stale data) at the top edge; one per view, the most severe.
 */
export function Message({
  kind = 'info',
  bar = false,
  children,
  action,
}: {
  kind?: Kind;
  bar?: boolean;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <View
      className={cn(
        'flex-row gap-2',
        box[kind],
        bar ? 'items-center px-4 py-1.5' : 'items-start rounded-md px-2 py-1.5',
      )}
    >
      <View className={bar ? '' : 'mt-0.5'}>
        <Icon icon={glyph[kind]} tone={kind === 'info' ? 'neutral' : tone[kind]} />
      </View>
      <Text className="flex-1" tone={text[kind]}>
        {children}
      </Text>
      {action}
    </View>
  );
}

/** qp-callout: emphasised group of content (no border, no shadow). */
export function Callout({ children }: { children: ReactNode }) {
  return <View className="rounded-xl bg-tint p-3">{children}</View>;
}
