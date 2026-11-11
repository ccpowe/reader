import { IconSearch } from '@tabler/icons-react-native';
import { TextInput, View, type TextInputProps } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';

import { Icon } from './Icon';
import { Text } from './Text';

/** qp-input. Touch: 16px text (--font-input-size-touch) and 40dp tall; error = danger ring. */
export function Input({
  label,
  hint,
  error,
  className,
  ...props
}: TextInputProps & { label?: string; hint?: string; error?: string | null; className?: string }) {
  const placeholder = useCSSVariable('--color-tertiary') as string | undefined;
  return (
    <View className={cn('gap-1', className)}>
      {label ? <Text level="ui-strong">{label}</Text> : null}
      <TextInput
        className={cn(
          'h-11 rounded-lg border bg-tint px-3 text-body text-primary',
          error ? 'border-danger' : 'border-strong focus:border-accent',
        )}
        placeholderTextColor={placeholder}
        {...props}
      />
      {error ? (
        <Text level="desc" tone="danger">
          {error}
        </Text>
      ) : hint ? (
        <Text level="desc" tone="secondary">
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

/** qp-search at the phone scale (P25): 44 high, 24 from the screen edge (16 inside a sheet). */
export function SearchField({ inset = 'page', ...props }: TextInputProps & { inset?: 'page' | 'sheet' }) {
  const placeholder = useCSSVariable('--color-tertiary') as string | undefined;
  return (
    <View className={cn('h-11 flex-row items-center gap-2 rounded-xl border border-subtle bg-tint px-3', inset === 'page' ? 'mx-6' : 'mx-4')}>
      <Icon icon={IconSearch} size="toolbar" />
      <TextInput className="h-full flex-1 text-body text-primary" placeholderTextColor={placeholder} {...props} />
    </View>
  );
}
