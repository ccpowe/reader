import type { ReactNode } from 'react';
import { Modal, Pressable, View } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { IconX } from '@tabler/icons-react-native';

import { IconButton } from './Button';
import { Text } from './Text';
import { useShadow } from './useShadow';

/**
 * qp-sheet: mobile bottom sheet (raised surface, top corners --radius-2xl, handle). quietpaper has
 * no captured motion for it; it fades in (qp-fade), as the style pack's CSS does.
 */
export function Sheet({
  visible,
  title,
  subtitle,
  onClose,
  children,
}: {
  visible: boolean;
  title?: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <Modal animationType="fade" onRequestClose={onClose} transparent visible={visible}>
      {/* A field in the sheet stays above the keyboard: the sheet rises with it. */}
      <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
        <Pressable accessibilityLabel="关闭" className="flex-1 bg-overlay" onPress={onClose} />
        <SheetBody bottomInset={insets.bottom} onClose={onClose} subtitle={subtitle} title={title}>
          {children}
        </SheetBody>
      </KeyboardAvoidingView>
    </Modal>
  );
}

/** The sheet panel alone, for previews and stories. */
export function SheetBody({
  title,
  subtitle,
  onClose,
  children,
  bottomInset = 0,
}: {
  title?: string;
  subtitle?: string;
  onClose?: () => void;
  children: ReactNode;
  bottomInset?: number;
}) {
  const shadow = useShadow('sheet');
  return (
    <View className="rounded-t-3xl bg-raised px-4 pt-2" style={[shadow, { paddingBottom: 16 + bottomInset }]} testID="sheet">
      <View className="mb-3 h-1 w-9 self-center rounded-full bg-control" />
      {title ? (
        <View className="mb-4 flex-row items-start gap-2">
          <View className="min-w-0 flex-1">
            <Text level="h2">{title}</Text>
            {subtitle ? (
              <Text className="mt-1" level="desc" tone="secondary">
                {subtitle}
              </Text>
            ) : null}
          </View>
          {onClose ? <IconButton icon={IconX} label="关闭" onPress={onClose} /> : null}
        </View>
      ) : null}
      {children}
    </View>
  );
}
