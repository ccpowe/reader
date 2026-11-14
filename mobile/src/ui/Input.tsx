import { IconSearch, IconX } from '@tabler/icons-react-native';
import { useState, type ComponentRef, type ReactNode, type Ref } from 'react';
import { Pressable, TextInput, View, type TextInputProps } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';
import { t } from '@/lib/i18n';

import { Icon } from './Icon';
import { Text } from './Text';

/**
 * qp-input. Touch: 16px text (--font-input-size-touch) and 40dp tall; error = danger ring.
 * `trailing`: a control inside the field on the right (show password).
 */
export function Input({
  label,
  hint,
  error,
  className,
  trailing,
  ref,
  onFocus,
  onBlur,
  ...props
}: TextInputProps & {
  label?: string;
  hint?: string;
  error?: string | null;
  className?: string;
  trailing?: ReactNode;
  ref?: Ref<ComponentRef<typeof TextInput>>;
}) {
  const placeholder = useCSSVariable('--color-tertiary') as string | undefined;
  const [focused, setFocused] = useState(false);
  return (
    <View className={cn('gap-1', className)}>
      {label ? <Text level="ui-strong">{label}</Text> : null}
      <View
        className={cn(
          'h-11 flex-row items-center rounded-lg border bg-tint',
          error ? 'border-danger' : focused ? 'border-accent' : 'border-strong',
        )}
      >
        <TextInput
          className="h-full flex-1 px-3 text-body text-primary"
          placeholderTextColor={placeholder}
          // The visible label is a sibling Text; screen readers get it, and the error or hint, here.
          accessibilityHint={error ?? hint}
          accessibilityLabel={label}
          onBlur={(event) => {
            setFocused(false);
            onBlur?.(event);
          }}
          onFocus={(event) => {
            setFocused(true);
            onFocus?.(event);
          }}
          ref={ref}
          {...props}
        />
        {trailing}
      </View>
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

/**
 * qp-search at the phone scale (P25): 44 high, 24 from the screen edge (16 inside a sheet). With
 * text in it, a clear button empties it in one tap (old app).
 */
export function SearchField({ inset = 'page', ...props }: TextInputProps & { inset?: 'page' | 'sheet' }) {
  const placeholder = useCSSVariable('--color-tertiary') as string | undefined;
  const { value, onChangeText } = props;
  return (
    <View className={cn('h-11 flex-row items-center gap-2 rounded-xl border border-subtle bg-tint px-3', inset === 'page' ? 'mx-6' : 'mx-4')} testID="align-inset">
      <Icon icon={IconSearch} size="toolbar" />
      <TextInput className="h-full flex-1 text-body text-primary" placeholderTextColor={placeholder} {...props} />
      {value && onChangeText ? (
        <Pressable
          accessibilityLabel={t().common.clearSearch}
          accessibilityRole="button"
          className="-mr-2 h-11 w-11 items-center justify-center active:opacity-60"
          onPress={() => onChangeText('')}
        >
          <Icon icon={IconX} size="sm" />
        </Pressable>
      ) : null}
    </View>
  );
}
